"""Phase 32 (K-C1) — short-lived signed credential for the MJPEG program feed.

PROBLEM (reinspection K-C1): Integrity Pack H3 correctly put auth on
``GET /video/feed/{slot}``, but the broadcast output page renders that feed
through a bare ``<img>`` tag, which cannot carry an ``Authorization`` header
or an ``X-API-Key`` header — and headless Chromium capture of
``/broadcast/output`` had no credential mechanism at all. Result: the
output page streamed 401 → NO FEED slate.

SOLUTION: a signed *feed token* carried in the query string, which an
``<img>`` tag CAN carry:

  * Mint: ``POST /api/v1/video/feed-token`` (operator+ only). Returns a
    token valid for a short TTL (default 600s, min 60s, max 3600s).
  * Use: ``GET /api/v1/video/feed/{slot}?feed_token=<token>`` — or, for
    headless capture, ``/broadcast/output?feed_token=<token>``, which the
    output page propagates into the ``<img>`` src (see
    ``frontend/components/broadcast/game-video.tsx``). The shell streamer
    entrypoint mints (or accepts) the token the same way.
  * Verify: HMAC-SHA256 over the payload, keyed by the same SECRET_KEY that
    signs JWTs. No DB row, no new secret, no paid service.

SCOPE — deliberately narrow, so H3 is NOT weakened:

  * The token is valid for ONE route only: ``GET /video/feed/{slot}``.
    It grants nothing else — not /video/status, not /video/thumbnail, not
    any mutating route. The ``get_feed_principal`` dependency below is
    wired onto the feed route and nowhere else.
  * The token is NOT a general auth credential: it carries no user, no
    role upgrade (it resolves to a viewer principal), and it cannot mint
    anything else.
  * The token is feed-wide, not slot-scoped, ON PURPOSE: the director
    switches slots mid-broadcast, and the ``<img>`` src follows
    ``active_camera``. A slot-scoped token would 401 on every camera
    switch. Feed-wide scope is still "feed only" — it cannot reach any
    other endpoint.

HONEST LIMITS (do not over-claim):

  * A token is a bearer credential for the feed until it expires: anyone
    who obtains the token (e.g. from a shared capture URL or browser
    history) can view the feed for the remainder of its short TTL.
    Mitigations, not eliminations: short default TTL, operator-only
    minting, ``Cache-Control: no-store`` on the feed response, and the
    token never appears in any log. Single-use tokens are NOT used:
    a MJPEG stream reconnects and the output page re-renders the ``<img>``
    on every slot switch, so single-use would break the stream itself.
  * There is no revocation list (no DB). If a token leaks, the remedy is
    to wait out its TTL (minutes) or rotate SECRET_KEY (which invalidates
    every token AND every JWT — operator decision, not done here).
  * If SECRET_KEY is unset, minting raises and verification fails closed
    (same fail-closed posture as JWT handling, Integrity C1).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps import authenticate_checked
from app.config import settings
from app.core.security import TokenPayload

__all__ = [
    "DEFAULT_FEED_TOKEN_TTL_SECONDS",
    "MIN_FEED_TOKEN_TTL_SECONDS",
    "MAX_FEED_TOKEN_TTL_SECONDS",
    "FEED_TOKEN_PRINCIPAL",
    "FeedTokenError",
    "get_feed_principal",
    "mint_feed_token",
    "verify_feed_token",
]

#: Token format version prefix (lets a future format coexist, never parsed
#: leniently: unknown versions fail closed).
_TOKEN_VERSION = "ft1"
#: Scope claim — the ONLY scope a feed token ever carries.
_FEED_SCOPE = "video-feed"

DEFAULT_FEED_TOKEN_TTL_SECONDS = 600
MIN_FEED_TOKEN_TTL_SECONDS = 60
MAX_FEED_TOKEN_TTL_SECONDS = 3600

#: Principal a valid feed token resolves to: a viewer, nothing more.
#: It is intentionally NOT the operator principal — feed-token holders
#: must never pass a require_operator gate.
FEED_TOKEN_PRINCIPAL = TokenPayload(
    user_id="feed-token",
    username="feed-token",
    is_superuser=False,
    scopes=[],
    role="viewer",
)


class FeedTokenError(ValueError):
    """Minting failed (bad TTL, or no signing key configured)."""


def _signing_secret() -> str:
    """SECRET_KEY via the guarded accessor: raises when unset (fail closed)."""
    return settings.SECRET_KEY.get_secret_value()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def mint_feed_token(
    ttl_seconds: int = DEFAULT_FEED_TOKEN_TTL_SECONDS,
    *,
    secret: Optional[str] = None,
    now: Optional[int] = None,
) -> str:
    """Mint a short-lived feed token.

    Raises:
        FeedTokenError: TTL outside [60, 3600], or no signing key.
    """
    if not MIN_FEED_TOKEN_TTL_SECONDS <= ttl_seconds <= MAX_FEED_TOKEN_TTL_SECONDS:
        raise FeedTokenError(
            f"ttl_seconds must be {MIN_FEED_TOKEN_TTL_SECONDS}.."
            f"{MAX_FEED_TOKEN_TTL_SECONDS}, got {ttl_seconds}."
        )
    try:
        key = secret if secret is not None else _signing_secret()
    except RuntimeError as exc:
        raise FeedTokenError(
            "Cannot mint a feed token: SECRET_KEY is not configured."
        ) from exc
    if not key:
        raise FeedTokenError("Cannot mint a feed token: SECRET_KEY is empty.")
    epoch = int(now if now is not None else time.time())
    payload = {
        "scope": _FEED_SCOPE,
        "exp": epoch + ttl_seconds,
        "nonce": secrets.token_urlsafe(16),
    }
    encoded = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{_TOKEN_VERSION}.{encoded}".encode("ascii")
    sig = hmac.new(key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{_TOKEN_VERSION}.{encoded}.{_b64url(sig)}"


def verify_feed_token(
    token: Optional[str],
    *,
    secret: Optional[str] = None,
    now: Optional[int] = None,
) -> bool:
    """Verify a feed token. Every failure mode returns False (never raises,
    except nothing — callers treat False as unauthenticated).

    Checks, all required: ``ft1`` version, exactly 3 dot-separated parts,
    JSON payload with scope == "video-feed", expiry strictly in the
    future, and a constant-time HMAC match. No slot check here — the
    token is feed-wide by design (see module docstring).
    """
    if not token:
        return False
    try:
        key = secret if secret is not None else _signing_secret()
    except RuntimeError:
        return False  # no signing key configured: fail closed
    if not key:
        return False
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != _TOKEN_VERSION:
        return False
    _, encoded, provided_sig = parts
    signing_input = f"{_TOKEN_VERSION}.{encoded}".encode("ascii")
    expected_sig = _b64url(
        hmac.new(key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    )
    if not hmac.compare_digest(expected_sig, provided_sig):
        return False
    try:
        payload = json.loads(_b64url_decode(encoded).decode("utf-8"))
    except Exception:
        return False
    if not isinstance(payload, dict) or payload.get("scope") != _FEED_SCOPE:
        return False
    exp = payload.get("exp")
    if not isinstance(exp, int):
        return False
    epoch = int(now if now is not None else time.time())
    return exp > epoch


def token_expiry_iso(token: str, *, now: Optional[int] = None) -> Optional[str]:
    """Expiry of a minted token as an ISO-8601 UTC string, or None if the
    token does not parse (used only for the mint response; verification
    never trusts this — it re-checks ``exp`` itself)."""
    try:
        encoded = token.split(".")[1]
        payload = json.loads(_b64url_decode(encoded).decode("utf-8"))
        exp = payload["exp"]
    except Exception:
        return None
    return datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()


_bearer_scheme = HTTPBearer(auto_error=False)


async def get_feed_principal(
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    api_key: Optional[str] = Header(default=None, alias=settings.API_KEY_HEADER),
    feed_token: Optional[str] = Query(default=None),
) -> TokenPayload:
    """Auth for ``GET /video/feed/{slot}`` ONLY (Phase 32, K-C1).

    Acceptance order:
      1. Full auth (Bearer JWT / X-API-Key, kill-switch-checked) — the H3
         behaviour, unchanged.
      2. A valid signed feed token (``?feed_token=``) — the headless /
         ``<img>`` path, which cannot send headers.
      3. Otherwise 401 — an open browser request to the feed STILL fails,
         exactly as H3 requires.

    This dependency must NOT be wired onto any other route: a feed token
    is not a general credential.
    """
    principal = await authenticate_checked(bearer, api_key)
    if principal is not None:
        return principal
    if verify_feed_token(feed_token):
        return FEED_TOKEN_PRINCIPAL
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=(
            "Not authenticated: provide a valid Bearer <redacted> "
            "or X-API-Key, or a short-lived ?feed_token= minted by an "
            "operator via POST /api/v1/video/feed-token."
        ),
        headers={"WWW-Authenticate": "Bearer"},
    )
