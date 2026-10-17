# backend/app/core/rate_limit_middleware.py
"""
AEGIS AI - Rate Limit Middleware (ASGI)

In-memory per-IP rate limiting for expensive/mutating HTTP endpoints,
backed by SecurityManager.check_rate_limit (sync, in-memory sliding window).

Rules:
  POST /api/v1/auth/token                          -> 5 requests/min per IP
  POST /api/v1/broadcast/*, /api/v1/scrims/*,
       /api/v1/content/*                           -> 60 requests/min per IP

Over-limit requests get HTTP 429 with a Retry-After header.
Everything else passes through untouched.

Wiring (see report for the exact main.py insertion spec):
  from app.core.rate_limit_middleware import RateLimitMiddleware
  app.add_middleware(RateLimitMiddleware)
"""

from __future__ import annotations

import json
from typing import Awaitable, Callable, List, Optional, Tuple

import structlog

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
        key = f"ratelimit:{rule_name}:{ip}"

        try:
            allowed = security_manager.check_rate_limit(
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
