# backend/tests/unit/test_integrity_credentials_auth.py
"""Integrity Fix Pack — Worker A (Credentials & Auth) tests.

Covers, against docs/FULL_PROJECT_INSPECTION.md:
  * C1/H1: no committed credential defaults in app.config —
      (a) production Settings fail fast when SECRET_KEY and/or
          DATABASE_URL are unset;
      (a2) with SECRET_KEY unset, JWT sign/verify raise RuntimeError
          (no forgeable fallback key in any environment);
      (b) a JWT signed with the OLD committed default string is
          rejected by the app's configured key.
  * H2: kill-switch revocation — a DB user's already-issued JWT stops
    working (401) the moment deactivate_tournament flips is_active,
    while another tournament's active user keeps working, and the
    X-API-Key path is untouched.
  * H3: GET /video/feed/{slot} requires auth — 401 unauthenticated,
    200 with a valid credential (the stream generator is replaced with
    a finite stand-in so the test never hangs on the infinite MJPEG
    loop; what is under test is the auth wiring, not the streamer).

DB tests use their own in-memory SQLite engine (same pattern as
tests/unit/test_users.py) and point app.api.deps at it by monkeypatch.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import jwt as pyjwt
import pytest
import pytest_asyncio
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError
from sqlalchemy import JSON
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import deps
from app.api.deps import get_current_user
from app.api.v1.users.service import UserService
from app.config import Settings, settings
from app.core import security as security_module
from app.core.security import security_manager
from app.db.base import Base
from app.db.models.team import Team
from app.db.models.tournament import Tournament

pytestmark = pytest.mark.unit

#: The literal committed default removed by the C1 fix
#: (docs/FULL_PROJECT_INSPECTION.md, C1). Kept here ONLY as the forgery
#: input for test (b) — it must never be a working key again.
OLD_COMMITTED_DEFAULT = "change-me-in-production-please-very-long-secret"


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


# ── (a) production fail-fast ───────────────────────────────────────


def test_production_fail_fast_without_secret_key(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError) as exc:
        Settings(
            ENVIRONMENT="production",
            DATABASE_URL="postgresql+asyncpg://u:p@db.invalid:5432/aegis",
        )
    assert "SECRET_KEY" in str(exc.value)


def test_production_fail_fast_without_database_url(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError) as exc:
        Settings(ENVIRONMENT="production", SECRET_KEY="x" * 40)
    assert "DATABASE_URL" in str(exc.value)


def test_production_boots_when_both_are_set(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = Settings(
        ENVIRONMENT="production",
        SECRET_KEY="x" * 40,
        DATABASE_URL="postgresql+asyncpg://u:p@db.invalid:5432/aegis",
    )
    assert cfg.SECRET_KEY.get_secret_value() == "x" * 40
    assert cfg.DATABASE_URL.startswith("postgresql+asyncpg://")


def test_no_usable_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = Settings()  # development default environment
    assert cfg.DATABASE_URL == ""  # no embedded credentials remain
    with pytest.raises(RuntimeError):
        cfg.SECRET_KEY.get_secret_value()


# ── (a2) JWT functions fail loudly without a key ───────────────────


def test_jwt_sign_and_verify_raise_without_secret_key(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    dev_cfg = Settings()
    monkeypatch.setattr(security_module, "settings", dev_cfg)
    with pytest.raises(RuntimeError):
        security_manager.create_access_token(user_id="u-1", username="u1")
    with pytest.raises(RuntimeError):
        security_manager.decode_token("any.token.value")


# ── (b) the old committed default no longer verifies ──────────────


def test_token_signed_with_old_committed_default_is_rejected():
    assert settings.SECRET_KEY.get_secret_value() != OLD_COMMITTED_DEFAULT
    payload = {
        "sub": "attacker",
        "username": "attacker",
        "is_superuser": True,
        "scopes": ["admin"],
        "role": "admin",
        "type": "access",
        "exp": datetime.utcnow() + timedelta(hours=1),
        "iat": datetime.utcnow(),
    }
    forged = pyjwt.encode(payload, OLD_COMMITTED_DEFAULT, algorithm="HS256")
    assert security_manager.decode_token(forged) is None

    # Sanity: the rejection above is the key, not the payload shape —
    # a token signed with the configured (env-supplied) key decodes.
    legit = security_manager.create_access_token(
        user_id="u-legit", username="legit", role="viewer"
    )
    decoded = security_manager.decode_token(legit)
    assert decoded is not None and decoded.user_id == "u-legit"


# ── (c) kill-switch revokes live JWTs ──────────────────────────────


def _sqlite_friendly_metadata():
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, postgresql.JSONB):
                column.type = JSON()


@pytest_asyncio.fixture(scope="function")
async def engine():
    _sqlite_friendly_metadata()
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture()
async def db(engine):
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture()
async def auth_env(db, engine, monkeypatch):
    """Point app.api.deps' session factory at the test engine."""
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(deps, "AsyncSessionLocal", maker)
    return UserService(db)


async def _make_tournament(db, name):
    t = Tournament(name=name, slug=f"t-{uuid.uuid4().hex[:8]}")
    db.add(t)
    await db.flush()
    return t


async def test_kill_switch_revokes_live_token(auth_env, db):
    svc = auth_env
    cup_a = await _make_tournament(db, "Kill Cup A")
    cup_b = await _make_tournament(db, "Kill Cup B")
    team = Team(name="KS Team", is_active=True)
    db.add(team)
    await db.flush()
    await db.commit()

    resp_a, _ = await svc.create_user(
        username="ks_a1", password="long-enough", role="observer",
        tournament_id=cup_a.id, team_id=team.id,
    )
    resp_b, _ = await svc.create_user(
        username="ks_b1", password="long-enough", role="observer",
        tournament_id=cup_b.id, team_id=team.id,
    )
    await db.commit()

    token_a = security_manager.create_access_token(
        user_id=resp_a.id, username="ks_a1", role="observer",
        team_id=team.id, tournament_id=cup_a.id,
    )
    token_b = security_manager.create_access_token(
        user_id=resp_b.id, username="ks_b1", role="observer",
        team_id=team.id, tournament_id=cup_b.id,
    )

    # Before the kill switch: both tokens authenticate.
    assert (await get_current_user(bearer=_bearer(token_a), api_key=None)).username == "ks_a1"
    assert (await get_current_user(bearer=_bearer(token_b), api_key=None)).username == "ks_b1"

    # Kill switch for tournament A only.
    deactivated = await svc.deactivate_tournament(cup_a.id)
    assert deactivated == 1
    await db.commit()

    # The SAME, still-unexpired token A is now rejected with 401.
    with pytest.raises(HTTPException) as exc:
        await get_current_user(bearer=_bearer(token_a), api_key=None)
    assert exc.value.status_code == 401

    # Tournament B's active user is unaffected.
    assert (await get_current_user(bearer=_bearer(token_b), api_key=None)).username == "ks_b1"


async def test_token_for_missing_db_user_is_401(auth_env, db):
    ghost = security_manager.create_access_token(
        user_id=str(uuid.uuid4()), username="ghost", role="viewer"
    )
    with pytest.raises(HTTPException) as exc:
        await get_current_user(bearer=_bearer(ghost), api_key=None)
    assert exc.value.status_code == 401


async def test_operator_principal_and_api_key_paths_unchanged(auth_env, db, monkeypatch):
    # The configured operator login (user_id "operator") is NOT a DB
    # account — it keeps authenticating without a DB row.
    op_token = security_manager.create_access_token(
        user_id="operator", username="admin", is_superuser=True,
        scopes=["admin"], role="admin",
    )
    principal = await get_current_user(bearer=_bearer(op_token), api_key=None)
    assert principal.user_id == "operator"

    # X-API-Key path: no DB check, exactly as before the fix.
    monkeypatch.setattr(deps.settings, "ADMIN_API_KEYS", ["integrity-test-api-key"])
    principal = await get_current_user(bearer=None, api_key="integrity-test-api-key")
    assert principal is deps.ADMIN_PRINCIPAL
    with pytest.raises(HTTPException) as exc:
        await get_current_user(bearer=None, api_key="wrong-key")
    assert exc.value.status_code == 401


# ── (d) /video/feed/{slot} auth ────────────────────────────────────


def _video_app(monkeypatch):
    from fastapi import FastAPI

    from app.api.v1.video import router as video_router

    async def _finite_stream(slot_number, sim, fps, quality):
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\nFAKE\r\n"

    monkeypatch.setattr(video_router, "_mjpeg_program_stream", _finite_stream)
    app = FastAPI()
    app.include_router(video_router.router, prefix="/api/v1")
    return app


def test_video_feed_requires_auth(monkeypatch):
    from fastapi.testclient import TestClient

    client = TestClient(_video_app(monkeypatch), raise_server_exceptions=False)

    resp = client.get("/api/v1/video/feed/1")
    assert resp.status_code == 401

    token = security_manager.create_access_token(
        user_id="operator", username="admin", is_superuser=True,
        scopes=["admin"], role="admin",
    )
    resp = client.get(
        "/api/v1/video/feed/1", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("multipart/x-mixed-replace")
