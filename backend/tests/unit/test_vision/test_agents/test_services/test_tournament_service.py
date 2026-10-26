# backend/tests/unit/test_vision/test_agents/test_services/test_tournament_service.py
"""Unit tests for the REAL TournamentService.

History / honesty note (K-H8 fix, 2026-10-24): the previous version of
this file imported ``app.services.tournament_service`` and
``app.db.models`` names (``TournamentStatus``, ``TournamentFormat``,
``TournamentTeam``) that never existed, so plain pytest died at
collection. The real service is
``app.api.v1.tournament.service.TournamentService`` — constructor
``(db=None, repo=None)`` — and its status values are plain strings
(``"draft"`` / ``"active"`` / ``"completed"`` / ``"archived"`` /
``"inactive"``), not an enum.

Only the real API is tested here. Methods the old file asserted
(``register_team``, ``unregister_team``, ``get_registered_teams``,
``get_tournament_standings``, ``get_tournament_stats``,
``start_tournament``, ``complete_tournament``, ``cancel_tournament``,
hard-delete) do NOT exist on the real service and are NOT tested —
inventing them would re-create the same fake coverage. Likewise the
real ``get_tournament`` returns ``None`` for a missing id (it does not
raise), and ``delete_tournament`` is always a soft delete returning
bool (there is no ``soft=False`` parameter).

No Postgres, no Redis, no network: a small in-memory fake repository
(same pattern as ``tests/unit/test_master_data.py``) stands in for the
SQLAlchemy repository, injected via the service's ``repo=`` parameter.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.api.v1.tournament.schemas import (
    TournamentCreate,
    TournamentPatch,
    TournamentUpdate,
)
from app.api.v1.tournament.service import TournamentService
from app.db.models.tournament import Tournament


# ── in-memory fake repository (mirrors test_master_data.py) ──────────


class _FakeTournamentRepo:
    """Minimal in-memory stand-in for TournamentRepository."""

    def __init__(self):
        self.store: dict[str, Tournament] = {}

    def _visible(self):
        return [o for o in self.store.values() if not getattr(o, "is_deleted", False)]

    async def create(self, **kwargs):
        obj = Tournament(**kwargs)
        if not getattr(obj, "id", None):
            obj.id = str(uuid.uuid4())
        self.store[obj.id] = obj
        return obj

    async def get(self, id):
        obj = self.store.get(str(id))
        if obj is not None and getattr(obj, "is_deleted", False):
            return None
        return obj

    async def get_all(self, skip=0, limit=100, order_by=None, filters=None):
        rows = self._visible()
        if order_by:
            rows = sorted(rows, key=lambda r: str(getattr(r, order_by, "") or ""))
        return rows[skip : skip + limit]

    async def list_all(self, skip=0, limit=100, order_by="created_at"):
        return await self.get_all(skip=skip, limit=limit, order_by=order_by)

    async def get_by_slug(self, slug):
        for t in self._visible():
            if t.slug == slug:
                return t
        return None

    async def get_active(self):
        for t in self._visible():
            if t.status == "active":
                return t
        return None

    async def deactivate_others(self, keep_id):
        n = 0
        for t in self._visible():
            if str(t.id) != str(keep_id) and t.status == "active":
                t.status = "inactive"
                n += 1
        return n

    async def update(self, id, **kwargs):
        obj = await self.get(id)
        if obj is None:
            return None
        for key, value in kwargs.items():
            if hasattr(obj, key):
                setattr(obj, key, value)
        return obj

    async def delete(self, id, soft=True):
        obj = await self.get(id)
        if obj is None:
            return False
        if soft:
            obj.is_deleted = True
        else:
            self.store.pop(obj.id, None)
        return True


def _service():
    return TournamentService(db=None, repo=_FakeTournamentRepo())


def _payload(**over):
    base = {
        "name": "AEGIS Invitational 2024",
        "points_per_kill": 1,
        "placement_points": {"1": 10},
        "prize_pool": "₹10,00,000",
        "organizer": "AEGIS AI",
    }
    base.update(over)
    return TournamentCreate(**base)


@pytest.mark.unit
@pytest.mark.services
class TestTournamentService:
    """Test the real TournamentService (DB-backed API surface)."""

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CREATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @pytest.mark.asyncio
    async def test_create_tournament(self):
        svc = _service()
        created = await svc.create_tournament(_payload())
        assert created is not None
        assert created.name == "AEGIS Invitational 2024"
        assert created.status == "draft"
        assert created.is_active is False
        assert created.total_matches == 0
        assert created.points_per_kill == 1
        assert created.placement_points == {"1": 10}
        assert created.prize_pool == "₹10,00,000"

    @pytest.mark.asyncio
    async def test_create_tournament_generates_slug(self):
        svc = _service()
        created = await svc.create_tournament(_payload())
        assert created.slug == "aegis-invitational-2024"
        assert " " not in created.slug
        assert created.slug == created.slug.lower()

    @pytest.mark.asyncio
    async def test_create_tournament_explicit_slug_kept(self):
        svc = _service()
        created = await svc.create_tournament(_payload(slug="my-cup-2026"))
        assert created.slug == "my-cup-2026"

    @pytest.mark.asyncio
    async def test_create_tournament_duplicate_slug_is_409(self):
        svc = _service()
        await svc.create_tournament(_payload(name="Same Name"))
        with pytest.raises(HTTPException) as exc:
            await svc.create_tournament(_payload(name="Same Name"))
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_create_tournament_without_db_is_503(self):
        svc = TournamentService(db=None)  # no repo -> no database
        with pytest.raises(HTTPException) as exc:
            await svc.create_tournament(_payload())
        assert exc.value.status_code == 503

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # RETRIEVAL (honest-empty reads, no DB)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @pytest.mark.asyncio
    async def test_get_tournament(self):
        svc = _service()
        created = await svc.create_tournament(_payload())
        fetched = await svc.get_tournament(created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.name == "AEGIS Invitational 2024"

    @pytest.mark.asyncio
    async def test_get_tournament_not_found_is_none_not_raise(self):
        # Real behaviour: missing row -> None (the router maps it to 404).
        svc = _service()
        assert await svc.get_tournament("non_existent_id") is None

    @pytest.mark.asyncio
    async def test_list_tournaments(self):
        svc = _service()
        for name in ("Cup A", "Cup B", "Cup C"):
            await svc.create_tournament(_payload(name=name))
        rows = await svc.list_tournaments()
        assert len(rows) == 3
        assert {t.name for t in rows} == {"Cup A", "Cup B", "Cup C"}

    @pytest.mark.asyncio
    async def test_list_tournaments_pagination(self):
        svc = _service()
        for name in ("Cup A", "Cup B", "Cup C"):
            await svc.create_tournament(_payload(name=name))
        page = await svc.list_tournaments(skip=1, limit=1)
        assert len(page) == 1

    @pytest.mark.asyncio
    async def test_reads_without_db_are_honest_empty(self):
        svc = TournamentService(db=None)
        assert await svc.list_tournaments() == []
        assert await svc.get_tournament("anything") is None
        assert await svc.get_active_tournament() is None

    @pytest.mark.asyncio
    async def test_get_is_cached_second_read_skips_fetch(self):
        svc = _service()
        created = await svc.create_tournament(_payload())
        calls = 0
        orig = svc._fetch_tournament

        async def counting(tournament_id):
            nonlocal calls
            calls += 1
            return await orig(tournament_id)

        svc._fetch_tournament = counting
        assert await svc.get_tournament(created.id) is not None
        assert calls == 1
        assert await svc.get_tournament(created.id) is not None
        assert calls == 1  # second read served from cache

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # UPDATE / PATCH
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @pytest.mark.asyncio
    async def test_update_tournament(self):
        svc = _service()
        created = await svc.create_tournament(_payload())
        updated = await svc.update_tournament(
            created.id,
            TournamentUpdate(
                name="Updated Tournament Name",
                slug=created.slug,
                description="Updated description",
            ),
        )
        assert updated is not None
        assert updated.name == "Updated Tournament Name"
        assert updated.description == "Updated description"

    @pytest.mark.asyncio
    async def test_update_tournament_not_found_is_none(self):
        svc = _service()
        updated = await svc.update_tournament(
            "missing",
            TournamentUpdate(name="X", slug="x"),
        )
        assert updated is None

    @pytest.mark.asyncio
    async def test_patch_tournament_partial(self):
        svc = _service()
        created = await svc.create_tournament(_payload())
        patched = await svc.patch_tournament(
            created.id, TournamentPatch(points_per_kill=2, status="completed")
        )
        assert patched is not None
        assert patched.points_per_kill == 2
        assert patched.status == "completed"
        assert patched.is_active is False
        assert patched.name == "AEGIS Invitational 2024"  # untouched field kept

    @pytest.mark.asyncio
    async def test_patch_tournament_not_found_is_none(self):
        svc = _service()
        assert await svc.patch_tournament("missing", TournamentPatch(name="X")) is None

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ACTIVATE — exactly one active tournament
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @pytest.mark.asyncio
    async def test_activate_tournament_keeps_exactly_one_active(self):
        svc = _service()
        a = await svc.create_tournament(_payload(name="Cup A"))
        b = await svc.create_tournament(_payload(name="Cup B"))

        act_a = await svc.activate_tournament(a.id)
        assert act_a is not None and act_a.is_active is True
        active = await svc.get_active_tournament()
        assert active is not None and active.id == a.id

        act_b = await svc.activate_tournament(b.id)
        assert act_b is not None and act_b.is_active is True
        active = await svc.get_active_tournament()
        assert active is not None and active.id == b.id

        again_a = await svc.get_tournament(a.id)
        assert again_a is not None and again_a.is_active is False
        assert again_a.status == "inactive"

        actives = [t for t in await svc.list_tournaments() if t.is_active]
        assert len(actives) == 1 and actives[0].id == b.id

    @pytest.mark.asyncio
    async def test_activate_tournament_not_found_is_none(self):
        svc = _service()
        assert await svc.activate_tournament("does-not-exist") is None
        assert await svc.get_active_tournament() is None

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DELETION (soft delete, bool result)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @pytest.mark.asyncio
    async def test_delete_tournament_soft_deletes(self):
        svc = _service()
        created = await svc.create_tournament(_payload())
        assert await svc.delete_tournament(created.id) is True
        # Soft-deleted rows disappear from reads.
        assert await svc.get_tournament(created.id) is None
        assert await svc.list_tournaments() == []

    @pytest.mark.asyncio
    async def test_delete_tournament_not_found_is_false(self):
        svc = _service()
        assert await svc.delete_tournament("missing") is False
