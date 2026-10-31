# backend/tests/unit/test_phase35_w2_auth_controls.py
"""Phase 35 Worker 2 — items 3 + 4 + 11 ONLY.

  * M-3 account lockout (``app/main.py`` ``_authenticate_db_user``):
    ``locked_until`` was read but never set (verification report §3,
    STRUCK/dead code). Now: 5 consecutive failed passwords lock the
    account for 15 minutes; the correct password is rejected while
    locked; the lock expires and login works again; a DB failure
    denies the login instead of crashing or granting it.
  * M-4 API-key revocation without restart (``app/api/deps.py``):
    ``ADMIN_API_KEYS`` was a static list and ``revoke_key`` honestly
    demanded a restart. Now: ``revoke_api_key`` records the key's
    SHA-256 digest in an in-memory registry consulted by
    ``_verify_api_key`` — the choke point for REST and WebSocket auth —
    so a revoked key is 401 on the next request, an unaffected key
    keeps working, and no raw key value is ever stored. HONEST LIMIT
    pinned by a test: the registry is in-memory, so a restart
    resurrects a revoked key unless it was also removed from
    ADMIN_API_KEYS.
  * M-11 rate-limit gaps (``app/core/rate_limit_middleware.py``):
    feed-token mint, sensitive write prefixes, a GET feed/admin rule
    and WS handshake limits are added; Redis-down still limits
    (per-process fallback) and a failed limit check now denies
    (fail-closed) instead of allowing.

No docs/ledger file is written here (the coordinator owns it). No
secrets: all key/password values below are test fixtures only.
"""

from __future__ import annotations

import sys
import types
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect
from sqlalchemy import JSON, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# --- fpdf stub (same pattern as the Phase 32 tests) ----------------------
# app.main pulls in the dossiers router, which imports fpdf2. fpdf2 is
# not installed in this environment and PDF generation is not exercised
# here, so a minimal stub is installed before importing app.main. This
# stubs an optional dependency, not product behaviour.
_fpdf_stub = types.ModuleType("fpdf")
_fpdf_stub.__path__ = []


class _FPDF:
    def __init__(self, *args, **kwargs):
        pass


class _MethodReturnValue(Enum):
    NONE = 0


class _XPos(Enum):
    LEFT = 0
    RIGHT = 1
    LMARGIN = 2


class _YPos(Enum):
    TOP = 0
    NEXT = 1


_fpdf_stub.FPDF = _FPDF
_enums_stub = types.ModuleType("fpdf.enums")
_enums_stub.MethodReturnValue = _MethodReturnValue
_enums_stub.XPos = _XPos
_enums_stub.YPos = _YPos
sys.modules.setdefault("fpdf", _fpdf_stub)
sys.modules.setdefault("fpdf.enums", _enums_stub)

import app.main as main_module  # noqa: E402
from app.api import deps as deps_module  # noqa: E402
from app.api.deps import (  # noqa: E402
    authenticate_checked,
    authenticate_websocket,
    clear_revoked_api_keys_for_tests,
    is_api_key_revoked,
    revoke_api_key,
)
from app.core import rate_limit_middleware as rlm  # noqa: E402
from app.core.rate_limit_middleware import RateLimitMiddleware, _match_rule, check_rate_limit  # noqa: E402
from app.core.security import security_manager  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.models.user import User  # noqa: E402

pytestmark = pytest.mark.unit

_CORRECT_PASSWORD = "phase35-correct-password"
_WRONG_PASSWORD = "phase35-wrong-password"


def _sqlite_friendly_metadata():
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, postgresql.JSONB):
                column.type = JSON()


def _as_utc(value):
    """Normalise a possibly-naive SQLite datetime to aware UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


# ══════════════════════════════════════════════════════════════════════
# ITEM 3 — account lockout
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture()
async def lockout_db(tmp_path, monkeypatch):
    """File-SQLite engine with one DB user, wired into main.py's
    ``get_db_context`` (the session source ``_authenticate_db_user``
    actually uses). Returns (maker, username)."""
    _sqlite_friendly_metadata()
    db_path = tmp_path / "p35_lockout.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    username = f"lockout_{uuid.uuid4().hex[:8]}"
    async with maker() as session:
        session.add(
            User(
                email=f"{username}@example.invalid",
                username=username,
                hashed_password=security_manager.hash_password(_CORRECT_PASSWORD),
                full_name="Lockout Test User",
                role="viewer",
                is_active=True,
                is_superuser=False,
                is_verified=True,
            )
        )
        await session.commit()

    @asynccontextmanager
    async def _test_db_context():
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(main_module, "get_db_context", _test_db_context)
    try:
        yield maker, username
    finally:
        await engine.dispose()


async def _read_user(maker, username):
    async with maker() as session:
        user = (
            await session.execute(select(User).where(User.username == username))
        ).scalars().first()
        return user.failed_login_attempts, _as_utc(user.locked_until)


async def _set_lock(maker, username, *, attempts, locked_until):
    async with maker() as session:
        user = (
            await session.execute(select(User).where(User.username == username))
        ).scalars().first()
        user.failed_login_attempts = attempts
        user.locked_until = locked_until
        await session.commit()


async def test_lockout_constants_are_the_documented_values():
    assert main_module.LOCKOUT_THRESHOLD == 5
    assert main_module.LOCKOUT_DURATION_SECONDS == 900


async def test_below_threshold_not_locked(lockout_db):
    maker, username = lockout_db
    for _ in range(main_module.LOCKOUT_THRESHOLD - 1):
        assert await main_module._authenticate_db_user(username, _WRONG_PASSWORD) is None
    attempts, locked_until = await _read_user(maker, username)
    assert attempts == main_module.LOCKOUT_THRESHOLD - 1
    assert locked_until is None
    # Correct password still works and resets the counter.
    assert await main_module._authenticate_db_user(username, _CORRECT_PASSWORD) is not None
    attempts, locked_until = await _read_user(maker, username)
    assert attempts == 0
    assert locked_until is None


async def test_threshold_locks_and_correct_password_rejected_while_locked(lockout_db):
    maker, username = lockout_db
    for _ in range(main_module.LOCKOUT_THRESHOLD):
        assert await main_module._authenticate_db_user(username, _WRONG_PASSWORD) is None
    attempts, locked_until = await _read_user(maker, username)
    assert attempts == main_module.LOCKOUT_THRESHOLD
    assert locked_until is not None
    remaining = (locked_until - datetime.now(timezone.utc)).total_seconds()
    assert 800 < remaining <= 900, f"lock remaining {remaining}s, expected ~900s"

    # Correct password is rejected while locked, and the lock is NOT
    # extended by further attempts (fixed expiry, no indefinite lock).
    assert await main_module._authenticate_db_user(username, _CORRECT_PASSWORD) is None
    attempts2, locked_until2 = await _read_user(maker, username)
    assert attempts2 == main_module.LOCKOUT_THRESHOLD
    assert abs((locked_until2 - locked_until).total_seconds()) < 1
    assert await main_module._authenticate_db_user(username, _WRONG_PASSWORD) is None
    _, locked_until3 = await _read_user(maker, username)
    assert abs((locked_until3 - locked_until).total_seconds()) < 1


async def test_lock_expiry_allows_login_again_and_restarts_count(lockout_db):
    maker, username = lockout_db
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    await _set_lock(
        maker, username, attempts=main_module.LOCKOUT_THRESHOLD, locked_until=past
    )
    # Expired lock: correct password works, counter and lock cleared.
    assert await main_module._authenticate_db_user(username, _CORRECT_PASSWORD) is not None
    attempts, locked_until = await _read_user(maker, username)
    assert attempts == 0
    assert locked_until is None

    # And one typo after expiry does NOT instantly re-lock: the count
    # restarts from zero.
    await _set_lock(
        maker, username, attempts=main_module.LOCKOUT_THRESHOLD, locked_until=past
    )
    assert await main_module._authenticate_db_user(username, _WRONG_PASSWORD) is None
    attempts, locked_until = await _read_user(maker, username)
    assert attempts == 1
    assert locked_until is None


async def test_db_failure_fails_closed_without_crash(monkeypatch):
    """A DB error while checking credentials denies the login (None)
    and never raises out of _authenticate_db_user."""

    @asynccontextmanager
    async def _boom_context():
        raise RuntimeError("simulated database outage")
        yield  # pragma: no cover - unreachable

    monkeypatch.setattr(main_module, "get_db_context", _boom_context)
    # Correct password against a dead DB must still be denied.
    assert await main_module._authenticate_db_user("anyone", _CORRECT_PASSWORD) is None


async def test_unknown_and_inactive_users_never_lock(lockout_db):
    maker, username = lockout_db
    assert await main_module._authenticate_db_user("no-such-user", _WRONG_PASSWORD) is None
    assert await main_module._authenticate_db_user(username, _CORRECT_PASSWORD) is not None
    async with maker() as session:
        user = (
            await session.execute(select(User).where(User.username == username))
        ).scalars().first()
        user.is_active = False
        await session.commit()
    assert await main_module._authenticate_db_user(username, _CORRECT_PASSWORD) is None
    assert await main_module._authenticate_db_user(username, _WRONG_PASSWORD) is None
    attempts, locked_until = await _read_user(maker, username)
    assert attempts == 0
    assert locked_until is None


# ══════════════════════════════════════════════════════════════════════
# ITEM 4 — API-key revocation without restart
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _clean_revocation_registry():
    clear_revoked_api_keys_for_tests()
    yield
    clear_revoked_api_keys_for_tests()


def _set_api_keys(monkeypatch, keys):
    monkeypatch.setattr(deps_module.settings, "ADMIN_API_KEYS", list(keys))


def test_revoke_api_key_records_digest_only_and_rejects_key(monkeypatch):
    _set_api_keys(monkeypatch, ["p35-old-key", "p35-other-key"])
    from app.api.deps import _verify_api_key

    assert _verify_api_key("p35-old-key") is True
    assert revoke_api_key("p35-old-key") is True  # was a configured key
    assert _verify_api_key("p35-old-key") is False
    assert is_api_key_revoked("p35-old-key") is True
    # No raw key value is retained: the registry holds a 64-hex digest.
    assert "p35-old-key" not in deps_module._REVOKED_API_KEY_HASHES
    assert len(deps_module._REVOKED_API_KEY_HASHES) == 1
    (digest,) = deps_module._REVOKED_API_KEY_HASHES
    assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
    # The unaffected key keeps working.
    assert _verify_api_key("p35-other-key") is True
    # Revoking an unconfigured key reports False (and still records it).
    assert revoke_api_key("p35-never-configured") is False
    assert _verify_api_key("p35-never-configured") is False
    assert revoke_api_key("") is False


async def test_revoked_key_401_rest_and_ws_unaffected_key_works(monkeypatch):
    _set_api_keys(monkeypatch, ["p35-old-key", "p35-other-key"])
    assert await authenticate_checked(bearer=None, api_key="p35-old-key") is not None
    assert await authenticate_websocket(None, token="p35-old-key") is not None

    revoke_api_key("p35-old-key")

    # REST: revoked key resolves to no principal (caller raises 401).
    assert await authenticate_checked(bearer=None, api_key="p35-old-key") is None
    # WebSocket: revoked key resolves to no principal either.
    assert await authenticate_websocket(None, token="p35-old-key") is None
    # Unaffected key still authenticates on both paths.
    assert await authenticate_checked(bearer=None, api_key="p35-other-key") is not None
    assert await authenticate_websocket(None, token="p35-other-key") is not None


async def test_revocation_does_not_revoke_jwt_path(monkeypatch):
    _set_api_keys(monkeypatch, ["p35-old-key"])
    from fastapi.security import HTTPAuthorizationCredentials

    token = security_manager.create_access_token(
        user_id="operator", username="operator", is_superuser=True,
        scopes=["admin"], role="admin",
    )
    bearer = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    assert await authenticate_checked(bearer=bearer, api_key=None) is not None
    revoke_api_key("p35-old-key")
    # Revoking a static key must not invalidate JWT auth.
    assert await authenticate_checked(bearer=bearer, api_key=None) is not None


def test_revocation_is_in_memory_restart_resurrects_unless_removed_from_env(
    monkeypatch,
):
    """HONEST LIMIT, pinned: clearing the registry (what a process
    restart does) brings the key back while it is still in
    ADMIN_API_KEYS. Permanent revocation = revoke in-process AND
    remove the key from ADMIN_API_KEYS."""
    _set_api_keys(monkeypatch, ["p35-old-key"])
    from app.api.deps import _verify_api_key

    revoke_api_key("p35-old-key")
    assert _verify_api_key("p35-old-key") is False
    clear_revoked_api_keys_for_tests()  # simulates a fresh process
    assert _verify_api_key("p35-old-key") is True
    # Removing the key from the allowlist is what survives a restart.
    _set_api_keys(monkeypatch, [])
    assert _verify_api_key("p35-old-key") is False


# ══════════════════════════════════════════════════════════════════════
# ITEM 11 — rate-limit gaps
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _clean_in_memory_limiter():
    security_manager._rate_limits.clear()
    yield
    security_manager._rate_limits.clear()


def test_match_rule_coverage_and_exclusions():
    # Pre-existing rules, unchanged.
    assert _match_rule("POST", "/api/v1/auth/token") == (5, "auth_token")
    assert _match_rule("POST", "/api/v1/broadcast/switch") == (60, "broadcast_write")
    assert _match_rule("POST", "/api/v1/scrims/start") == (60, "scrims_write")
    assert _match_rule("POST", "/api/v1/content/generate-recap") == (60, "content_write")
    # Feed-token mint: exact rule wins over the /video/ prefix rule.
    assert _match_rule("POST", "/api/v1/video/feed-token") == (30, "feed_token_mint")
    # Sensitive writes, including non-POST mutating methods.
    assert _match_rule("POST", "/api/v1/video/capture/start") == (60, "video_write")
    assert _match_rule("POST", "/api/v1/director/mode") == (60, "director_write")
    assert _match_rule("POST", "/api/v1/stream/start") == (60, "stream_write")
    assert _match_rule("POST", "/api/v1/users") == (30, "users_write")
    assert _match_rule("PATCH", "/api/v1/users/abc") == (30, "users_write")
    assert _match_rule("DELETE", "/api/v1/users/abc") == (30, "users_write")
    assert _match_rule("POST", "/api/v1/observer-pilot/kill-switch") == (60, "observer_pilot_write")
    assert _match_rule("POST", "/api/v1/director-learning/analyze") == (60, "director_learning_write")
    assert _match_rule("POST", "/api/v1/dossiers/ingest/fight") == (60, "dossiers_ingest")
    assert _match_rule("POST", "/api/v1/replay/recorder/start") == (60, "replay_write")
    # Safe GET rules.
    assert _match_rule("GET", "/api/v1/video/feed/1") == (120, "video_feed_get")
    assert _match_rule("GET", "/api/v1/admin/system") == (120, "admin_get")
    # Deliberate exclusions: UI-polled reads and GETs on write prefixes.
    assert _match_rule("GET", "/api/v1/director/state") is None
    assert _match_rule("GET", "/api/v1/broadcast/status") is None
    assert _match_rule("GET", "/api/v1/system/status") is None
    assert _match_rule("GET", "/api/v1/video/status") is None
    assert _match_rule("GET", "/api/v1/users") is None
    assert _match_rule("POST", "/api/v1/stories/generate") is None  # documented remainder
    assert _match_rule("GET", "/api/v1/video/feed-token") is None


async def test_check_rate_limit_redis_down_still_limits(monkeypatch):
    """Redis raising → per-process fallback still enforces the limit."""

    async def _boom(key, max_requests, window_seconds):
        raise ConnectionError("simulated Redis outage")

    monkeypatch.setattr(rlm, "_check_rate_limit_redis", _boom)
    key = f"p35-redis-down-{uuid.uuid4().hex[:8]}"
    assert await check_rate_limit(key, max_requests=2, window_seconds=60) is True
    assert await check_rate_limit(key, max_requests=2, window_seconds=60) is True
    assert await check_rate_limit(key, max_requests=2, window_seconds=60) is False


def _build_limited_app():
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.post("/api/v1/video/feed-token")
    async def _mint():
        return {"ok": True}

    @app.post("/api/v1/director/mode")
    async def _mode():
        return {"ok": True}

    @app.get("/api/v1/director/state")
    async def _state():
        return {"ok": True}

    @app.post("/api/v1/auth/token")
    async def _login():
        return {"ok": True}

    @app.get("/api/v1/video/feed/{slot}")
    async def _feed(slot: int):
        return {"ok": True, "slot": slot}

    @app.websocket("/ws/live")
    async def _ws_live(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass

    @app.websocket("/ws/other")
    async def _ws_other(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass

    return app


@pytest.fixture()
def redis_down_app(monkeypatch):
    """The test app with the Redis check forced to fail, so every
    limit decision runs through the in-memory fallback deterministically
    (no dependence on whether a Redis happens to be reachable)."""

    async def _boom(key, max_requests, window_seconds):
        raise ConnectionError("simulated Redis outage")

    monkeypatch.setattr(rlm, "_check_rate_limit_redis", _boom)
    return _build_limited_app()


def test_middleware_feed_token_mint_limit_and_normal_use(redis_down_app):
    client = TestClient(redis_down_app)
    # Sane operator use: 30 mints in a minute all succeed.
    for _ in range(30):
        assert client.post("/api/v1/video/feed-token").status_code == 200
    # 31st is throttled, with Retry-After and the mint rule named.
    resp = client.post("/api/v1/video/feed-token")
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "60"
    assert resp.json()["rule"] == "feed_token_mint"
    # A different rule has its own budget: director writes unaffected.
    assert client.post("/api/v1/director/mode").status_code == 200
    # Unthrottled polled reads are unaffected no matter how often.
    for _ in range(5):
        assert client.get("/api/v1/director/state").status_code == 200


def test_middleware_redis_down_still_429s_at_middleware_level(redis_down_app):
    client = TestClient(redis_down_app)
    for _ in range(5):
        assert client.post("/api/v1/auth/token").status_code == 200
    assert client.post("/api/v1/auth/token").status_code == 429


def test_middleware_check_failure_fails_closed(monkeypatch):
    """Both backends unusable → the throttled request is denied (429),
    not admitted."""

    async def _boom(key, max_requests, window_seconds=60):
        raise RuntimeError("limiter itself is broken")

    monkeypatch.setattr(rlm, "check_rate_limit", _boom)
    client = TestClient(_build_limited_app())
    assert client.post("/api/v1/video/feed-token").status_code == 429
    # Unthrottled paths are unaffected by a limiter failure.
    assert client.get("/api/v1/director/state").status_code == 200


def _connect_once(client, path):
    """True when the WS handshake succeeds; raises WebSocketDisconnect
    (asserting the close code) when the middleware refuses it."""
    try:
        with client.websocket_connect(path):
            return True
    except WebSocketDisconnect as exc:
        return exc


def test_ws_handshake_limit_and_unlisted_path(redis_down_app):
    client = TestClient(redis_down_app)
    for _ in range(60):
        assert _connect_once(client, "/ws/live") is True
    rejected = _connect_once(client, "/ws/live")
    assert isinstance(rejected, WebSocketDisconnect)
    assert rejected.code == 1008
    # An unlisted WS path is not throttled by this middleware.
    assert _connect_once(client, "/ws/other") is True


def test_ws_check_failure_fails_closed(monkeypatch):
    async def _boom(key, max_requests, window_seconds=60):
        raise RuntimeError("limiter itself is broken")

    monkeypatch.setattr(rlm, "check_rate_limit", _boom)
    client = TestClient(_build_limited_app())
    rejected = _connect_once(client, "/ws/live")
    assert isinstance(rejected, WebSocketDisconnect)
    assert rejected.code == 1011
