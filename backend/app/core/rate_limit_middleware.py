# backend/app/core/rate_limit_middleware.py
"""
AEGIS AI - Rate Limit Middleware (ASGI)

Redis-backed per-IP rate limiting for expensive/mutating HTTP endpoints,
so limits are shared across processes/nodes (single Redis counter per
rule+IP via an atomic Lua INCR+EXPIRE).

Rules (first match wins; limits are per IP per 60-second window):

  POST /api/v1/auth/token                          ->   5/min (unchanged)
  POST /api/v1/video/feed-token                    ->  30/min (Phase 35,
      M-11: feed-token mint was unthrottled. 30/min is generous for the
      operator happy path — the streamer/output page mints once per
      capture start — while capping mint-flooding by an authenticated
      caller. Minting itself still requires operator auth.)
  POST/PUT/PATCH/DELETE /api/v1/video/             ->  60/min (capture
      start/stop, on-air, discover)
  POST/PUT/PATCH/DELETE /api/v1/director/          ->  60/min (mode,
      scene, camera, overlays, suggestion approve/reject)
  POST/PUT/PATCH/DELETE /api/v1/stream/            ->  60/min
  POST/PUT/PATCH/DELETE /api/v1/users/             ->  30/min (account
      provisioning / password reset / kill switch — rarer than a
      director action, so the tighter ceiling)
  POST/PUT/PATCH/DELETE /api/v1/observer-pilot/    ->  60/min
      (kill-switch, override, suggestion approve/reject)
  POST/PUT/PATCH/DELETE /api/v1/director-learning/ ->  60/min
  POST/PUT/PATCH/DELETE /api/v1/broadcast/         ->  60/min (unchanged)
  POST/PUT/PATCH/DELETE /api/v1/scrims/            ->  60/min (unchanged)
  POST/PUT/PATCH/DELETE /api/v1/content/           ->  60/min (unchanged)
  POST /api/v1/dossiers/ingest/                    ->  60/min (fight ingest)
  POST/PUT/PATCH/DELETE /api/v1/replay/            ->  60/min (recorder
      start/stop, queue take/dismiss)
  GET  /api/v1/video/feed/                         -> 120/min (MJPEG
      program feed: ONE request per stream connection, not per frame,
      so 120 connections/min per IP is generous for the output page,
      streamer and reconnects on slot switches, while capping
      connection-churn floods)
  GET  /api/v1/admin/                              -> 120/min (operator
      system/agent views)
  WS   /ws/live, /ws/monitor (handshake only)      ->  60/min (a client
      connects once and stays connected; only new handshakes are
      counted, never messages on an established connection)

Over-limit HTTP requests get 429 with a Retry-After header; over-limit
WebSocket handshakes are closed with code 1008 before the app accepts
them. Everything else passes through untouched.

Deliberately NOT throttled (Phase 35 decision, not an oversight):
UI-polled read-only GETs — /api/v1/director/state, /api/v1/broadcast/
status, /api/v1/system/status, /api/v1/analytics/*, /api/v1/teams,
/api/v1/matches, standings/kill-feed/summary — throttling them would
break the live broadcast UI, which polls them, for no credential or
exhaustion benefit (they are read-only and cheap relative to a stream).
The remaining write routers (stories, preshow, ceremony, zone, rundown,
pip, match-point, feed ingest, observers, cluster, analysis,
predictions, anti-cheat) stay auth-gated and unthrottled in this phase;
they are operator-only and neither credential- nor exhaustion-sensitive,
and a catch-all write rule was rejected because a single shared-IP
burst (venue NAT) must not 429 legit director/spotter traffic. If a
catch-all is wanted later, it belongs in its own phase with a
measured burst budget.

Degradation (Phase 35, M-11 — fail-closed where it counts):
  * Redis unreachable → the check degrades to the in-memory
    per-process limiter in SecurityManager and STILL LIMITS
    (single-node degradation). It never opens the floodgates.
  * The check itself failing (both backends unusable, an unexpected
    error inside the limiter) → the request is DENIED (429 for HTTP,
    close for WS), not allowed. The old "fail open so rate limiting
    never takes the app down" behaviour is gone: an uncheckable
    sensitive endpoint must not silently admit unlimited traffic.
    Unthrottled paths are unaffected, so a limiter failure can only
    ever deny throttled paths, never the whole app.

Wiring (see report for the exact main.py insertion spec):
  from app.core.rate_limit_middleware import RateLimitMiddleware
  app.add_middleware(RateLimitMiddleware)
"""

from __future__ import annotations

import json
import time
from typing import Awaitable, Callable, FrozenSet, List, Optional, Tuple

import redis.asyncio as redis
import structlog

from app.config import settings
from app.core.security import security_manager

logger = structlog.get_logger("aegis.core.rate_limit")

Scope = dict
Message = dict
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

_WRITE_METHODS: FrozenSet[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# (methods, path_matcher, max_requests_per_minute, rule_name), FIRST MATCH
# WINS — so the exact feed-token rule must precede the /video/ prefix
# rule, and the exact auth-token rule precedes everything. A matcher is
# either an exact path or a (prefix, ) tuple (trailing "/").
_RULES: List[Tuple[FrozenSet[str], str, int, str]] = [
    (frozenset({"POST"}), "/api/v1/auth/token", 5, "auth_token"),
    (frozenset({"POST"}), "/api/v1/video/feed-token", 30, "feed_token_mint"),
    (_WRITE_METHODS, "/api/v1/video/", 60, "video_write"),
    (_WRITE_METHODS, "/api/v1/director/", 60, "director_write"),
    (_WRITE_METHODS, "/api/v1/stream/", 60, "stream_write"),
    (_WRITE_METHODS, "/api/v1/users/", 30, "users_write"),
    (_WRITE_METHODS, "/api/v1/observer-pilot/", 60, "observer_pilot_write"),
    (_WRITE_METHODS, "/api/v1/director-learning/", 60, "director_learning_write"),
    (_WRITE_METHODS, "/api/v1/broadcast/", 60, "broadcast_write"),
    (_WRITE_METHODS, "/api/v1/scrims/", 60, "scrims_write"),
    (_WRITE_METHODS, "/api/v1/content/", 60, "content_write"),
    (frozenset({"POST"}), "/api/v1/dossiers/ingest/", 60, "dossiers_ingest"),
    (_WRITE_METHODS, "/api/v1/replay/", 60, "replay_write"),
    (frozenset({"GET"}), "/api/v1/video/feed/", 120, "video_feed_get"),
    (frozenset({"GET"}), "/api/v1/admin/", 120, "admin_get"),
]

# WebSocket handshake rules: (path, max_handshakes_per_minute, rule_name).
_WS_RULES: List[Tuple[str, int, str]] = [
    ("/ws/live", 60, "ws_live_connect"),
    ("/ws/monitor", 60, "ws_monitor_connect"),
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
    """Return (max_requests, rule_name) for a throttled route, else None.

    First matching rule wins; method must be in the rule's method set
    and the path must equal an exact matcher or start with a prefix
    matcher.
    """
    for methods, matcher, max_requests, rule_name in _RULES:
        if method not in methods:
            continue
        if matcher.endswith("/"):
            # A prefix rule also matches the bare prefix path itself
            # (POST /api/v1/users, the users-router create route, has
            # no trailing slash; /api/v1/users/42 must match too).
            if path == matcher[:-1] or path.startswith(matcher):
                return max_requests, rule_name
        elif path == matcher:
            return max_requests, rule_name
    return None


def _match_ws_rule(path: str) -> Optional[Tuple[int, str]]:
    """Return (max_handshakes, rule_name) for a throttled WS path."""
    for ws_path, max_requests, rule_name in _WS_RULES:
        if path == ws_path:
            return max_requests, rule_name
    return None


def _client_ip(scope: Scope) -> str:
    client = scope.get("client")
    return client[0] if client else "unknown"


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
    unreachable (single-node degradation) — the fallback STILL LIMITS;
    Redis being down never means unlimited requests. Only an error
    inside the fallback itself propagates to the caller, where the
    middleware denies fail-closed.
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

    async def _deny_http(
        self, send: Send, max_requests: int, rule_name: str, ip: str, path: str
    ) -> None:
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
            path=path,
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

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        scope_type = scope.get("type")
        if scope_type == "websocket":
            await self._handle_websocket(scope, receive, send)
            return
        if scope_type != "http":
            await self.app(scope, receive, send)
            return

        rule = _match_rule(scope.get("method", ""), scope.get("path", ""))
        if rule is None:
            await self.app(scope, receive, send)
            return

        max_requests, rule_name = rule
        ip = _client_ip(scope)
        key = f"{rule_name}:{ip}"

        try:
            allowed = await check_rate_limit(
                key, max_requests=max_requests, window_seconds=60
            )
        except Exception as e:
            # Fail closed (Phase 35, M-11): the limit could not be
            # checked at all (Redis down is NOT this path — it degrades
            # to the in-memory limiter inside check_rate_limit). An
            # uncheckable sensitive endpoint admits nothing.
            logger.warning(
                "Rate limit check failed; denying request (fail closed)",
                rule=rule_name,
                ip=ip,
                error=str(e),
            )
            await self._deny_http(send, max_requests, rule_name, ip, scope.get("path", ""))
            return

        if allowed:
            await self.app(scope, receive, send)
            return
        await self._deny_http(send, max_requests, rule_name, ip, scope.get("path", ""))

    async def _handle_websocket(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """Throttle only new handshakes on the two control/monitor
        sockets. Established connections are never counted again, and
        unlisted paths pass through untouched."""
        rule = _match_ws_rule(scope.get("path", ""))
        if rule is None:
            await self.app(scope, receive, send)
            return
        max_requests, rule_name = rule
        ip = _client_ip(scope)
        key = f"{rule_name}:{ip}"
        try:
            allowed = await check_rate_limit(
                key, max_requests=max_requests, window_seconds=60
            )
        except Exception as e:
            # Fail closed, same posture as HTTP: a handshake whose
            # limit cannot be checked is refused, not admitted.
            logger.warning(
                "WS rate limit check failed; refusing handshake (fail closed)",
                rule=rule_name,
                ip=ip,
                error=str(e),
            )
            await send(
                {"type": "websocket.close", "code": 1011, "reason": "Rate limit check unavailable"}
            )
            return
        if allowed:
            await self.app(scope, receive, send)
            return
        logger.info("WS rate limit exceeded", rule=rule_name, ip=ip, path=scope.get("path"))
        await send(
            {"type": "websocket.close", "code": 1008, "reason": "Rate limit exceeded"}
        )
