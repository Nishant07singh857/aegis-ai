# backend/app/cluster/registry.py
"""AEGIS AI — distributed node registry (Phase 7 / W3).

Every backend process ("node") registers itself in Redis on startup and
heartbeats every :data:`HEARTBEAT_INTERVAL_S` seconds. The key
``aegis:nodes:{node_id}`` carries a 45s TTL, so dead nodes vanish from the
cluster view automatically — no leader election, no manual cleanup.

Node identity
-------------
``node_id`` is a uuid4 hex generated **per process start** (overridable via
the ``NODE_ID`` env var). Per-process identity is deliberate: several uvicorn
workers on the same host would otherwise share one registry key and clobber
each other; with per-process ids each worker shows up as its own node and the
operator sees the true process topology.

Roles
-----
From env ``NODE_ROLES`` (comma-separated, default ``"api,ai-worker"``).
Valid values: ``api``, ``ai-worker``, ``streamer``. Unknown values are
dropped with a warning — never persisted.

Graceful degradation
--------------------
If Redis is unreachable at startup (or drops mid-run), the node keeps
serving in **standalone mode**: a warning is logged and ``list_nodes()``
returns a single honest entry describing *this* process, marked
``standalone=True``. No fake peers are ever invented.

agent_count
-----------
Each node counts its *own* running agents and stores the count in its hash
on every heartbeat. The count is provided by an injected
``agent_count_provider`` callable (wired in ``app.main``); the registry
itself never guesses.
"""

from __future__ import annotations

import asyncio
import os
import socket
import structlog
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.config import settings

logger = structlog.get_logger("aegis.cluster.registry")

# Redis key prefix for node registrations.
KEY_PREFIX = "aegis:nodes:"

# Heartbeat every 15s, TTL 45s: a node that misses 3 beats is auto-removed.
HEARTBEAT_INTERVAL_S = 15.0
HEARTBEAT_TTL_S = 45

# Status thresholds (seconds since last heartbeat).
HEALTHY_BELOW_S = 30.0   # <30s  -> healthy (green)
DEGRADED_BELOW_S = 45.0   # 30-45s -> degraded (amber); >45s -> stale (red,
                          # normally impossible because the key TTL is 45s,
                          # but computed anyway for safety)

VALID_ROLES = frozenset({"api", "ai-worker", "streamer"})
DEFAULT_ROLES = ("api", "ai-worker")


def parse_roles(raw: Optional[str]) -> List[str]:
    """Parse ``NODE_ROLES`` into a clean, deduplicated list of valid roles.

    Unknown values are dropped (with a warning); an empty result falls
    back to :data:`DEFAULT_ROLES`.
    """
    seen: List[str] = []
    for token in (raw or "").split(","):
        role = token.strip().lower()
        if not role:
            continue
        if role not in VALID_ROLES:
            logger.warning(
                "Ignoring unknown node role",
                role=role,
                valid_roles=sorted(VALID_ROLES),
            )
            continue
        if role not in seen:
            seen.append(role)
    return seen or list(DEFAULT_ROLES)


def node_version() -> str:
    """Build version: explicit GIT_SHA env first, else the app VERSION constant."""
    return os.environ.get("GIT_SHA") or os.environ.get("AEGIS_VERSION") or settings.VERSION


def node_status(heartbeat_age_s: float) -> str:
    """Map heartbeat age to healthy/degraded/stale."""
    if heartbeat_age_s < HEALTHY_BELOW_S:
        return "healthy"
    if heartbeat_age_s < DEGRADED_BELOW_S:
        return "degraded"
    return "stale"


def build_node_entry(node_id: str, fields: Dict[str, Any], now: float) -> Dict[str, Any]:
    """Build the public node dict the API returns.

    ``fields`` is the Redis hash (all strings); ``heartbeat_ts`` is epoch
    seconds of the node's last beat. A missing/unparseable timestamp is
    treated as infinitely old (status "stale"), never as healthy.
    """
    try:
        last_beat = float(fields.get("heartbeat_ts", ""))
    except (TypeError, ValueError):
        last_beat = 0.0
    age = max(0.0, now - last_beat) if last_beat > 0 else float("inf")
    roles = [r for r in str(fields.get("roles", "")).split(",") if r]
    try:
        agent_count = int(fields.get("agent_count", "0") or 0)
    except (TypeError, ValueError):
        agent_count = 0
    return {
        "node_id": node_id,
        "hostname": str(fields.get("hostname", "unknown")),
        "roles": roles,
        "started_at": str(fields.get("started_at", "")),
        "version": str(fields.get("version", "")),
        "heartbeat_age_s": round(age, 1),
        "status": node_status(age),
        "agent_count": max(0, agent_count),
        "standalone": str(fields.get("standalone", "")).lower() == "true",
    }


class NodeRegistry:
    """Registers this process in Redis and keeps its heartbeat alive.

    Create one per process (the module-level :data:`node_registry` singleton
    is what ``app.main`` uses). Call :meth:`start` in the FastAPI lifespan
    startup and :meth:`stop` on shutdown.
    """

    def __init__(
        self,
        *,
        redis_url: Optional[str] = None,
        node_id: Optional[str] = None,
        roles: Optional[List[str]] = None,
        version: Optional[str] = None,
        agent_count_provider: Optional[Callable[[], int]] = None,
    ) -> None:
        self.redis_url = redis_url or settings.REDIS_URL
        self.node_id = node_id or settings.NODE_ID or uuid.uuid4().hex
        self.roles = roles if roles is not None else parse_roles(settings.NODE_ROLES)
        self.version = version or node_version()
        self.hostname = socket.gethostname()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._agent_count_provider = agent_count_provider

        self._redis: Any = None          # redis.asyncio client (lazy)
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._standalone = False         # True while Redis is unreachable
        self._redis_ok = False
        self._last_local_beat = time.time()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def key(self) -> str:
        return f"{KEY_PREFIX}{self.node_id}"

    @property
    def standalone(self) -> bool:
        """True when Redis is unreachable: only this node is reported."""
        return self._standalone

    def set_agent_count_provider(self, provider: Callable[[], int]) -> None:
        self._agent_count_provider = provider

    def _agent_count(self) -> int:
        if self._agent_count_provider is None:
            return 0
        try:
            return max(0, int(self._agent_count_provider()))
        except Exception as exc:  # a provider bug must never kill heartbeats
            logger.warning("agent_count_provider failed", error=str(exc))
            return 0

    def _hash_fields(self) -> Dict[str, str]:
        return {
            "hostname": self.hostname,
            "roles": ",".join(self.roles),
            "started_at": self.started_at,
            "version": self.version,
            "agent_count": str(self._agent_count()),
            "heartbeat_ts": f"{time.time():.3f}",
            "standalone": "true" if self._standalone else "false",
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, agent_count_provider: Optional[Callable[[], int]] = None) -> None:
        """Register this node in Redis and begin heartbeating.

        Never raises: Redis failure drops the node into standalone mode with
        a clear warning so the process keeps serving.
        """
        if agent_count_provider is not None:
            self._agent_count_provider = agent_count_provider
        try:
            import redis.asyncio as redis_lib

            client = redis_lib.from_url(
                self.redis_url,
                decode_responses=True,
                socket_timeout=3,
                socket_connect_timeout=3,
            )
            await client.ping()
            self._redis = client
            self._redis_ok = True
            self._standalone = False
        except Exception as exc:
            self._redis = None
            self._redis_ok = False
            self._standalone = True
            logger.warning(
                "Redis unreachable — node registry running STANDALONE; "
                "cluster view will show only this node",
                redis_url=self._redis_url_for_log(),
                error=str(exc),
            )
            return

        await self._register()
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name="node-registry-heartbeat"
        )
        logger.info(
            "Node registered in cluster",
            node_id=self.node_id,
            hostname=self.hostname,
            roles=self.roles,
            version=self.version,
        )

    def _redis_url_for_log(self) -> str:
        # Never log credentials: strip any userinfo from the URL.
        url = self.redis_url or ""
        if "@" in url:
            url = url.split("@", 1)[1]
            scheme = self.redis_url.split("://", 1)[0] if "://" in self.redis_url else ""
            url = f"{scheme}://<redacted>@{url}" if scheme else url
        return url

    async def _register(self) -> None:
        assert self._redis is not None
        pipe = self._redis.pipeline()
        pipe.hset(self.key, mapping=self._hash_fields())
        pipe.expire(self.key, HEARTBEAT_TTL_S)
        await pipe.execute()
        self._last_local_beat = time.time()

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_S)
                await self._beat()
        except asyncio.CancelledError:
            pass

    async def _beat(self) -> None:
        """One heartbeat: refresh fields + TTL. Survives Redis blips."""
        if self._redis is None:
            return
        try:
            pipe = self._redis.pipeline()
            pipe.hset(self.key, mapping=self._hash_fields())
            pipe.expire(self.key, HEARTBEAT_TTL_S)
            await pipe.execute()
            self._last_local_beat = time.time()
            if not self._redis_ok:
                logger.info("Redis reachable again — cluster registry re-joined")
            self._redis_ok = True
            self._standalone = False
        except Exception as exc:
            if self._redis_ok:
                logger.warning(
                    "Node heartbeat failed — continuing, will retry",
                    error=str(exc),
                )
            self._redis_ok = False
            self._standalone = True

    async def stop(self) -> None:
        """Cancel the heartbeat and remove this node's registration."""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        if self._redis is not None:
            try:
                await self._redis.delete(self.key)
            except Exception:
                pass  # key TTL cleans up regardless
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None
        logger.info("Node deregistered from cluster", node_id=self.node_id)

    # ------------------------------------------------------------------
    # Reads (used by the cluster API)
    # ------------------------------------------------------------------

    async def list_nodes(self) -> List[Dict[str, Any]]:
        """Return the live node list.

        Redis reachable: every registered node. Redis down: a single honest
        entry for *this* process with ``standalone=True``. Never invents
        peers.
        """
        now = time.time()
        if self._redis is None or not self._redis_ok:
            return [self._standalone_entry(now)]
        entries: List[Dict[str, Any]] = []
        try:
            async for key in self._redis.scan_iter(match=f"{KEY_PREFIX}*", count=100):
                node_id = key[len(KEY_PREFIX):] if isinstance(key, str) else str(key)
                fields = await self._redis.hgetall(key)
                entries.append(build_node_entry(node_id, fields, now))
        except Exception as exc:
            logger.warning("Cluster read failed — falling back to standalone view", error=str(exc))
            self._redis_ok = False
            self._standalone = True
            return [self._standalone_entry(now)]
        entries.sort(key=lambda e: (e["hostname"], e["node_id"]))
        return entries

    def _standalone_entry(self, now: float) -> Dict[str, Any]:
        fields = self._hash_fields()
        fields["standalone"] = "true"
        entry = build_node_entry(self.node_id, fields, now)
        entry["status"] = "healthy"  # our own local liveness is certain
        entry["heartbeat_age_s"] = round(now - self._last_local_beat, 1)
        return entry


#: Process-wide singleton used by ``app.main`` lifespan and the cluster API.
node_registry = NodeRegistry()
