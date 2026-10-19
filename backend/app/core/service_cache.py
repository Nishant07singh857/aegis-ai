"""Shared two-tier read cache for master-data services.

Same pattern the teams service established (Phase 12): Redis via
``cache_manager`` when available, plus a process-local TTL dict so reads
stay fast (and tests stay DB/Redis-free) when Redis is down. A generation
counter embedded in every key makes invalidation O(1): writes bump the
generation and old Redis entries simply expire.

``ServiceCache`` is instantiated per service (one per API namespace) so
generations and local entries never collide across services.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional, Tuple

from app.core.cache import cache_manager

logger = logging.getLogger("aegis.service_cache")

DEFAULT_TTL = 60


class ServiceCache:
    """Two-tier (Redis + local) TTL cache with generation-bump invalidation.

    Args:
        namespace: prefix embedded in every key (e.g. ``"players"``).
        ttl: seconds a cached read stays fresh.
        serialize / deserialize: convert cached values to/from JSON-safe
            payloads for the Redis tier. The local tier keeps the raw value.
    """

    def __init__(
        self,
        namespace: str,
        ttl: int = DEFAULT_TTL,
        serialize: Optional[Callable[[Any], Any]] = None,
        deserialize: Optional[Callable[[Any], Any]] = None,
    ) -> None:
        self.namespace = namespace
        self.ttl = ttl
        self._serialize = serialize or (lambda v: v)
        self._deserialize = deserialize or (lambda v: v)
        self._generation = 0
        self._local: Dict[str, Tuple[float, Any]] = {}  # key -> (expires_at, value)

    # ---------- keying ----------

    def _key(self, kind: str, *parts: str) -> str:
        return f"{self.namespace}:v{self._generation}:{kind}:" + ":".join(parts)

    # ---------- reads ----------

    async def get(self, kind: str, *parts: str) -> Tuple[bool, Any]:
        """Return (hit, value). Never raises — a cache failure is a miss."""
        key = self._key(kind, *parts)
        try:
            payload = await cache_manager.get(key)
            if payload is not None:
                return True, self._deserialize(payload)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(f"{self.namespace} Redis cache read failed: {exc}")
        entry = self._local.get(key)
        if entry is not None:
            expires_at, value = entry
            if expires_at > time.time():
                return True, value
            del self._local[key]
        return False, None

    async def set(self, kind: str, value: Any, *parts: str) -> None:
        """Populate both tiers. Never raises."""
        key = self._key(kind, *parts)
        self._local[key] = (time.time() + self.ttl, value)
        try:
            await cache_manager.set(key, self._serialize(value), ttl=self.ttl)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(f"{self.namespace} Redis cache write failed: {exc}")

    # ---------- invalidation ----------

    def invalidate(self) -> None:
        """Bump the generation and drop the local tier.

        Redis entries under the old generation are left to expire via TTL —
        no key scanning needed, and stale reads are impossible because the
        generation is part of every key. Call this on every write.
        """
        self._generation += 1
        self._local.clear()
        logger.debug(f"{self.namespace} cache invalidated (generation {self._generation})")

    # ---------- test hooks ----------

    def _clear_local(self) -> None:
        self._local.clear()
