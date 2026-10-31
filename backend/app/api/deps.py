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
  * Phase 35 (M-4): static keys CAN now be revoked at runtime, without a
    restart, through the in-memory revocation registry below
    (``revoke_api_key``). The registry is consulted by ``_verify_api_key``,
    the single choke point for the REST and WebSocket API-key paths, so a
    revoked key is rejected (401) on the very next request. HONEST LIMIT:
    the registry is IN-MEMORY ONLY — a process restart resurrects a
    revoked key unless the key has also been removed from ADMIN_API_KEYS
    (the environment/config source). Permanent revocation is therefore
    "revoke in-process AND remove from ADMIN_API_KEYS"; revoking
    in-process alone buys immediate protection for the running process
    only. No REST/WS endpoint wires ``revoke_api_key`` yet — the WS
    ``revoke_key`` action in ``app/main.py`` still reports the old
    restart-only message until it is rewired to call this registry.
    Revoked keys are stored as SHA-256 digests only, never as raw keys.

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
  POST /api/v1/director/mode               full_auto<->semi_auto<->manual switching
                                           (legacy "auto" normalised to "full_auto")
  POST /api/v1/director/scene              manual-mode scene select
                                           (409 while director is not in MANUAL)
  POST /api/v1/director/suggestions/{id}/approve   apply an AI suggestion
                                           (409 in FULL_AUTO)
  POST /api/v1/director/suggestions/{id}/reject    drop an AI suggestion
  POST /api/v1/director/overlays           manual-mode overlay toggles
  POST /api/v1/director/lower-third        manual-mode lower third

AUTHENTICATED (get_current_user — viewer included) — read-only:
  GET  /api/v1/director/state              director state snapshot
  GET  /api/v1/stream/status               stream state (no key material)

PROTECTED (WS control channel) — every mutating action dispatched through
/ws/live's handle_action() requires an authenticated ?token= (JWT or API key)
whose role is operator or admin (mirrors the REST require_operator gates —
viewers' tokens only unlock the read-only feeds). The token is re-validated
against the DB user's live is_active flag on EVERY action frame (Phase 32
K-H1 fix), so deactivating an account kills its live control channel on
the next action instead of at token expiry:
  assign_agent, lock_pov, start_scrim, generate_recap, restart_node,
  toggle_node, toggle_autopost, upload_sponsor, revoke_key, stop_training,
  post_now, create_tournament, clip_highlight, generate_all_highlights,
  toggle_broadcaster_seat, test_webhook, test_post.
Unauthenticated or insufficient-role action frames get an
{"status": "unauthorized"} ack and are dropped. Notably `revoke_key` no
longer leaks a fresh key to anonymous callers, and `restart_node` /
`stop_training` can no longer be triggered by anyone who can reach the port.
"""

import hashlib
import logging
import secrets
import threading
from typing import Optional

from fastapi import Depends, Header, HTTPException, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from ..config import settings
from ..core.security import VALID_ROLES, security_manager, TokenPayload
from ..db.base import AsyncSessionLocal
from ..db.models.enums import UserRole
from ..db.models.user import User

logger = logging.getLogger("aegis.api.deps")

_bearer_scheme = HTTPBearer(auto_error=False)
_API_KEY_ALIAS = settings.API_KEY_HEADER  # "X-API-Key" — resolved at import

ADMIN_PRINCIPAL = TokenPayload(
    user_id="operator",
    username="operator",
    is_superuser=True,
    scopes=["admin"],
    role="admin",
)


def _hash_api_key(raw_key: str) -> str:
    """SHA-256 digest of a raw API key (hex). Only digests are ever stored
    in the revocation registry; a raw key value is never retained."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


#: Phase 35 (M-4) — runtime revocation registry for static ADMIN_API_KEYS.
#: In-memory ONLY (see module docstring): a restart clears it, so a
#: revoked key comes back unless it was also removed from ADMIN_API_KEYS.
#: Membership is by digest, never by raw key value.
_REVOKED_API_KEY_HASHES: set = set()
_REVOKED_API_KEY_LOCK = threading.Lock()


def revoke_api_key(raw_key: str) -> bool:
    """Revoke a static API key for the running process, no restart needed.

    The key's SHA-256 digest is added to the revocation registry, and
    every subsequent ``_verify_api_key`` check rejects it (401 on REST,
    rejected principal on the WebSocket path). Returns True when the key
    matched a currently configured ADMIN_API_KEYS entry at revocation
    time, False otherwise (the digest is still recorded, so a key added
    to the allowlist later in this process lifetime stays revoked —
    there is deliberately no un-revoke: restoring a key requires a
    restart with the key present in ADMIN_API_KEYS and no revocation
    recorded in the fresh process).

    HONEST LIMIT: in-memory only. A restart resurrects the key unless
    it was also removed from ADMIN_API_KEYS. Permanent revocation is
    "revoke here AND remove from the environment/config source". The
    raw key is never stored or logged; only its digest is retained.
    """
    if not raw_key:
        return False
    digest = _hash_api_key(raw_key)
    was_configured = any(
        secrets.compare_digest(raw_key, allowed)
        for allowed in settings.ADMIN_API_KEYS
    )
    with _REVOKED_API_KEY_LOCK:
        _REVOKED_API_KEY_HASHES.add(digest)
    logger.warning(
        "API key revoked at runtime (digest recorded; in-memory only — "
        "remove the key from ADMIN_API_KEYS for permanent revocation)"
    )
    return was_configured


def is_api_key_revoked(raw_key: str) -> bool:
    """True when the key's digest is in the runtime revocation registry."""
    if not raw_key:
        return False
    return _hash_api_key(raw_key) in _REVOKED_API_KEY_HASHES


def clear_revoked_api_keys_for_tests() -> None:
    """Empty the runtime revocation registry. TEST SUPPORT ONLY — no
    production caller uses this; revocation is one-way in a running
    process (a restart is the restore path)."""
    with _REVOKED_API_KEY_LOCK:
        _REVOKED_API_KEY_HASHES.clear()


def _verify_api_key(raw_key: str) -> bool:
    """Constant-time check of a raw key against the static allowlist.

    A key in the Phase 35 runtime revocation registry is rejected even
    though it is still in ADMIN_API_KEYS (fail closed: revoked = 401).
    This is the single choke point for API-key auth on both REST
    (``authenticate_checked``) and WebSocket (``authenticate_websocket``),
    so revocation takes effect on every path on the next request.
    """
    if not raw_key or not settings.ADMIN_API_KEYS:
        return False
    if is_api_key_revoked(raw_key):
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

    NOTE (H2, Integrity Fix Pack): this sync core only verifies the token
    signature. The REST dependencies below (``get_current_user`` /
    ``get_optional_user``) and the WebSocket authenticator
    (``authenticate_websocket``) use ``authenticate_checked``-equivalent
    revocation checks instead, which additionally check JWT principals
    against the DB user's live ``is_active`` flag. This sync function
    remains only as the shared signature-verification step inside those
    checked wrappers.
    """
    if bearer and bearer.credentials:
        payload = security_manager.decode_token(bearer.credentials)
        if payload and payload.user_id:
            return payload
    if api_key and _verify_api_key(api_key):
        return ADMIN_PRINCIPAL
    return None


#: user_id of the configured operator principal (Phase 1 ADMIN_USERNAME /
#: ADMIN_PASSWORD login in app/main.py). That account is NOT a DB user —
#: it exists only in settings — so the DB kill-switch (which is scoped to
#: DB accounts, per-tournament) has no row to check for it. Disabling the
#: operator means unsetting ADMIN_PASSWORD, not a DB flag flip.
OPERATOR_USER_ID = "operator"


async def _db_user_is_active(user_id: str) -> Optional[bool]:
    """Return the DB user's live ``is_active`` flag, or None if there is
    no DB user row with that id."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User.is_active).where(User.id == user_id)
        )
        return result.scalar_one_or_none()


async def authenticate_checked(
    bearer: Optional[HTTPAuthorizationCredentials],
    api_key: Optional[str],
) -> Optional[TokenPayload]:
    """
    ``authenticate_token_or_key`` + H2 kill-switch revocation check.

    For JWT (Bearer) principals — the token path only; the X-API-Key path
    is deliberately untouched by the DB check and never DB-checked — the
    DB user row is loaded and:

      * ``is_active`` is False (e.g. after the Phase 16 kill switch,
        ``UserService.deactivate_tournament``) → rejected (None → 401);
      * no DB row exists for the token's user_id → rejected (None → 401);
      * the DB lookup itself fails → rejected (fail closed, logged);
      * the configured operator principal (user_id "operator", minted by
        the settings-credential login, not a DB account) → allowed, as it
        has no DB row by design.
    """
    payload = authenticate_token_or_key(bearer, api_key)
    if payload is None:
        return None
    if not (bearer and bearer.credentials):
        # X-API-Key path: no DB check by design, but _verify_api_key has
        # already consulted the Phase 35 runtime revocation registry, so
        # a revoked static key never reaches this return.
        return payload  # X-API-Key path: unchanged, no DB check.
    if payload.user_id == OPERATOR_USER_ID:
        return payload
    try:
        is_active = await _db_user_is_active(payload.user_id)
    except Exception:
        logger.warning(
            "is_active check failed for user_id=%s; failing closed",
            payload.user_id,
            exc_info=True,
        )
        return None
    if is_active is not True:
        logger.info(
            "Token rejected: DB user user_id=%s is inactive or missing",
            payload.user_id,
        )
        return None
    return payload


async def get_optional_user(
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    api_key: Optional[str] = Header(default=None, alias=_API_KEY_ALIAS),
) -> Optional[TokenPayload]:
    """Return the authenticated principal, or None. Never raises.

    JWT principals are revocation-checked (H2): a deactivated DB user's
    token resolves to None here, i.e. the caller is treated as anonymous.
    """
    return await authenticate_checked(bearer, api_key)


async def get_current_user(
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    api_key: Optional[str] = Header(default=None, alias=_API_KEY_ALIAS),
) -> TokenPayload:
    """
    Require authentication: a valid JWT (Authorization: Bearer <token>,
    obtained from POST /api/v1/auth/token) OR a valid X-API-Key from the
    static allowlist. Raises 401 when neither is valid, or when no auth is
    configured at all (closed by default).

    H2 (Integrity Fix Pack): JWT principals are additionally checked
    against the DB user's live ``is_active`` flag on every request, so
    deactivating an account (the Phase 16 kill switch) revokes its
    already-issued tokens immediately instead of at next login.
    """
    user = await authenticate_checked(bearer, api_key)
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
# PHASE 6 — ROLE-BASED ACCESS CONTROL (extended Phase 16)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Roles: admin | operator | director | spotter | observer | viewer
# (see app.db.models.enums.UserRole; app.core.security.VALID_ROLES is the
# derived canonical tuple — never hardcode the list here).
# Hierarchy: admin ⊇ operator ⊇ {director, spotter} (broadcast control)
#            ⊇ observer ⊇ viewer.
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
    if role in VALID_ROLES:
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
    is insufficient. Role names are case-insensitive; unknown names raise
    ValueError at wiring time.
    """
    allowed = {r.strip().lower() for r in roles}
    unknown = allowed - set(VALID_ROLES)
    if unknown:
        raise ValueError(
            f"Unknown role(s) {sorted(unknown)}: use {', '.join(VALID_ROLES)}."
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


#: Roles allowed on the broadcast-control plane: stream start/stop,
#: director console, replay/PiP/rundown control, observer-slot assignment
#: and the /ws/live control channel. admin ⊇ operator ⊇ {director, spotter}
#: per the UserRole hierarchy.
BROADCAST_CONTROL_ROLES = ("admin", "operator", "director", "spotter")

#: Broadcast-control-or-above: admits every broadcast-control role.
require_operator = require_role(*BROADCAST_CONTROL_ROLES)

#: Admin-only. Replaces the Phase 1 definition (which was equivalent to
#: get_current_user because every authenticated principal was the operator).
#: principal_role() keeps the configured operator credential and static
#: API keys resolving to "admin", so existing admin-gated routes behave
#: exactly as before for the operator.
require_admin = require_role("admin")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PHASE 16 — TEAM-SCOPED READS (observer role)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Observers carry a ``team_id`` JWT claim. On team-data endpoints (teams,
# players, analysis) an authenticated observer sees ONLY their own team:
# list endpoints are filtered to it, and requesting any other team's id
# (path or query param) is a 403. Non-observer principals — and anonymous
# callers on the still-public read endpoints — are unaffected (scope None).


async def get_current_team_id(
    user: Optional[TokenPayload] = Depends(get_optional_user),
) -> Optional[str]:
    """Return the authenticated principal's ``team_id`` JWT claim, or None.

    Never raises: anonymous callers simply have no team scope.
    """
    return user.team_id if user is not None else None


async def require_team_scope(
    user: Optional[TokenPayload] = Depends(get_optional_user),
) -> Optional[str]:
    """Team-scope dependency for team-data endpoints.

    Returns the observer's ``team_id`` when the caller is an authenticated
    observer, else None (no restriction — applies to admins, operators,
    directors, spotters, viewers and anonymous callers alike).

    Raises 403 when an observer's token carries no team assignment: an
    observer without a team cannot be scoped, so team-data reads are
    denied rather than silently widened.
    """
    if user is None or principal_role(user) != UserRole.OBSERVER.value:
        return None
    team_id = user.team_id
    if not team_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Observer account has no team assignment: team-data access denied.",
        )
    return team_id


def enforce_team_scope(requested_team_id: Optional[str], scope: Optional[str]) -> None:
    """403 when an observer asks for a team other than their own.

    ``scope`` is the value returned by ``require_team_scope()``. ``None``
    means "not an observer" — no restriction. A ``None`` requested id means
    "no specific team requested" — also allowed (routers then filter the
    list to the observer's team instead).
    """
    if scope is None or requested_team_id is None:
        return
    if str(requested_team_id) != str(scope):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Observers may only access their assigned team's data.",
        )


async def authenticate_websocket(
    websocket: WebSocket,
    token: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Optional[TokenPayload]:
    """
    Authenticate a WebSocket control frame.

    Browsers cannot set Authorization headers on a WebSocket handshake, so the
    client passes `?token=<jwt-or-api-key>` on connect. Returns the principal
    or None; the caller decides whether the action requires it.

    Phase 32 (K-H1 fix): this is async and applies the SAME kill-switch
    revocation check as the REST ``authenticate_checked`` path — a JWT
    principal whose DB user row is missing or has ``is_active`` False is
    rejected (None), and a DB lookup failure fails closed (None, logged).
    The configured operator principal (user_id "operator", no DB row by
    design) and the X-API-Key path are exempt from the DB check, exactly
    as on REST — the X-API-Key path IS still checked against the Phase 35
    runtime revocation registry inside ``_verify_api_key``.
    Callers MUST re-run this per control action, not once at connect:
    a token frozen at connect time would otherwise keep working until
    expiry after its account is deactivated (see ``websocket_live`` in
    app/main.py, which re-validates on every action frame).
    """
    credential = token or api_key
    if not credential:
        return None
    payload = security_manager.decode_token(credential)
    if payload and payload.user_id:
        if payload.user_id == OPERATOR_USER_ID:
            return payload
        try:
            is_active = await _db_user_is_active(payload.user_id)
        except Exception:
            logger.warning(
                "WS is_active check failed for user_id=%s; failing closed",
                payload.user_id,
                exc_info=True,
            )
            return None
        if is_active is not True:
            logger.info(
                "WS token rejected: DB user user_id=%s is inactive or missing",
                payload.user_id,
            )
            return None
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
