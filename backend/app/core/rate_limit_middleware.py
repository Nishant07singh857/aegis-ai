# backend/app/core/rate_limit_middleware.py
"""
AEGIS AI - Rate Limit Middleware (ASGI)

Redis-backed per-IP rate limiting for expensive/mutating HTTP endpoints,
so limits are shared across processes/nodes (single Redis counter per
rule+IP via an atomic Lua INCR+EXPIRE).

Rules:
  POST /api/v1/auth/token                          -> 5 requests/min per IP
  POST /api/v1/broadcast/*, /api/v1/scrims/*,
       /api/v1/content/*                           -> 60 requests/min per IP

Over-limit requests get HTTP 429 with a Retry-After header.
Everything else passes through untouched.

Degradation: if Redis is unreachable, the middleware logs an honest
warning (throttled) and falls back to the in-memory per-process limiter
in SecurityManager — single-node degradation, never fail-closed. Rate
limiting must never take the app down.

Wiring (see report for the exact main.py insertion spec):
  from app.core.rate_limit_middleware import RateLimitMiddleware
  app.add_middleware(RateLimitMiddleware)
"""

from __future__ import annotations

import json
import time
from typing import Awaitable, Callable, List, Optional, Tuple

import redis.asyncio as redis
import structlog

from app.config import settings
from app.core.security import security_manager

logger = structlog.get_logger("aegis.core.rate_limit")

Scope = dict
Message = dict
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


# (path_matcher, max_requests_per_minute, rule_name)
# A matcher is either an exact path or a (prefix, ) tuple.
_RULES: List[Tuple[str, int, str]] = [
    ("/api/v1/auth/token", 5, "auth_token"),
    ("/api/v1/broadcast/", 60, "broadcast_write"),
    ("/api/v1/scrims/", 60, "scrims_write"),
    ("/api/v1/content/", 60, "content_write"),
]

_RETRY_AFTER_SECONDS = 60
_REDIS_KEY_PREFIX = "aegis:ratelimit"

# Atomic fixed-window counter: INCR, and set the window TTL on first hit.
_LUA_CHECK = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""

_redis_client: Optional[redis.Redis] = None
_last_redis_warn: float = 0.0
_REDIS_WARN_THROTTLE_SECONDS = 60.0


def _match_rule(method: str, path: str) -> Optional[Tuple[int, str]]:
    """Return (max_requests, rule_name) for POST routes, else None."""
    if method != "POST":
        return None
    for matcher, max_requests, rule_name in _RULES:
        if matcher.endswith("/"):
            if path.startswith(matcher):
                return max_requests, rule_name
        elif path == matcher:
            return max_requests, rule_name
    return None


def _get_redis() -> redis.Redis:
    """Lazily build the shared Redis client (same pattern as cache.py)."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(
            settings.REDIS_URL,
            max_connections=10,
            socket_timeout=2.0,
            decode_responses=True,
        )
    return _redis_client


async def _check_rate_limit_redis(
    key: str, max_requests: int, window_seconds: int
) -> Optional[bool]:
    """Shared-counter check against Redis.

    Returns None when Redis is unavailable (caller degrades honestly).
    """
    client = _get_redis()
    count = await client.eval(_LUA_CHECK, 1, key, window_seconds)
    return int(count) <= max_requests


async def check_rate_limit(key: str, max_requests: int, window_seconds: int = 60) -> bool:
    """Rate-limit check shared across nodes via Redis.

    Falls back to the in-memory per-process limiter when Redis is
    unreachable (single-node degradation) — never fails closed.
    """
    global _last_redis_warn
    redis_key = f"{_REDIS_KEY_PREFIX}:{key}"
    try:
        allowed = await _check_rate_limit_redis(redis_key, max_requests, window_seconds)
        if allowed is not None:
            return allowed
    except Exception as e:  # Redis down, auth error, timeout, ...
        now = time.time()
        if now - _last_redis_warn >= _REDIS_WARN_THROTTLE_SECONDS:
            _last_redis_warn = now
            logger.warning(
                "Redis unavailable for rate limiting; degraded to single-node "
                "in-memory limiter (limits are per-process until Redis recovers)",
                error=str(e),
            )

    # Degraded path: per-process sliding window. Still protective on a
    # single node, and honest about it in the log line above.
    return security_manager.check_rate_limit(
        key, max_requests=max_requests, window_seconds=window_seconds
    )


class RateLimitMiddleware:
    """ASGI middleware enforcing the rules above."""

    def __init__(self, app):
        self.app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        rule = _match_rule(scope.get("method", ""), scope.get("path", ""))
        if rule is None:
            await self.app(scope, receive, send)
            return

        max_requests, rule_name = rule
        client = scope.get("client")
        ip = client[0] if client else "unknown"
        key = f"{rule_name}:{ip}"

        try:
            allowed = await check_rate_limit(
                key, max_requests=max_requests, window_seconds=60
            )
        except Exception as e:
            # Rate limiting must never take the app down; fail open.
            logger.warning("Rate limit check failed; allowing request", error=str(e))
            allowed = True

        if allowed:
            await self.app(scope, receive, send)
            return

        body = json.dumps(
            {
                "detail": (
                    "Rate limit exceeded. "
                    f"Max {max_requests} requests per minute for this endpoint."
                ),
                "rule": rule_name,
            }
        ).encode("utf-8")

        logger.info(
            "Rate limit exceeded",
            rule=rule_name,
            ip=ip,
            path=scope.get("path"),
        )
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", str(_RETRY_AFTER_SECONDS).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
