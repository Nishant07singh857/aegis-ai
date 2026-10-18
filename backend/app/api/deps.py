# backend/app/api/deps.py
"""
AEGIS AI — shared FastAPI dependencies (authentication / authorization).

Phase 1 auth model (honest and minimal):
  * There is NO User/ApiKey table in the reachable DB models package
    (`app/db/models/`), and we deliberately do NOT invent a DB migration here.
  * The control plane therefore authenticates against a single configured
    operator credential plus an optional static API-key allowlist, both from
    settings (see `app/config.py` Security section):
      - ADMIN_USERNAME / ADMIN_PASSWORD → POST /api/v1/auth/token login,
        returns a JWT (created by SecurityManager.create_access_token,
        HS256, ACCESS_TOKEN_EXPIRE_MINUTES expiry).
      - ADMIN_API_KEYS → headless/service auth via the X-API-Key header
        (settings.API_KEY_HEADER). Compared with secrets.compare_digest.
        Static-allowlist keys are dev/demo grade; a hashed store belongs in
        the DB-backed auth that replaces this in Phase 2.

Dependencies:
  * get_current_user  — accepts a valid JWT (Authorization: Bearer <token>)
    OR a valid X-API-Key. Returns a TokenPayload for JWT auth, or a synthetic
    admin TokenPayload for API-key auth. Raises 401 when neither is valid,
    or when no auth is configured at all (closed by default).
  * require_admin     — currently identical to get_current_user (every
    authenticated principal IS the operator). Kept as a separate dependency
    so future user roles can tighten it without touching every route.
  * get_optional_user — like get_current_user but returns None instead of
    raising; used by the WebSocket control channel where auth travels in the
    `?token=` query param (browsers can't set WS headers).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ENDPOINT ACCESS MAP (backend/app/main.py)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PUBLIC (no auth) — read-only demo surface:
  GET  /                                   welcome payload
  GET  /api/v1/system/status               health (public)
  GET  /api/v1/teams, /api/v1/teams/{id}   match state
  GET  /api/v1/players, /api/v1/players/mvp match state
  GET  /api/v1/matches, /api/v1/matches/live
  GET  /api/v1/tournaments
  GET  /api/v1/analytics/standings, /kill_feed, /kill-feed, /summary
  GET  /api/v1/broadcast/status            director state (read-only)
  GET  /api/v1/commentary/latest
  GET  /api/v1/highlights
  POST /api/v1/auth/token                  login (rate-limited by design)
  WS   /ws/live, /ws/monitor               passive subscription + ping/pong
                                           (receiving state pushes stays open)

PROTECTED (require_admin) — state-mutating, admin, broadcast control:
  POST /api/v1/broadcast/switch            change live POV
  POST /api/v1/broadcast/replay            trigger replay
  POST /api/v1/broadcast/production-mode   change broadcast modes
  GET  /api/v1/admin/system                operator system view
  GET  /api/v1/admin/agents, /api/v1/agents agent inventory
  GET  /api/v1/devices                     GPU node inventory
  GET  /api/v1/agent-assignments
  GET  /api/v1/agent-logs
  POST /api/v1/scrims/start                starts a scrim (mutates state)
  POST /api/v1/content/generate-recap      queues a render job (mutates state)

PROTECTED (require_operator — operator or admin) — broadcast control:
  POST /api/v1/stream/start, /stream/stop  spend CPU, broadcast to the world
  POST /api/v1/director/mode               auto<->manual director switching
  POST /api/v1/director/scene              manual-mode scene select
                                           (409 while director is in AUTO)
  POST /api/v1/director/overlays           manual-mode overlay toggles
  POST /api/v1/director/lower-third        manual-mode lower third

AUTHENTICATED (get_current_user — viewer included) — read-only:
  GET  /api/v1/director/state              director state snapshot
  GET  /api/v1/stream/status               stream state (no key material)

PROTECTED (WS control channel) — every mutating action dispatched through
/ws/live's handle_action() requires an authenticated ?token= (JWT or API key)
whose role is operator or admin (mirrors the REST require_operator gates —
viewers' tokens only unlock the read-only feeds):
  assign_agent, lock_pov, start_scrim, generate_recap, restart_node,
  toggle_node, toggle_autopost, upload_sponsor, revoke_key, stop_training,
  post_now, create_tournament, clip_highlight, generate_all_highlights,
  toggle_broadcaster_seat, test_webhook, test_post.
Unauthenticated or insufficient-role action frames get an
{"status": "unauthorized"} ack and are dropped. Notably `revoke_key` no
longer leaks a fresh key to anonymous callers, and `restart_node` /
`stop_training` can no longer be triggered by anyone who can reach the port.
"""

import secrets
from typing import Optional

from fastapi import Depends, Header, HTTPException, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import settings
from ..core.security import security_manager, TokenPayload

_bearer_scheme = HTTPBearer(auto_error=False)
_API_KEY_ALIAS = settings.API_KEY_HEADER  # "X-API-Key" — resolved at import

ADMIN_PRINCIPAL = TokenPayload(
    user_id="operator",
    username="operator",
    is_superuser=True,
    scopes=["admin"],
    role="admin",
)


def _verify_api_key(raw_key: str) -> bool:
    """Constant-time check of a raw key against the static allowlist."""
    if not raw_key or not settings.ADMIN_API_KEYS:
        return False
    return any(
        secrets.compare_digest(raw_key, allowed)
        for allowed in settings.ADMIN_API_KEYS
    )


def _auth_is_configured() -> bool:
    return bool(settings.ADMIN_PASSWORD) or bool(settings.ADMIN_API_KEYS)


def authenticate_token_or_key(
    bearer: Optional[HTTPAuthorizationCredentials],
    api_key: Optional[str],
) -> Optional[TokenPayload]:
    """
    Shared core: validate a Bearer JWT or an X-API-Key.

    Returns a TokenPayload on success, None on failure (no exception).
    """
    if bearer and bearer.credentials:
        payload = security_manager.decode_token(bearer.credentials)
        if payload and payload.user_id:
            return payload
    if api_key and _verify_api_key(api_key):
        return ADMIN_PRINCIPAL
    return None


async def get_optional_user(
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    api_key: Optional[str] = Header(default=None, alias=_API_KEY_ALIAS),
) -> Optional[TokenPayload]:
    """Return the authenticated principal, or None. Never raises."""
    return authenticate_token_or_key(bearer, api_key)


async def get_current_user(
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    api_key: Optional[str] = Header(default=None, alias=_API_KEY_ALIAS),
) -> TokenPayload:
    """
    Require authentication: a valid JWT (Authorization: Bearer <token>,
    obtained from POST /api/v1/auth/token) OR a valid X-API-Key from the
    static allowlist. Raises 401 when neither is valid, or when no auth is
    configured at all (closed by default).
    """
    user = authenticate_token_or_key(bearer, api_key)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Not authenticated: provide a valid Bearer JWT "
                "(POST /api/v1/auth/token) or X-API-Key."
                if _auth_is_configured()
                else "Authentication is not configured on this server "
                "(set ADMIN_PASSWORD and/or ADMIN_API_KEYS); access denied."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PHASE 6 — ROLE-BASED ACCESS CONTROL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Roles: admin | operator | viewer (see app.db.models.enums.UserRole).
# Hierarchy: admin ⊇ operator ⊇ viewer — require_operator() admits admins
# too, because admins can do everything operators can.
#
# require_admin is bound at the end of this section as require_role("admin")
# (replacing the Phase 1 definition, which was equivalent to
# get_current_user because every authenticated principal was the operator).


def principal_role(user: TokenPayload) -> str:
    """
    Resolve the effective role of an authenticated principal.

    The JWT carries an explicit ``role`` claim (Phase 6). Principals built
    without one (the static X-API-Key principal, hand-built TokenPayloads)
    fall back to the pre-roles rule: superuser/"admin" scope → admin.
    Unknown values degrade to "viewer" (least privilege).
    """
    role = (user.role or "").strip().lower()
    if role in ("admin", "operator", "viewer"):
        return role
    if user.is_superuser or "admin" in (user.scopes or []):
        return "admin"
    if "operator" in (user.scopes or []):
        return "operator"
    return "viewer"


def require_role(*roles: str):
    """
    Dependency factory: require the principal's role to be one of ``roles``.

    Usage: ``user: TokenPayload = Depends(require_role("admin", "operator"))``.
    Raises 401 via get_current_user when unauthenticated, 403 when the role
    is insufficient. Role names are case-insensitive.
    """
    allowed = {r.strip().lower() for r in roles}
    unknown = allowed - {"admin", "operator", "viewer"}
    if unknown:
        raise ValueError(
            f"Unknown role(s) {sorted(unknown)}: use admin/operator/viewer."
        )

    async def _require_role(
        user: TokenPayload = Depends(get_current_user),
    ) -> TokenPayload:
        if principal_role(user) not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Insufficient role: this endpoint requires "
                    f"{' or '.join(sorted(allowed))}."
                ),
            )
        return user

    return _require_role


#: Operator-or-above: admits "operator" AND "admin".
require_operator = require_role("admin", "operator")

#: Admin-only. Replaces the Phase 1 definition (which was equivalent to
#: get_current_user because every authenticated principal was the operator).
#: principal_role() keeps the configured operator credential and static
#: API keys resolving to "admin", so existing admin-gated routes behave
#: exactly as before for the operator.
require_admin = require_role("admin")


def authenticate_websocket(
    websocket: WebSocket,
    token: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Optional[TokenPayload]:
    """
    Authenticate a WebSocket control frame.

    Browsers cannot set Authorization headers on a WebSocket handshake, so the
    client passes `?token=<jwt-or-api-key>` on connect. Returns the principal
    or None; the caller decides whether the action requires it.
    """
    credential = token or api_key
    if not credential:
        return None
    payload = security_manager.decode_token(credential)
    if payload and payload.user_id:
        return payload
    if _verify_api_key(credential):
        return ADMIN_PRINCIPAL
    return None


def verify_admin_login(username: str, password: str) -> bool:
    """
    Check operator login credentials against settings.

    Returns False when password login is not configured (ADMIN_PASSWORD unset)
    — the login endpoint then reports 503 instead of letting anyone in.
    """
    if not settings.ADMIN_PASSWORD:
        return False
    expected_user = settings.ADMIN_USERNAME or ""
    expected_pw = settings.ADMIN_PASSWORD.get_secret_value()
    return secrets.compare_digest(username, expected_user) and secrets.compare_digest(
        password, expected_pw
    )
