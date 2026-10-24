# backend/tests/integration/test_database/test_repositories.py
"""
Integration tests for the REAL database repositories (H28 rewrite).

History: the previous version of this file (923 lines) imported seven
repository modules that do not exist —
``app.db.repositories.{tournament_repo, match_repo, team_repo, player_repo,
event_repo, highlight_repo, analytics_repo}`` — so it died at collection with
ModuleNotFoundError and the repository layer had ZERO real coverage while
appearing to be covered. The invented Match/Player/Event/Highlight/Analytics
repository tests were removed with it: those repositories do not exist, so
there was nothing honest to assert against them. What was "lost" was zero —
the file never ran.

Only two repositories exist in the codebase, and this file tests exactly
those, against a real (in-memory SQLite) database:

* ``app.db.repositories.tournament_repository.TournamentRepository``
* ``app.db.repositories.team_repository.TeamRepository``

Both extend ``app.db.base.BaseRepository``, whose generic CRUD
(create / get / get_or_404 / get_all / update / delete / count / exists /
bulk_create / bulk_update) is exercised here through the tournament repo,
plus each repository's own methods (``get_by_slug``, ``get_active``,
``deactivate_others``, ``list_all``, ``get_by_name``).

Isolation: the shared ``db_session`` fixture in tests/conftest.py points at
the real configured database (no rollback isolation), so this file builds
its own throwaway engine instead — a fresh in-memory SQLite database per
test — and never touches the dev/production DB. JSONB columns are swapped
for generic JSON in metadata exactly as tests/unit/test_users.py does
(test-local only; production models are untouched). Note the real models
store ``status`` as a plain string (e.g. "draft" / "active"), not the
``TournamentStatus`` enum the dead file asserted.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import JSON, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models.match import Match
from app.db.models.team import Team
from app.db.models.tournament import Tournament
from app.db.repositories.team_repository import TeamRepository
from app.db.repositories.tournament_repository import TournamentRepository


def _sqlite_friendly_metadata():
    """JSONB has no SQLite DDL rendering — swap it for generic JSON.

    Test-local only (same pattern as tests/unit/test_users.py); production
    models are untouched.
    """
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, postgresql.JSONB):
                column.type = JSON()


@pytest_asyncio.fixture()
async def repo_session():
    """Fresh in-memory DB + session per test (dev DB is never touched)."""
    _sqlite_friendly_metadata()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            # matches included only because Tournament.matches is
            # delete-orphan cascaded: a hard delete loads that table.
            tables=[Tournament.__table__, Team.__table__, Match.__table__],
        )
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


def _tournament_kwargs(**overrides):
    kwargs = {
        "name": "Repository Test Tournament",
        "slug": f"repo-test-{uuid.uuid4().hex[:8]}",
        "description": "Tournament created for repository testing",
        "status": "draft",
        "start_date": datetime.now(timezone.utc),
        "end_date": datetime.now(timezone.utc) + timedelta(days=3),
        "organizer": "Test Org",
        "points_per_kill": 1,
        "prize_pool": "₹1,00,000",
        "is_public": True,
    }
    kwargs.update(overrides)
    return kwargs


@pytest.mark.integration
@pytest.mark.database
class TestTournamentRepository:
    """Real CRUD against TournamentRepository + BaseRepository."""

    async def test_create_tournament(self, repo_session):
        repo = TournamentRepository(repo_session)
        tournament = await repo.create(**_tournament_kwargs())

        assert tournament.id is not None
        assert len(tournament.id) == 36  # UUID string
        assert tournament.name == "Repository Test Tournament"
        assert tournament.status == "draft"  # plain string column, not an enum
        assert tournament.total_matches == 0
        assert tournament.is_public is True

    async def test_get_tournament(self, repo_session):
        repo = TournamentRepository(repo_session)
        created = await repo.create(**_tournament_kwargs())

        retrieved = await repo.get(created.id)
        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.slug == created.slug

    async def test_get_tournament_not_found(self, repo_session):
        repo = TournamentRepository(repo_session)
        assert await repo.get(str(uuid.uuid4())) is None

    async def test_get_or_404(self, repo_session):
        repo = TournamentRepository(repo_session)
        created = await repo.create(**_tournament_kwargs())

        assert (await repo.get_or_404(created.id)).id == created.id
        with pytest.raises(HTTPException) as exc:
            await repo.get_or_404(str(uuid.uuid4()))
        assert exc.value.status_code == 404

    async def test_get_all_pagination(self, repo_session):
        repo = TournamentRepository(repo_session)
        for i in range(5):
            await repo.create(**_tournament_kwargs(name=f"List {i}"))

        # get_all orders by created_at DESC by default; page sizes are exact
        # on an isolated DB (the dead file assumed a shared DB and still
        # asserted exact page contents — one more reason it could never run).
        assert len(await repo.get_all(skip=0, limit=3)) == 3
        assert len(await repo.get_all(skip=3, limit=3)) == 2

    async def test_get_all_with_filters(self, repo_session):
        repo = TournamentRepository(repo_session)
        await repo.create(**_tournament_kwargs(name="Draft One"))
        await repo.create(**_tournament_kwargs(name="Active One", status="active"))

        actives = await repo.get_all(filters={"status": "active"})
        assert [t.name for t in actives] == ["Active One"]

    async def test_update_tournament(self, repo_session):
        repo = TournamentRepository(repo_session)
        created = await repo.create(**_tournament_kwargs())

        updated = await repo.update(
            created.id, name="Updated Name", prize_pool="₹2,00,000"
        )
        assert updated is not None
        assert updated.name == "Updated Name"
        assert updated.prize_pool == "₹2,00,000"
        assert updated.slug == created.slug  # untouched fields stay

    async def test_update_nonexistent_returns_none(self, repo_session):
        repo = TournamentRepository(repo_session)
        assert await repo.update(str(uuid.uuid4()), name="Nope") is None

    async def test_soft_delete_hides_row(self, repo_session):
        repo = TournamentRepository(repo_session)
        created = await repo.create(**_tournament_kwargs())

        assert await repo.delete(created.id, soft=True) is True
        assert await repo.get(created.id) is None
        # The row still exists physically — soft delete is a flag, and
        # BaseRepository reads filter it out.
        result = await repo_session.execute(
            select(Tournament).where(Tournament.id == created.id)
        )
        row = result.scalar_one_or_none()
        assert row is not None and row.is_deleted is True

    async def test_hard_delete_removes_row(self, repo_session):
        repo = TournamentRepository(repo_session)
        created = await repo.create(**_tournament_kwargs())

        assert await repo.delete(created.id, soft=False) is True
        result = await repo_session.execute(
            select(Tournament).where(Tournament.id == created.id)
        )
        assert result.scalar_one_or_none() is None

    async def test_delete_nonexistent_returns_false(self, repo_session):
        repo = TournamentRepository(repo_session)
        assert await repo.delete(str(uuid.uuid4())) is False

    async def test_count_and_exists(self, repo_session):
        repo = TournamentRepository(repo_session)
        created = await repo.create(**_tournament_kwargs())
        await repo.create(**_tournament_kwargs(is_public=False))

        assert await repo.count() == 2
        assert await repo.count(filters={"is_public": True}) == 1
        assert await repo.exists(created.id) is True
        assert await repo.exists(str(uuid.uuid4())) is False

    async def test_bulk_create_and_bulk_update(self, repo_session):
        repo = TournamentRepository(repo_session)
        items = [
            _tournament_kwargs(name=f"Bulk {i}") for i in range(5)
        ]
        created = await repo.bulk_create(items)
        assert len(created) == 5
        assert all(t.id for t in created)

        changed = await repo.bulk_update(
            [{"id": t.id, "description": "bulk-updated"} for t in created[:3]]
        )
        assert changed == 3
        assert (await repo.get(created[0].id)).description == "bulk-updated"
        assert (await repo.get(created[4].id)).description != "bulk-updated"

    async def test_get_by_slug(self, repo_session):
        repo = TournamentRepository(repo_session)
        created = await repo.create(**_tournament_kwargs())

        assert (await repo.get_by_slug(created.slug)).id == created.id
        assert await repo.get_by_slug("no-such-slug") is None

    async def test_get_active_and_deactivate_others(self, repo_session):
        repo = TournamentRepository(repo_session)
        first = await repo.create(**_tournament_kwargs(status="active"))
        second = await repo.create(**_tournament_kwargs(status="active"))
        await repo.create(**_tournament_kwargs(status="draft"))

        deactivated = await repo.deactivate_others(keep_id=first.id)
        assert deactivated == 1  # only `second`; the draft row is untouched

        active = await repo.get_active()
        assert active is not None and active.id == first.id
        assert (await repo.get(second.id)).status == "inactive"

    async def test_list_all(self, repo_session):
        repo = TournamentRepository(repo_session)
        await repo.create(**_tournament_kwargs())
        await repo.create(**_tournament_kwargs())
        gone = await repo.create(**_tournament_kwargs())
        await repo.delete(gone.id, soft=True)

        rows = await repo.list_all()
        assert len(rows) == 2  # soft-deleted rows are excluded


@pytest.mark.integration
@pytest.mark.database
class TestTeamRepository:
    """Real CRUD against TeamRepository + BaseRepository."""

    async def test_create_and_get_team(self, repo_session):
        repo = TeamRepository(repo_session)
        team = await repo.create(
            name="Repository Test Team",
            tag="RTT",
            region="India",
            is_active=True,
        )

        assert team.id is not None
        assert team.name == "Repository Test Team"
        assert team.tag == "RTT"
        assert team.is_active is True

        retrieved = await repo.get(team.id)
        assert retrieved is not None and retrieved.id == team.id

    async def test_get_by_name(self, repo_session):
        repo = TeamRepository(repo_session)
        team = await repo.create(name="Named Team", tag="NT")

        assert (await repo.get_by_name("Named Team")).id == team.id
        assert await repo.get_by_name("No Such Team") is None

    async def test_get_by_name_ignores_soft_deleted(self, repo_session):
        repo = TeamRepository(repo_session)
        team = await repo.create(name="Soon Deleted", tag="SD")
        await repo.delete(team.id, soft=True)

        assert await repo.get_by_name("Soon Deleted") is None

    async def test_update_team(self, repo_session):
        repo = TeamRepository(repo_session)
        team = await repo.create(name="Before", tag="BFR")

        updated = await repo.update(team.id, name="After", region="Asia")
        assert updated is not None
        assert updated.name == "After"
        assert updated.region == "Asia"
        assert updated.tag == "BFR"

    async def test_get_all_filters_active(self, repo_session):
        repo = TeamRepository(repo_session)
        await repo.create(name="Active Team", tag="ACT", is_active=True)
        await repo.create(name="Inactive Team", tag="INA", is_active=False)

        actives = await repo.get_all(filters={"is_active": True})
        assert [t.name for t in actives] == ["Active Team"]
