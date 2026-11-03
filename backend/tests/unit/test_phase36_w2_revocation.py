# backend/tests/unit/test_phase36_w2_revocation.py
"""Phase 36 Worker 2 — N-4 operator revocation path + L-4.

  * N-4: Phase 35 left ``revoke_api_key()`` in-memory only with zero
    production callers and a WS ``revoke_key`` handler that still
    reported restart-only. Now the revocation is PERSISTENT:
    ``revoke_api_key_persistent`` writes the key's SHA-256 digest to
    the ``revoked_api_key`` table (migration 018) AND to the
    in-memory registry, the lifespan seeds the registry from the
    table at startup, the operator-only REST endpoint
    ``POST /api/v1/admin/api-keys/revoke`` and the WS ``revoke_key``
    action both call it, and a restart simulation (fresh cache
    re-seeded from the same DB) still rejects the key. Digest-only
    storage throughout: no raw key is ever stored, logged, or echoed.
  * L-4: the WS-connect ``director_state`` initial literal was sent
    unmarked even with DEMO off. It now carries
    ``"source": "placeholder"`` (DEMO on: the C6 ``"source": "demo"``
    frame instead).

No secrets: all key values below are test fixtures only. No commit,
push, stash, .env, PNG, workflow, Kaggle, or outreach touch here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from test_phase33_db_boot_fail_fast import _fpdf_stub_if_absent  # noqa: E402

from app.api import deps as deps_module  # noqa: E402
from app.api.deps import (  # noqa: E402
    ADMIN_PRINCIPAL,
    authenticate_checked,
    authenticate_websocket,
    clear_revoked_api_keys_for_tests,
    is_api_key_revoked,
    is_api_key_revoked_in_db,
    revoke_api_key,
    revoke_api_key_persistent,
    seed_revoked_api_key_cache,
)
from app.core.security import security_manager  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.models.revoked_api_key import RevokedApiKey  # noqa: E402

pytestmark = pytest.mark.unit

_VICTIM_KEY = "p36-victim-key"
_OTHER_KEY = "p36-other-key"


@pytest.fixture(scope="module")
def main_module():
    """app.main via the Phase 33 fpdf-stub context manager (teardown
    restores sys.modules and evicts stub-bound app.main — the pattern
    Phase 35's W4 file adopted for exactly this import)."""
    with _fpdf_stub_if_absent():
        try:
            import app.main as _main
        except ImportError as exc:
            pytest.skip(f"app.main not importable here: {exc}")
        yield _main


@pytest.fixture()
def client(main_module):
    return TestClient(main_module.app)


def _sqlite_friendly_metadata():
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, postgresql.JSONB):
                column.type = JSON()


@pytest.fixture(autouse=True)
def _clean_revocation_registry():
    clear_revoked_api_keys_for_tests()
    yield
    clear_revoked_api_keys_for_tests()


@pytest.fixture()
async def revocation_db(tmp_path, monkeypatch):
    """File-SQLite engine with the full schema (incl. migration-018
    table via create_all), wired in as deps.AsyncSessionLocal — the
    session source the persistent functions actually use."""
    _sqlite_friendly_metadata()
    db_path = tmp_path / "p36_revocation.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(deps_module, "AsyncSessionLocal", maker)
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.fixture()
async def no_table_db(tmp_path, monkeypatch):
    """Engine pointing at a DB file with NO tables at all: every DB
    write/read fails, which is how the degraded path is exercised."""
    db_path = tmp_path / "p36_no_table.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(deps_module, "AsyncSessionLocal", maker)
    try:
        yield maker
    finally:
        await engine.dispose()


def _set_api_keys(monkeypatch, keys):
    monkeypatch.setattr(deps_module.settings, "ADMIN_API_KEYS", list(keys))


def _digest(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


async def _read_revocation_row(maker, raw_key):
    async with maker() as session:
        row = (
            await session.execute(
                select(RevokedApiKey).where(
                    RevokedApiKey.key_digest == _digest(raw_key)
                )
            )
        ).scalars().first()
        if row is None:
            return None
        return {
            "key_digest": row.key_digest,
            "revoked_at": row.revoked_at,
            "revoked_by": row.revoked_by,
            "reason": row.reason,
        }


class _FakeManager:
    """Captures manager.broadcast calls made by handle_action."""

    def __init__(self):
        self.broadcasts = []

    async def broadcast(self, message):
        self.broadcasts.append(message)

    def acks(self):
        return [m["data"] for m in self.broadcasts if m.get("type") == "action_ack"]


@pytest.fixture()
def fake_manager(main_module, monkeypatch):
    fake = _FakeManager()
    monkeypatch.setattr(main_module, "manager", fake)
    return fake


# ══════════════════════════════════════════════════════════════════════
# N-4 — persistent revocation at the deps layer
# ══════════════════════════════════════════════════════════════════════


async def test_persistent_revoke_rejects_next_request_other_key_works(
    revocation_db, monkeypatch
):
    _set_api_keys(monkeypatch, [_VICTIM_KEY, _OTHER_KEY])
    from app.api.deps import _verify_api_key

    assert _verify_api_key(_VICTIM_KEY) is True
    result = await revoke_api_key_persistent(
        _VICTIM_KEY, revoked_by="operator", reason="p36 test revoke"
    )
    assert result == {
        "revoked": True,
        "was_configured": True,
        "persisted": True,
        "already_revoked": False,
    }
    # Next request denied on both paths; other key unaffected.
    assert _verify_api_key(_VICTIM_KEY) is False
    assert _verify_api_key(_OTHER_KEY) is True
    assert await authenticate_checked(bearer=None, api_key=_VICTIM_KEY) is None
    assert await authenticate_checked(bearer=None, api_key=_OTHER_KEY) is not None
    assert await authenticate_websocket(None, token=_VICTIM_KEY) is None
    assert await authenticate_websocket(None, token=_OTHER_KEY) is not None
    assert is_api_key_revoked(_VICTIM_KEY) is True
    assert await is_api_key_revoked_in_db(_VICTIM_KEY) is True
    assert await is_api_key_revoked_in_db(_OTHER_KEY) is False


async def test_persistent_revoke_stores_digest_only(revocation_db, monkeypatch):
    _set_api_keys(monkeypatch, [_VICTIM_KEY])
    await revoke_api_key_persistent(
        _VICTIM_KEY, revoked_by="operator", reason="leaked in chat"
    )
    row = await _read_revocation_row(revocation_db, _VICTIM_KEY)
    assert row is not None
    assert row["key_digest"] == _digest(_VICTIM_KEY)
    assert len(row["key_digest"]) == 64
    assert row["revoked_at"] is not None
    assert row["revoked_by"] == "operator"
    assert row["reason"] == "leaked in chat"
    # No raw key value anywhere in the stored row.
    assert _VICTIM_KEY not in json.dumps(row, default=str)


async def test_restart_simulation_fresh_cache_still_rejects(
    revocation_db, monkeypatch
):
    """The proof N-4 exists for: clear the in-memory registry (what a
    process restart does), re-seed it from the SAME database, and the
    revoked key is still rejected — while the other key still works."""
    _set_api_keys(monkeypatch, [_VICTIM_KEY, _OTHER_KEY])
    from app.api.deps import _verify_api_key

    await revoke_api_key_persistent(_VICTIM_KEY, revoked_by="operator")
    assert _verify_api_key(_VICTIM_KEY) is False

    # Fresh process: empty cache, then the startup seed.
    clear_revoked_api_keys_for_tests()
    assert _verify_api_key(_VICTIM_KEY) is True  # resurrected before seed
    seeded = await seed_revoked_api_key_cache()
    assert seeded == 1
    assert _verify_api_key(_VICTIM_KEY) is False
    assert _verify_api_key(_OTHER_KEY) is True
    assert await authenticate_checked(bearer=None, api_key=_VICTIM_KEY) is None


async def test_seed_loads_preexisting_row(revocation_db, monkeypatch):
    """A row written by an earlier process (inserted directly, cache
    empty) is picked up by the seed."""
    _set_api_keys(monkeypatch, [_VICTIM_KEY])
    from app.api.deps import _verify_api_key

    async with revocation_db() as session:
        session.add(RevokedApiKey(key_digest=_digest(_VICTIM_KEY)))
        await session.commit()
    assert _verify_api_key(_VICTIM_KEY) is True  # cache knows nothing yet
    assert await seed_revoked_api_key_cache() == 1
    assert _verify_api_key(_VICTIM_KEY) is False


async def test_revoke_unconfigured_key_still_recorded(
    revocation_db, monkeypatch
):
    _set_api_keys(monkeypatch, [_OTHER_KEY])
    result = await revoke_api_key_persistent("p36-never-configured")
    assert result["was_configured"] is False
    assert result["persisted"] is True
    assert result["revoked"] is True
    # Recorded, so it stays revoked if ever added to the allowlist.
    assert await is_api_key_revoked_in_db("p36-never-configured") is True
    assert revoke_api_key("") is False
    assert (await revoke_api_key_persistent(""))["revoked"] is False


async def test_revoke_twice_is_safe_noop(revocation_db, monkeypatch):
    _set_api_keys(monkeypatch, [_VICTIM_KEY])
    first = await revoke_api_key_persistent(_VICTIM_KEY)
    assert first["already_revoked"] is False
    second = await revoke_api_key_persistent(_VICTIM_KEY)
    assert second == {
        "revoked": True,
        "was_configured": True,
        "persisted": True,
        "already_revoked": True,
    }
    async with revocation_db() as session:
        count = (
            await session.execute(
                select(RevokedApiKey).where(
                    RevokedApiKey.key_digest == _digest(_VICTIM_KEY)
                )
            )
        ).scalars().all()
    assert len(count) == 1  # unique digest: exactly one row


async def test_db_write_failure_revokes_in_memory_but_not_persisted(
    no_table_db, monkeypatch
):
    """Degraded path: the DB write fails (no table), the running
    process is still protected, but the result says so honestly."""
    _set_api_keys(monkeypatch, [_VICTIM_KEY])
    from app.api.deps import _verify_api_key

    result = await revoke_api_key_persistent(_VICTIM_KEY)
    assert result["revoked"] is True
    assert result["persisted"] is False
    assert _verify_api_key(_VICTIM_KEY) is False
    # And a fresh process could NOT re-seed it: nothing was stored.
    clear_revoked_api_keys_for_tests()
    assert await seed_revoked_api_key_cache() == 0
    assert _verify_api_key(_VICTIM_KEY) is True


async def test_seed_failure_returns_zero_and_keeps_cache(
    no_table_db, monkeypatch
):
    _set_api_keys(monkeypatch, [_VICTIM_KEY])
    revoke_api_key(_VICTIM_KEY)  # in-memory only
    assert await seed_revoked_api_key_cache() == 0
    assert is_api_key_revoked(_VICTIM_KEY) is True


async def test_persistent_revoke_does_not_revoke_jwt_path(
    revocation_db, monkeypatch
):
    _set_api_keys(monkeypatch, [_VICTIM_KEY])
    from fastapi.security import HTTPAuthorizationCredentials

    token = security_manager.create_access_token(
        user_id="operator", username="operator", is_superuser=True,
        scopes=["admin"], role="admin",
    )
    bearer = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    assert await authenticate_checked(bearer=bearer, api_key=None) is not None
    await revoke_api_key_persistent(_VICTIM_KEY)
    assert await authenticate_checked(bearer=bearer, api_key=None) is not None


# ══════════════════════════════════════════════════════════════════════
# N-4 — WS revoke_key handler
# ══════════════════════════════════════════════════════════════════════


async def test_ws_revoke_key_persists_and_rejects(
    main_module, fake_manager, revocation_db, monkeypatch
):
    _set_api_keys(monkeypatch, [_VICTIM_KEY, _OTHER_KEY])
    from app.api.deps import _verify_api_key

    ack = await main_module.handle_action(
        {"action": "revoke_key", "api_key": _VICTIM_KEY, "reason": "ws test"},
        user=ADMIN_PRINCIPAL,
    )
    assert ack["status"] == "success"
    assert ack["revoked"] is True
    assert ack["was_configured"] is True
    assert ack["persisted"] is True
    assert ack["already_revoked"] is False
    # Never a fabricated key, never the raw key, in any frame.
    text = json.dumps(fake_manager.broadcasts)
    assert "sk_live_" not in text
    assert _VICTIM_KEY not in text
    # The key is rejected on the next request; the other key works.
    assert _verify_api_key(_VICTIM_KEY) is False
    assert _verify_api_key(_OTHER_KEY) is True
    row = await _read_revocation_row(revocation_db, _VICTIM_KEY)
    assert row is not None and row["reason"] == "ws test"


async def test_ws_revoke_key_without_key_is_error(
    main_module, fake_manager, monkeypatch
):
    # Preserves the Phase 32/H4 behaviour: no key → error, no success.
    with patch.object(main_module, "DEMO_MODE", False):
        ack = await main_module.handle_action(
            {"action": "revoke_key"}, user=ADMIN_PRINCIPAL
        )
    assert ack["status"] == "error"
    assert ack["message"]
    text = json.dumps(fake_manager.broadcasts)
    assert "sk_live_" not in text
    assert '"success"' not in text


async def test_ws_revoke_key_unconfigured_reports_honestly(
    main_module, fake_manager, revocation_db, monkeypatch
):
    _set_api_keys(monkeypatch, [_OTHER_KEY])
    ack = await main_module.handle_action(
        {"action": "revoke_key", "key": "p36-never-configured"},
        user=ADMIN_PRINCIPAL,
    )
    assert ack["status"] == "success"
    assert ack["was_configured"] is False
    assert ack["persisted"] is True
    assert await is_api_key_revoked_in_db("p36-never-configured") is True


# ══════════════════════════════════════════════════════════════════════
# N-4 — REST revoke endpoint
# ══════════════════════════════════════════════════════════════════════


def test_rest_revoke_endpoint_then_401(
    client, revocation_db, monkeypatch
):
    _set_api_keys(monkeypatch, [_VICTIM_KEY, _OTHER_KEY])
    resp = client.post(
        "/api/v1/admin/api-keys/revoke",
        json={"api_key": _VICTIM_KEY, "reason": "rest test"},
        headers={"X-API-Key": _OTHER_KEY},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert body["persisted"] is True
    assert body["was_configured"] is True
    assert _VICTIM_KEY not in resp.text  # raw key never echoed back
    # The revoked key is now 401 on a protected endpoint; the other
    # key still authenticates.
    assert client.get(
        "/api/v1/admin/system", headers={"X-API-Key": _VICTIM_KEY}
    ).status_code == 401
    assert client.get(
        "/api/v1/admin/system", headers={"X-API-Key": _OTHER_KEY}
    ).status_code == 200


def test_rest_revoke_requires_auth_and_key(client, monkeypatch):
    _set_api_keys(monkeypatch, [_OTHER_KEY])
    assert client.post(
        "/api/v1/admin/api-keys/revoke", json={"api_key": _OTHER_KEY}
    ).status_code == 401
    resp = client.post(
        "/api/v1/admin/api-keys/revoke",
        json={},
        headers={"X-API-Key": _OTHER_KEY},
    )
    assert resp.status_code == 400
    assert resp.json()["status"] == "error"


# ══════════════════════════════════════════════════════════════════════
# L-4 — WS-connect director_state initial literal
# ══════════════════════════════════════════════════════════════════════


def _director_state_frame(client, ws_path="/ws/live"):
    """Connect and return the director_state connect frame."""
    with client.websocket_connect(ws_path) as ws:
        for _ in range(40):
            frame = ws.receive_json()
            if frame.get("type") == "director_state":
                return frame
    raise AssertionError("director_state frame not seen on connect")


def test_ws_connect_director_state_marked_placeholder_demo_off(
    client, main_module
):
    with patch.object(main_module, "DEMO_MODE", False):
        frame = _director_state_frame(client)
    data = frame["data"]
    assert data["source"] == "placeholder"
    # The literal still carries its placeholder POV value — but it is
    # now labelled as a placeholder, not presented as a live decision.
    assert data["current_pov"] == "t1"
    assert data["reason"] == "Initial"
    # No top-level demo marker on a non-demo frame.
    assert frame.get("source") is None


def test_ws_connect_director_state_marked_demo_when_demo_on(
    client, main_module
):
    with patch.object(main_module, "DEMO_MODE", True):
        frame = _director_state_frame(client)
    assert frame.get("source") == "demo"
    assert frame["data"]["source"] == "demo"


# ══════════════════════════════════════════════════════════════════════
# Phase 36 QA fix (P6/P7) — DB-error logging must never carry a
# credential: a driver-shaped error echoing the connection URL must
# be scrubbed from BOTH the log message AND the exc_info traceback.
# Fake credential-shaped strings only; nothing here is a real secret.
# ══════════════════════════════════════════════════════════════════════

_FAKE_DB_URL = (
    "postgresql+asyncpg://fakeuser:fakepass-TEST-ONLY"
    "@fakehost.invalid:5432/fakedb"
)
_FAKE_DB_PASSWORD = "fakepass-TEST-ONLY"


def _assert_no_fake_credential_in_log(caplog):
    """The formatted log record (message + traceback) must contain
    neither the fake password nor the full fake URL — and must show
    the scrubber actually ran (``<redacted>``), not that logging was
    silently skipped."""
    text = caplog.text
    assert _FAKE_DB_PASSWORD not in text
    assert _FAKE_DB_URL not in text
    assert "fakeuser" not in text
    assert "<redacted>" in text


class _ExplodingFactory:
    """session_factory whose call raises a driver-shaped error that
    echoes the fake connection URL verbatim (the QA P6 shape)."""

    def __call__(self):
        raise Exception(
            "connection failed via "
            f"{_FAKE_DB_URL} (driver-shaped test message)"
        )


async def test_seed_failure_log_never_carries_db_credential(
    caplog, monkeypatch
):
    """QA P6 regression: seed failure logs a scrubbed surrogate, not
    the raw driver message — on the message AND the traceback."""
    monkeypatch.setattr(deps_module.settings, "DATABASE_URL", _FAKE_DB_URL)
    with caplog.at_level(logging.WARNING, logger="aegis.api.deps"):
        assert (
            await seed_revoked_api_key_cache(
                session_factory=_ExplodingFactory()
            )
            == 0
        )
    _assert_no_fake_credential_in_log(caplog)


class _ExplodingSession:
    """Supplied session whose commit raises a driver-shaped error
    echoing the fake connection URL verbatim (the QA P7 shape)."""

    def add(self, *args, **kwargs):
        return None

    async def commit(self):
        raise Exception(
            "write failed via "
            f"{_FAKE_DB_URL} (driver-shaped test message)"
        )

    async def rollback(self):
        return None


async def test_revoke_write_failure_log_never_carries_db_credential(
    caplog, monkeypatch
):
    """QA P7 regression: revoke-write failure logs a scrubbed
    surrogate, not the raw driver message — on the message AND the
    traceback. The key is still revoked in-memory (persisted=False)."""
    monkeypatch.setattr(deps_module.settings, "DATABASE_URL", _FAKE_DB_URL)
    _set_api_keys(monkeypatch, [_VICTIM_KEY])
    from app.api.deps import _verify_api_key

    with caplog.at_level(logging.WARNING, logger="aegis.api.deps"):
        result = await revoke_api_key_persistent(
            _VICTIM_KEY, session=_ExplodingSession()
        )
    assert result["revoked"] is True
    assert result["persisted"] is False
    assert _verify_api_key(_VICTIM_KEY) is False
    _assert_no_fake_credential_in_log(caplog)
