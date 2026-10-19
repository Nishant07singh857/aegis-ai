# backend/tests/unit/test_users.py
"""Phase 16 tournament-scoped user management tests (no Postgres, no Redis).

Covers:
  * create + bcrypt round-trip (hash stored, plaintext never persisted)
  * auto-generated passwords returned exactly once (create response / bulk sheet)
  * admin accounts require an explicit password; observers require a team_id
  * username uniqueness (409), unknown team/tournament (404)
  * JWT carries team_id + tournament_id claims (login extension, token level)
  * observer team-scoping: require_team_scope / enforce_team_scope 403s
  * bulk-observers: obs01..obsNN, round-robin team assignment, slot wiring
  * kill switch: deactivate_tournament deactivates only that tournament's users
  * inactive accounts fail authentication
  * delete rules: never-logged-in deletes, logged-in -> 409
  * no plaintext password anywhere in stored rows
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import JSON, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import enforce_team_scope, require_team_scope
from app.api.v1.users.service import UserService
from app.core.security import TokenPayload, security_manager
from app.db.base import Base
from app.db.models.enums import UserRole
from app.db.models.observer_slot import ObserverSlot
from app.db.models.team import Team
from app.db.models.tournament import Tournament
from app.db.models.user import User

pytestmark = pytest.mark.unit


def _sqlite_friendly_metadata():
    """JSONB has no SQLite DDL rendering — swap it for generic JSON.

    Test-local only; production models are untouched.
    """
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
async def svc(db):
    return UserService(db)


async def _make_team(db, name, active=True):
    team = Team(name=name, is_active=active)
    db.add(team)
    await db.flush()
    return team


async def _make_tournament(db, name="Test Cup", slug=None):
    t = Tournament(name=name, slug=slug or f"t-{uuid.uuid4().hex[:8]}")
    db.add(t)
    await db.flush()
    return t


def _observer_payload(team_id=None):
    return TokenPayload(
        user_id="u1", username="obs01", role=UserRole.OBSERVER.value, team_id=team_id
    )


# ── create / bcrypt ────────────────────────────────────────────────


async def test_create_and_authenticate_bcrypt_roundtrip(svc, db):
    team = await _make_team(db, "Alpha")
    resp, _ = await svc.create_user(
        username="spotter1", password="s3cret-pass", role="spotter", team_id=team.id
    )
    assert resp.generated_password is None  # explicit password -> never echoed

    row = (await db.execute(select(User).where(User.username == "spotter1"))).scalars().one()
    assert row.hashed_password != "s3cret-pass"
    assert row.hashed_password.startswith("$2b$")
    assert security_manager.verify_password("s3cret-pass", row.hashed_password)

    authed = await svc.authenticate("spotter1", "s3cret-pass")
    assert authed is not None and authed.id == row.id
    assert await svc.authenticate("spotter1", "wrong-pass") is None
    assert await svc.authenticate("SPOTTER1", "s3cret-pass") is not None  # case-insensitive


async def test_auto_generated_password_returned_once(svc, db):
    resp, plaintext = await svc.create_user(username="dir1", password=None, role="director")
    assert plaintext and len(plaintext) >= 16
    assert resp.generated_password == plaintext

    row = (await db.execute(select(User).where(User.username == "dir1"))).scalars().one()
    assert row.hashed_password != plaintext
    assert security_manager.verify_password(plaintext, row.hashed_password)

    # A later read carries no password material at all.
    reread = await svc.user_response(row)
    assert "password" not in reread.model_dump()


async def test_admin_requires_explicit_password(svc):
    with pytest.raises(HTTPException) as exc:
        await svc.create_user(username="root2", password=None, role="admin")
    assert exc.value.status_code == 422


async def test_observer_requires_team_id(svc, db):
    with pytest.raises(HTTPException) as exc:
        await svc.create_user(username="obsx", password="long-enough", role="observer")
    assert exc.value.status_code == 422

    with pytest.raises(HTTPException) as exc:
        await svc.create_user(
            username="obsy", password="long-enough", role="observer", team_id="nope"
        )
    assert exc.value.status_code == 404


async def test_duplicate_username_409(svc):
    await svc.create_user(username="dupe", password="long-enough", role="viewer")
    with pytest.raises(HTTPException) as exc:
        await svc.create_user(username="DUPE", password="long-enough", role="viewer")
    assert exc.value.status_code == 409


async def test_unknown_role_rejected(svc):
    with pytest.raises(HTTPException) as exc:
        await svc.create_user(username="weird", password="long-enough", role="superuser")
    assert exc.value.status_code == 422


# ── JWT claims (login extension, token level) ──────────────────────


async def test_jwt_carries_team_and_tournament_claims():
    token = security_manager.create_access_token(
        user_id="u-1",
        username="obs01",
        role="observer",
        team_id="team-1",
        tournament_id="tourn-1",
    )
    payload = security_manager.decode_token(token)
    assert payload is not None
    assert payload.role == "observer"
    assert payload.team_id == "team-1"
    assert payload.tournament_id == "tourn-1"

    # Legacy tokens without the claims still decode (claims default None).
    old = security_manager.create_access_token(user_id="u-2", username="v", role="viewer")
    old_payload = security_manager.decode_token(old)
    assert old_payload.team_id is None and old_payload.tournament_id is None


# ── team scoping ───────────────────────────────────────────────────


async def test_require_team_scope_observer():
    assert await require_team_scope(user=_observer_payload("team-9")) == "team-9"


async def test_require_team_scope_non_observer_and_anonymous():
    viewer = TokenPayload(user_id="u2", username="v", role="viewer")
    assert await require_team_scope(user=viewer) is None
    assert await require_team_scope(user=None) is None
    admin = TokenPayload(user_id="u3", username="a", role="admin")
    assert await require_team_scope(user=admin) is None


async def test_require_team_scope_observer_without_team_403():
    with pytest.raises(HTTPException) as exc:
        await require_team_scope(user=_observer_payload(None))
    assert exc.value.status_code == 403


async def test_enforce_team_scope():
    enforce_team_scope("team-1", "team-1")  # own team: fine
    enforce_team_scope(None, "team-1")  # no team requested: fine
    enforce_team_scope("team-2", None)  # not an observer: fine
    with pytest.raises(HTTPException) as exc:
        enforce_team_scope("team-2", "team-1")
    assert exc.value.status_code == 403


# ── bulk observers ─────────────────────────────────────────────────


async def test_bulk_observers_assigns_teams_and_slots(svc, db):
    tourn = await _make_tournament(db, "Bulk Cup")
    t1 = await _make_team(db, "Bravo")
    t2 = await _make_team(db, "Alpha")
    t3 = await _make_team(db, "Charlie")
    await _make_team(db, "Retired", active=False)
    await db.commit()

    sheet = await svc.bulk_create_observers(tournament_id=tourn.id, count=4, created_by="admin")
    assert sheet.count == 4
    assert [c.username for c in sheet.data] == ["obs01", "obs02", "obs03", "obs04"]
    # Round-robin over ACTIVE teams in name order: Alpha, Bravo, Charlie, Alpha.
    assert [c.team_name for c in sheet.data] == ["Alpha", "Bravo", "Charlie", "Alpha"]
    assert [c.team_id for c in sheet.data] == [t2.id, t1.id, t3.id, t2.id]
    assert [c.slot_number for c in sheet.data] == [1, 2, 3, 4]
    assert len({c.password for c in sheet.data}) == 4  # unique passwords

    for cred in sheet.data:
        row = (
            await db.execute(select(User).where(User.username == cred.username))
        ).scalars().one()
        assert row.role == "observer"
        assert row.team_id == cred.team_id
        assert row.tournament_id == tourn.id
        assert row.is_active is True
        assert row.created_by == "admin"
        assert row.hashed_password != cred.password
        assert security_manager.verify_password(cred.password, row.hashed_password)

    # Slots 1..4 point at the same teams.
    slots = (
        await db.execute(select(ObserverSlot).order_by(ObserverSlot.slot_number))
    ).scalars().all()
    assert [s.team_id for s in slots[:4]] == [t2.id, t1.id, t3.id, t2.id]

    # Responses carry the derived slot_number, never hardcoded.
    responses, total = await svc.list_user_responses(role="observer", tournament_id=tourn.id)
    assert total == 4
    by_name = {r.username: r for r in responses}
    assert by_name["obs01"].slot_number == 1
    assert by_name["obs03"].slot_number == 3


async def test_bulk_observers_no_active_teams_422(svc, db):
    tourn = await _make_tournament(db, "Empty Cup")
    with pytest.raises(HTTPException) as exc:
        await svc.bulk_create_observers(tournament_id=tourn.id, count=2)
    assert exc.value.status_code == 422


async def test_bulk_observers_unknown_tournament_404(svc):
    with pytest.raises(HTTPException) as exc:
        await svc.bulk_create_observers(tournament_id="missing", count=2)
    assert exc.value.status_code == 404


# ── kill switch ────────────────────────────────────────────────────


async def test_kill_switch_deactivates_only_that_tournament(svc, db):
    cup_a = await _make_tournament(db, "Cup A")
    cup_b = await _make_tournament(db, "Cup B")
    team = await _make_team(db, "Solo")
    await db.commit()

    await svc.create_user(username="a1", password="long-enough", role="observer",
                          tournament_id=cup_a.id, team_id=team.id)
    await svc.create_user(username="a2", password="long-enough", role="spotter",
                          tournament_id=cup_a.id)
    await svc.create_user(username="b1", password="long-enough", role="observer",
                          tournament_id=cup_b.id, team_id=team.id)
    await svc.create_user(username="g1", password="long-enough", role="viewer")
    await db.commit()

    deactivated = await svc.deactivate_tournament(cup_a.id)
    assert deactivated == 2
    await db.commit()

    states = {
        u.username: u.is_active
        for u in (await db.execute(select(User))).scalars().all()
        if u.username in {"a1", "a2", "b1", "g1"}
    }
    assert states == {"a1": False, "a2": False, "b1": True, "g1": True}


# ── inactive login rejected / patch / delete / filters ────────────


async def test_inactive_login_rejected(svc, db):
    await svc.create_user(username="gone", password="long-enough", role="viewer")
    assert await svc.authenticate("gone", "long-enough") is not None

    row = (await db.execute(select(User).where(User.username == "gone"))).scalars().one()
    await svc.patch_user(row.id, is_active=False)
    await db.commit()
    assert await svc.authenticate("gone", "long-enough") is None


async def test_patch_password_reset(svc, db):
    resp, _ = await svc.create_user(username="reset1", password="old-password", role="viewer")
    await svc.patch_user(resp.id, password="brand-new-password")
    await db.commit()
    assert await svc.authenticate("reset1", "brand-new-password") is not None
    assert await svc.authenticate("reset1", "old-password") is None


async def test_delete_rules(svc, db):
    resp, _ = await svc.create_user(username="temp1", password="long-enough", role="viewer")
    await db.commit()
    await svc.delete_user(resp.id)  # never logged in: hard delete OK
    await db.commit()
    assert await svc.get_user(resp.id) is None

    resp2, _ = await svc.create_user(username="temp2", password="long-enough", role="viewer")
    row = await svc.get_user(resp2.id)
    row.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    with pytest.raises(HTTPException) as exc:
        await svc.delete_user(resp2.id)
    assert exc.value.status_code == 409

    with pytest.raises(HTTPException) as exc:
        await svc.delete_user("does-not-exist")
    assert exc.value.status_code == 404


async def test_list_filters(svc, db):
    cup = await _make_tournament(db, "Filter Cup")
    await svc.create_user(username="f_obs", password="long-enough", role="observer",
                          tournament_id=cup.id, team_id=(await _make_team(db, "FTeam")).id)
    await svc.create_user(username="f_view", password="long-enough", role="viewer")
    await db.commit()

    users, total = await svc.list_users(role="observer")
    assert total >= 1 and all(u.role == "observer" for u in users)

    users, total = await svc.list_users(tournament_id=cup.id)
    assert total == 1 and users[0].username == "f_obs"

    users, total = await svc.list_users(is_active=True)
    assert all(u.is_active for u in users)


async def test_no_plaintext_password_anywhere(svc, db):
    known = ["alpha-secret", "beta-secret"]
    team = await _make_team(db, "Plain")
    await svc.create_user(username="pl1", password=known[0], role="viewer")
    resp, generated = await svc.create_user(username="pl2", password=None, role="spotter")
    known.append(resp.generated_password)
    await db.commit()

    rows = (await db.execute(select(User))).scalars().all()
    assert rows, "expected user rows"
    for row in rows:
        values = [str(v) for v in row.__dict__.values() if v is not None]
        for secret in known:
            assert not any(v == secret for v in values), f"plaintext leaked for {row.username}"
        assert row.hashed_password.startswith("$2b$")
