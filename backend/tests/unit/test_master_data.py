# backend/tests/unit/test_master_data.py
"""Phase 13 / W1 master-data tests (no Postgres, no Redis, no network).

Covers:
  * players CRUD (create/read/update/patch/delete) + honest-empty reads
  * player role validation against IGL/assault/support/sniper
  * player team assignment requires a real team (404 otherwise)
  * players cache: second read is a cache hit; writes invalidate
  * tournament CRUD + activate (only-one-active invariant)
  * tournament slug conflicts -> 409
  * tournament cache invalidation on activate
  * teams reads are honest-empty (no demo fallback), DB-down -> 503
  * points calculator reads the active tournament config; with no
    tournament it uses the documented Krafton BMSD defaults and logs
  * router auth: writes require admin, reads are public
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.db.models.player import Player
from app.db.models.team import Team
from app.db.models.tournament import Tournament


# ── in-memory fake repositories ──────────────────────────────────────


class _FakeRepo:
    """Minimal in-memory stand-in for BaseRepository subclasses."""

    def __init__(self):
        self.store: dict[str, object] = {}

    def _visible(self):
        return [o for o in self.store.values() if not getattr(o, "is_deleted", False)]

    async def create(self, **kwargs):
        obj = self._model(**kwargs)
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
        if filters:
            for field, value in filters.items():
                rows = [r for r in rows if getattr(r, field, None) == value]
        if order_by:
            rows = sorted(rows, key=lambda r: str(getattr(r, order_by, "") or ""))
        return rows[skip : skip + limit]

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


class FakePlayerRepo(_FakeRepo):
    _model = Player


class FakeTeamRepo(_FakeRepo):
    _model = Team


class FakeTournamentRepo(_FakeRepo):
    _model = Tournament

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

    async def list_all(self, skip=0, limit=100, order_by="created_at"):
        return await self.get_all(skip=skip, limit=limit, order_by=order_by)


class BrokenRepo:
    """Every call raises — simulates a dead database."""

    async def get_all(self, *a, **k):
        raise ConnectionError("db is gone")

    async def get(self, *a, **k):
        raise ConnectionError("db is gone")


# ── players CRUD ─────────────────────────────────────────────────────


def _player_service():
    from app.api.v1.players.service import PlayerService

    return PlayerService(db=None, repo=FakePlayerRepo())


def _player_payload(**over):
    from app.api.v1.players.schemas import PlayerCreate

    base = {"name": "Naman Mathur", "ign": "Mortal", "role": "IGL"}
    base.update(over)
    return PlayerCreate(**base)


@pytest.mark.asyncio
async def test_players_crud_roundtrip():
    svc = _player_service()
    created = await svc.create_player(_player_payload())
    assert created.id
    assert created.name == "Naman Mathur"
    assert created.ign == "Mortal"
    assert created.role == "IGL"
    assert created.team_id is None  # unassigned roster is allowed
    assert created.is_active is True

    fetched = await svc.get_player(created.id)
    assert fetched is not None and fetched.id == created.id

    from app.api.v1.players.schemas import PlayerUpdate

    updated = await svc.update_player(
        created.id,
        PlayerUpdate(name="Naman S Mathur", ign="Mortal", role="assault", is_active=True),
    )
    assert updated is not None and updated.role == "assault"

    from app.api.v1.players.schemas import PlayerPatch

    patched = await svc.patch_player(created.id, PlayerPatch(role="sniper"))
    assert patched is not None and patched.role == "sniper"
    assert patched.name == "Naman S Mathur"  # untouched fields preserved

    listed = await svc.list_players()
    assert [p.id for p in listed] == [created.id]

    assert await svc.delete_player(created.id) is True
    assert await svc.get_player(created.id) is None
    assert await svc.list_players() == []


@pytest.mark.asyncio
async def test_players_reads_honest_empty_without_db():
    from app.api.v1.players.service import PlayerService

    svc = PlayerService(db=None)
    assert await svc.list_players() == []
    assert await svc.get_player("nope") is None


@pytest.mark.asyncio
async def test_players_role_rejects_unknown():
    with pytest.raises(ValidationError):
        _player_payload(role="coach")


@pytest.mark.asyncio
async def test_players_team_assignment_requires_real_team():
    svc = _player_service()
    # teams repo is None here -> any team assignment is a 404
    with pytest.raises(HTTPException) as exc:
        await svc.create_player(_player_payload(team_id="ghost-team"))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_players_cache_hit_then_invalidate_on_write():
    from app.api.v1.players.service import PlayerService

    svc = PlayerService(db=None, repo=FakePlayerRepo())
    svc.cache._clear_local()

    calls = 0
    orig = svc._fetch_list_players

    async def counting(skip=0, limit=100, team_id=None):
        nonlocal calls
        calls += 1
        return await orig(skip=skip, limit=limit, team_id=team_id)

    svc._fetch_list_players = counting

    first = await svc.list_players()
    second = await svc.list_players()
    assert calls == 1, "second read must be served from cache"
    assert first == second

    await svc.create_player(_player_payload(name="Second Player", ign="P2"))
    await svc.list_players()
    assert calls == 2, "write must invalidate the cache"


# ── tournament CRUD + activate ───────────────────────────────────────


def _tournament_service():
    from app.api.v1.tournament.service import TournamentService

    return TournamentService(db=None, repo=FakeTournamentRepo())


def _tournament_payload(**over):
    from app.api.v1.tournament.schemas import TournamentCreate

    base = {"name": "BGIS 2026", "points_per_kill": 1, "placement_points": {"1": 10}}
    base.update(over)
    return TournamentCreate(**base)


@pytest.mark.asyncio
async def test_tournament_crud_roundtrip():
    svc = _tournament_service()
    created = await svc.create_tournament(_tournament_payload())
    assert created.slug == "bgis-2026"  # auto-derived from name
    assert created.status == "draft"
    assert created.is_active is False
    assert created.placement_points == {"1": 10}

    fetched = await svc.get_tournament(created.id)
    assert fetched is not None and fetched.id == created.id

    from app.api.v1.tournament.schemas import TournamentPatch

    patched = await svc.patch_tournament(
        created.id, TournamentPatch(points_per_kill=2, status="completed")
    )
    assert patched is not None and patched.points_per_kill == 2
    assert patched.is_active is False

    assert await svc.delete_tournament(created.id) is True
    assert await svc.get_tournament(created.id) is None


@pytest.mark.asyncio
async def test_tournament_slug_conflict_is_409():
    svc = _tournament_service()
    await svc.create_tournament(_tournament_payload(name="Same Name"))
    with pytest.raises(HTTPException) as exc:
        await svc.create_tournament(_tournament_payload(name="Same Name"))
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_tournament_activate_keeps_exactly_one_active():
    svc = _tournament_service()
    a = await svc.create_tournament(_tournament_payload(name="Cup A"))
    b = await svc.create_tournament(_tournament_payload(name="Cup B"))

    act_a = await svc.activate_tournament(a.id)
    assert act_a is not None and act_a.is_active is True
    active = await svc.get_active_tournament()
    assert active is not None and active.id == a.id

    act_b = await svc.activate_tournament(b.id)
    assert act_b is not None and act_b.is_active is True
    active = await svc.get_active_tournament()
    assert active is not None and active.id == b.id

    # only-one-active invariant: A was demoted, B is the single active one
    again_a = await svc.get_tournament(a.id)
    assert again_a is not None and again_a.is_active is False
    assert again_a.status == "inactive"

    rows = await svc.list_tournaments()
    actives = [t for t in rows if t.is_active]
    assert len(actives) == 1 and actives[0].id == b.id


@pytest.mark.asyncio
async def test_tournament_activate_missing_is_none():
    svc = _tournament_service()
    assert await svc.activate_tournament("does-not-exist") is None
    assert await svc.get_active_tournament() is None


@pytest.mark.asyncio
async def test_tournament_cache_invalidated_on_activate():
    svc = _tournament_service()
    a = await svc.create_tournament(_tournament_payload(name="Cup C"))

    calls = 0
    orig = svc._fetch_active_tournament

    async def counting():
        nonlocal calls
        calls += 1
        return await orig()

    svc._fetch_active_tournament = counting

    assert await svc.get_active_tournament() is None  # miss, caches None
    assert calls == 1
    await svc.activate_tournament(a.id)  # write -> invalidate
    active = await svc.get_active_tournament()  # must re-fetch
    assert calls == 2
    assert active is not None and active.id == a.id


# ── teams: honest empty, no demo fallback ─────────────────────────────


@pytest.mark.asyncio
async def test_teams_list_is_honest_empty_without_db():
    from app.api.v1.teams import service as teams_service
    from app.api.v1.teams.service import TeamService

    # Module-level teams cache is shared across tests in this process —
    # reset it so other tests' cached entries can't leak in here.
    teams_service._invalidate_teams_cache()
    svc = TeamService(db=None)
    assert await svc.list_teams() == []
    assert await svc.get_team("t1") is None


def test_teams_service_has_no_demo_import():
    import app.api.v1.teams.service as teams_service

    assert not hasattr(teams_service, "DEMO_TEAMS"), "demo dataset must not be imported"
    src = open(teams_service.__file__).read()
    assert "from .demo_data import" not in src
    assert "from app.api.v1.teams.demo_data import" not in src
    assert "_demo_response" not in src


@pytest.mark.asyncio
async def test_teams_db_down_is_503_not_fake():
    from app.api.v1.teams import service as teams_service
    from app.api.v1.teams.service import TeamService

    teams_service._invalidate_teams_cache()  # reset module cache from other tests
    svc = TeamService(db=None)
    svc.repo = BrokenRepo()
    with pytest.raises(HTTPException) as exc:
        await svc.list_teams()
    assert exc.value.status_code == 503
    with pytest.raises(HTTPException) as exc2:
        await svc.get_team("t1")
    assert exc2.value.status_code == 503


@pytest.mark.asyncio
async def test_teams_response_carries_region_and_is_active():
    from app.api.v1.teams.service import TeamService, _db_response

    svc = TeamService(db=None)
    svc.repo = FakeTeamRepo()
    team = await svc.repo.create(name="Team X", region="South Asia", is_active=False)
    resp = _db_response(team)
    assert resp.region == "South Asia"
    assert resp.is_active is False
    assert resp.source == "db"


# ── points calculator reads tournament config ────────────────────────


def test_scoring_uses_bmsd_defaults_without_tournament(caplog):
    from app.analytics.points_calculator import PointsCalculator

    calc = PointsCalculator()
    with caplog.at_level("WARNING", logger="aegis.analytics.points"):
        calc.configure_from_tournament(None)
    assert calc.points_per_kill == 1
    assert calc.placement_points.get_points(1) == 10  # chicken
    assert calc.placement_points.get_points(2) == 0  # no other placement points
    assert calc.config_source.startswith("defaults")
    assert any("defaults" in r.message for r in caplog.records)


def test_scoring_reads_tournament_config():
    from app.analytics.points_calculator import PointsCalculator

    calc = PointsCalculator()
    calc.configure_from_tournament(
        {
            "id": "t1",
            "points_per_kill": 2,
            "placement_points": {"1": 15, "2": 12},
            "rules": {},
        }
    )
    assert calc.points_per_kill == 2
    assert calc.placement_points.get_points(1) == 15
    assert calc.placement_points.get_points(2) == 12
    assert calc.placement_points.get_points(3) == 0  # missing -> 0, honest
    assert calc.config_source == "tournament:t1"


def test_scoring_match_calc_uses_tournament_config():
    from app.analytics.points_calculator import PointsCalculator

    calc = PointsCalculator()
    calc.configure_from_tournament(
        {"id": "t9", "points_per_kill": 1, "placement_points": {"1": 10}, "rules": {}}
    )
    out = calc.calculate_match_points(
        "m1",
        [
            {"team_id": "a", "team_name": "A", "placement": 1, "kills": 5, "damage": 900},
            {"team_id": "b", "team_name": "B", "placement": 4, "kills": 3, "damage": 500},
        ],
    )
    assert out["a"]["placement_points"] == 10
    assert out["a"]["kill_points"] == 5
    assert out["b"]["placement_points"] == 0  # BMSD: only chicken scores placement
    assert out["b"]["kill_points"] == 3


@pytest.mark.asyncio
async def test_configure_from_active_tournament_never_raises_on_db_failure():
    from app.analytics.points_calculator import PointsCalculator

    class _DeadSession:
        async def execute(self, *a, **k):
            raise ConnectionError("db is gone")

    calc = PointsCalculator()
    ok = await calc.configure_from_active_tournament(_DeadSession())
    assert ok is False
    assert calc.points_per_kill == 1
    assert calc.placement_points.get_points(1) == 10
    assert calc.config_source.startswith("defaults")


# ── router auth surface ───────────────────────────────────────────────


def _route_deps(router, method, path):
    for route in router.routes:
        methods = getattr(route, "methods", None) or set()
        if method in methods and getattr(route, "path", "") == path:
            return route.dependencies
    raise AssertionError(f"route {method} {path} not found")


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/players"),
        ("GET", "/players/{player_id}"),
        ("GET", "/teams/{team_id}/players"),
        ("GET", "/tournament"),
        ("GET", "/tournament/active"),
        ("GET", "/tournament/{tournament_id}"),
    ],
)
def test_reads_are_public(method, path):
    from app.api.v1.players.router import router as players_router
    from app.api.v1.tournament.router import router as tournament_router

    router = players_router if path.startswith(("/players", "/teams")) else tournament_router
    assert _route_deps(router, method, path) == []


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/players"),
        ("PUT", "/players/{player_id}"),
        ("PATCH", "/players/{player_id}"),
        ("DELETE", "/players/{player_id}"),
        ("POST", "/tournament"),
        ("PUT", "/tournament/{tournament_id}"),
        ("PATCH", "/tournament/{tournament_id}"),
        ("DELETE", "/tournament/{tournament_id}"),
        ("POST", "/tournament/{tournament_id}/activate"),
    ],
)
def test_writes_require_admin(method, path):
    from app.api.v1.players.router import router as players_router
    from app.api.v1.tournament.router import router as tournament_router

    router = players_router if path.startswith("/players") else tournament_router
    assert len(_route_deps(router, method, path)) > 0
