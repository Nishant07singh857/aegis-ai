# backend/tests/unit/test_cluster.py
"""Unit tests for the Phase 7 / W3 distributed node registry + cluster API.

Covers:
  * Role parsing (NODE_ROLES): valid roles kept, unknown dropped, empty -> default
  * Node identity: uuid4 hex per process by default, explicit override honored
  * Status thresholds: healthy <30s, degraded 30-45s, stale >45s
  * Registry register / heartbeat / deregister against an in-memory async
    Redis stand-in (no fakeredis installed, no live Redis on this VM)
  * agent_count published on heartbeat via the injected provider
  * Standalone fallback when Redis is unreachable: honest single-node entry,
    never invented peers; mid-run Redis failure also degrades cleanly
  * GET /api/v1/cluster/nodes: 200 with nodes for any authenticated user
    (viewer included), 401 unauthenticated

No network, no real Redis, no real credentials.
"""

from __future__ import annotations

import asyncio
import fnmatch
import time
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.cluster.registry import (
    DEFAULT_ROLES,
    NodeRegistry,
    build_node_entry,
    node_status,
    parse_roles,
)
from app.core.security import TokenPayload


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# In-memory async Redis stand-in
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class _FakePipeline:
    def __init__(self, client: "_FakeAsyncRedis") -> None:
        self._client = client
        self._ops: List[tuple] = []

    def hset(self, key: str, mapping: Optional[Dict[str, str]] = None, **_kw: Any) -> "_FakePipeline":
        self._ops.append(("hset", key, mapping or {}))
        return self

    def expire(self, key: str, ttl: int) -> "_FakePipeline":
        self._ops.append(("expire", key, ttl))
        return self

    async def execute(self) -> List[Any]:
        for op, key, value in self._ops:
            if op == "hset":
                await self._client.hset(key, mapping=value)
            else:
                await self._client.expire(key, value)
        return [True] * len(self._ops)


class _FakeAsyncRedis:
    """Minimal async Redis stand-in: hashes + TTL tracking + scan."""

    def __init__(self, *, fail_ping: bool = False) -> None:
        self.data: Dict[str, Dict[str, str]] = {}
        self.ttls: Dict[str, int] = {}
        self.deleted: List[str] = []
        self.fail_ping = fail_ping

    async def ping(self) -> bool:
        if self.fail_ping:
            from redis.exceptions import ConnectionError

            raise ConnectionError("simulated: redis unreachable")
        return True

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    async def hset(self, key: str, mapping: Optional[Dict[str, str]] = None, **_kw: Any) -> int:
        bucket = self.data.setdefault(key, {})
        bucket.update({str(k): str(v) for k, v in (mapping or {}).items()})
        return len(mapping or {})

    async def hgetall(self, key: str) -> Dict[str, str]:
        return dict(self.data.get(key, {}))

    async def expire(self, key: str, ttl: int) -> bool:
        self.ttls[key] = ttl
        return True

    async def delete(self, key: str) -> int:
        self.data.pop(key, None)
        self.ttls.pop(key, None)
        self.deleted.append(key)
        return 1

    async def scan_iter(self, match: str, count: int = 100):
        for key in list(self.data.keys()):
            if fnmatch.fnmatch(key, match):
                yield key

    async def aclose(self) -> None:
        return None


@pytest.fixture()
def fake_redis() -> _FakeAsyncRedis:
    return _FakeAsyncRedis()


@pytest.fixture()
def patched_redis_lib(monkeypatch: pytest.MonkeyPatch, fake_redis: _FakeAsyncRedis):
    """Route redis.asyncio.from_url at the registry's call site to the fake."""
    import redis.asyncio as redis_lib

    monkeypatch.setattr(redis_lib, "from_url", lambda *a, **k: fake_redis)
    return fake_redis


def _registry(**kwargs: Any) -> NodeRegistry:
    kwargs.setdefault("roles", ["api", "ai-worker"])
    kwargs.setdefault("version", "test-1.0")
    kwargs.setdefault("agent_count_provider", lambda: 7)
    return NodeRegistry(**kwargs)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Role parsing + status thresholds (pure functions)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.unit
def test_parse_roles_keeps_valid() -> None:
    assert parse_roles("api,ai-worker,streamer") == ["api", "ai-worker", "streamer"]


@pytest.mark.unit
def test_parse_roles_drops_unknown_and_dedups() -> None:
    assert parse_roles("api, bogus ,api, streamer") == ["api", "streamer"]


@pytest.mark.unit
def test_parse_roles_empty_falls_back_to_default() -> None:
    assert parse_roles("") == list(DEFAULT_ROLES)
    assert parse_roles(None) == list(DEFAULT_ROLES)
    assert parse_roles("nope,nada") == list(DEFAULT_ROLES)


@pytest.mark.unit
def test_node_status_thresholds() -> None:
    assert node_status(0.0) == "healthy"
    assert node_status(29.9) == "healthy"
    assert node_status(30.0) == "degraded"
    assert node_status(44.9) == "degraded"
    assert node_status(45.0) == "stale"
    assert node_status(10_000.0) == "stale"
    assert node_status(float("inf")) == "stale"


@pytest.mark.unit
def test_build_node_entry_computes_age_and_status() -> None:
    now = time.time()
    fields = {
        "hostname": "node-a",
        "roles": "api,streamer",
        "started_at": "2026-10-18T00:00:00+00:00",
        "version": "1.0.0",
        "agent_count": "12",
        "heartbeat_ts": f"{now - 10:.3f}",
    }
    entry = build_node_entry("abc123", fields, now)
    assert entry["node_id"] == "abc123"
    assert entry["roles"] == ["api", "streamer"]
    assert entry["agent_count"] == 12
    assert 9.0 <= entry["heartbeat_age_s"] <= 11.0
    assert entry["status"] == "healthy"
    assert entry["standalone"] is False


@pytest.mark.unit
def test_build_node_entry_degraded_and_stale() -> None:
    now = time.time()
    base = {"hostname": "h", "roles": "api", "started_at": "", "version": "", "agent_count": "0"}
    assert build_node_entry("x", {**base, "heartbeat_ts": f"{now - 35:.3f}"}, now)["status"] == "degraded"
    assert build_node_entry("x", {**base, "heartbeat_ts": f"{now - 60:.3f}"}, now)["status"] == "stale"


@pytest.mark.unit
def test_build_node_entry_missing_timestamp_is_stale_not_healthy() -> None:
    entry = build_node_entry("x", {"hostname": "h"}, time.time())
    assert entry["status"] == "stale"
    assert entry["heartbeat_age_s"] == float("inf")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Registry lifecycle against the fake Redis
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.unit
def test_node_id_defaults_to_uuid4_hex_per_process() -> None:
    r1 = NodeRegistry(roles=["api"])
    r2 = NodeRegistry(roles=["api"])
    assert len(r1.node_id) == 32 and all(c in "0123456789abcdef" for c in r1.node_id)
    assert r1.node_id != r2.node_id  # per-process identity, no key clobbering


@pytest.mark.unit
def test_node_id_override_honored() -> None:
    r = NodeRegistry(node_id="stable-node-1", roles=["api"])
    assert r.node_id == "stable-node-1"
    assert r.key == "aegis:nodes:stable-node-1"


@pytest.mark.unit
async def test_register_writes_hash_and_ttl(
    patched_redis_lib: _FakeAsyncRedis,
) -> None:
    reg = _registry(node_id="node-reg-1")
    await reg.start()
    try:
        assert reg.standalone is False
        stored = patched_redis_lib.data["aegis:nodes:node-reg-1"]
        assert stored["hostname"] == reg.hostname
        assert stored["roles"] == "api,ai-worker"
        assert stored["version"] == "test-1.0"
        assert stored["agent_count"] == "7"  # from the provider
        assert float(stored["heartbeat_ts"]) > 0
        assert patched_redis_lib.ttls["aegis:nodes:node-reg-1"] == 45
    finally:
        await reg.stop()


@pytest.mark.unit
async def test_heartbeat_refreshes_timestamp_and_ttl(
    patched_redis_lib: _FakeAsyncRedis,
) -> None:
    reg = _registry(node_id="node-beat-1")
    await reg.start()
    try:
        before = float(patched_redis_lib.data[reg.key]["heartbeat_ts"])
        await asyncio.sleep(0.02)
        await reg._beat()
        after = float(patched_redis_lib.data[reg.key]["heartbeat_ts"])
        assert after >= before
        assert patched_redis_lib.ttls[reg.key] == 45
    finally:
        await reg.stop()


@pytest.mark.unit
async def test_deregister_removes_key_and_stops_task(
    patched_redis_lib: _FakeAsyncRedis,
) -> None:
    reg = _registry(node_id="node-stop-1")
    await reg.start()
    assert reg._heartbeat_task is not None
    await reg.stop()
    assert "aegis:nodes:node-stop-1" not in patched_redis_lib.data
    assert "aegis:nodes:node-stop-1" in patched_redis_lib.deleted
    assert reg._heartbeat_task is None


@pytest.mark.unit
async def test_list_nodes_returns_all_registered_nodes(
    patched_redis_lib: _FakeAsyncRedis,
) -> None:
    reg1 = _registry(node_id="node-list-1", roles=["api"])
    reg2 = _registry(node_id="node-list-2", roles=["streamer"])
    await reg1.start()
    await reg2.start()
    try:
        nodes = await reg1.list_nodes()
        ids = {n["node_id"] for n in nodes}
        assert {"node-list-1", "node-list-2"} <= ids
        by_id = {n["node_id"]: n for n in nodes}
        assert by_id["node-list-2"]["roles"] == ["streamer"]
        assert by_id["node-list-1"]["status"] == "healthy"
        assert by_id["node-list-1"]["agent_count"] == 7
    finally:
        await reg1.stop()
        await reg2.stop()


@pytest.mark.unit
async def test_agent_count_provider_failure_does_not_kill_heartbeat(
    patched_redis_lib: _FakeAsyncRedis,
) -> None:
    def boom() -> int:
        raise RuntimeError("provider exploded")

    reg = NodeRegistry(node_id="node-boom", roles=["api"], agent_count_provider=boom)
    await reg.start()
    try:
        await reg._beat()  # must not raise
        assert patched_redis_lib.data[reg.key]["agent_count"] == "0"
    finally:
        await reg.stop()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Standalone fallback (Redis unreachable)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.mark.unit
async def test_startup_without_redis_goes_standalone(monkeypatch: pytest.MonkeyPatch) -> None:
    import redis.asyncio as redis_lib

    monkeypatch.setattr(redis_lib, "from_url", lambda *a, **k: _FakeAsyncRedis(fail_ping=True))
    reg = _registry(node_id="node-alone")
    await reg.start()  # must not raise
    try:
        assert reg.standalone is True
        nodes = await reg.list_nodes()
        assert len(nodes) == 1  # honest: only this process, never invented peers
        entry = nodes[0]
        assert entry["node_id"] == "node-alone"
        assert entry["hostname"] == reg.hostname
        assert entry["standalone"] is True
        assert entry["status"] == "healthy"
        assert entry["agent_count"] == 7
    finally:
        await reg.stop()


@pytest.mark.unit
async def test_midrun_redis_failure_degrades_to_standalone(
    patched_redis_lib: _FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = _registry(node_id="node-flaky")
    await reg.start()
    try:
        # Simulate Redis dying after registration: reads start failing.
        async def failing_hgetall(_key: str) -> Dict[str, str]:
            from redis.exceptions import ConnectionError

            raise ConnectionError("simulated: redis died mid-run")

        async def failing_scan_iter(_match: str, count: int = 100):
            from redis.exceptions import ConnectionError

            raise ConnectionError("simulated: redis died mid-run")
            yield  # make it an async generator

        monkeypatch.setattr(patched_redis_lib, "hgetall", failing_hgetall)
        monkeypatch.setattr(patched_redis_lib, "scan_iter", failing_scan_iter)

        nodes = await reg.list_nodes()
        assert reg.standalone is True
        assert len(nodes) == 1
        assert nodes[0]["standalone"] is True
        assert nodes[0]["node_id"] == "node-flaky"
    finally:
        await reg.stop()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Cluster API router
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _viewer() -> TokenPayload:
    return TokenPayload(user_id="u-viewer", username="test-viewer", role="viewer")


def _cluster_app(monkeypatch: pytest.MonkeyPatch, entries: List[Dict[str, Any]]) -> FastAPI:
    from app.api.v1.cluster.router import router

    class _StubRegistry:
        async def list_nodes(self) -> List[Dict[str, Any]]:
            return entries

    monkeypatch.setattr("app.api.v1.cluster.router.node_registry", _StubRegistry())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = _viewer
    return app


@pytest.mark.unit
def test_cluster_nodes_endpoint_returns_nodes_for_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = [
        {
            "node_id": "aaa",
            "hostname": "host-a",
            "roles": ["api", "ai-worker"],
            "started_at": "2026-10-18T00:00:00+00:00",
            "version": "1.0.0",
            "heartbeat_age_s": 4.2,
            "status": "healthy",
            "agent_count": 5,
            "standalone": False,
        }
    ]
    client = TestClient(_cluster_app(monkeypatch, entries))
    resp = client.get("/cluster/nodes")
    assert resp.status_code == 200
    body = resp.json()
    assert body == entries


@pytest.mark.unit
def test_cluster_nodes_endpoint_401_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1.cluster.router import router

    class _StubRegistry:
        async def list_nodes(self) -> List[Dict[str, Any]]:
            return []

    monkeypatch.setattr("app.api.v1.cluster.router.node_registry", _StubRegistry())
    app = FastAPI()
    app.include_router(router)  # no auth override: real get_current_user runs
    resp = TestClient(app).get("/cluster/nodes")
    assert resp.status_code == 401
