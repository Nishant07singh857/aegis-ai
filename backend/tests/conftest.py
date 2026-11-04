# backend/tests/conftest.py
"""
AEGIS AI - Test Configuration
Shared fixtures, mock objects, and test utilities for the entire test suite.
"""

# ── Integrity Fix Pack (C1/H1) — explicit TEST-ONLY credentials ──────
# app.config no longer carries built-in SECRET_KEY / DATABASE_URL
# defaults, so the suite must provide its own, exactly as the
# tests/pytest.ini [pytest-env] section always intended. setdefault runs
# before any app import in this process, never overrides a real
# environment, and these values are test fixtures, not credentials.
import os as _os

_os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")
_os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")

import asyncio
import pytest
import pytest_asyncio
from typing import List
from unittest.mock import Mock, AsyncMock
import structlog
import uuid
from datetime import datetime, timedelta

# Configure test logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.dev.ConsoleRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger("aegis.tests")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ASYNC SUPPORT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATABASE FIXTURES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest_asyncio.fixture
async def db_session():
    """Per-test ISOLATED database session (Phase 35 W5).

    Previously this fixture handed out sessions on the shared
    ``AsyncSessionLocal`` bound to the shared ``./test.db`` file,
    with only a rollback at teardown — rows committed by one test
    (or left behind by ``flush`` + a later commit) were visible to
    the next, and two tests could collide on unique values (e.g.
    the ``test_tournament`` slug below). Now every test gets a
    FRESH in-memory SQLite database: its own engine (StaticPool,
    one connection, so ``:memory:`` is shared by create_all and the
    session), full ``create_all``, and engine disposal at teardown.
    Nothing written here can leak into another test, or into the
    shared ``./test.db``.

    Scope note: this fixture isolates only tests that USE it
    (only the Phase 35 W5 proof tests do so far). It does NOT
    rebind the production module-global engine /
    ``AsyncSessionLocal`` in ``app.db.base`` — but that gap is
    now closed suite-wide by the Phase 37 W3 autouse fixture
    below (``_isolated_db_per_test``), which rebinds the 5
    production names for EVERY test, so no test reaches the
    shared ``./test.db`` any more. This fixture remains as the
    explicit per-test session for tests that request it.
    """
    from sqlalchemy import JSON
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.pool import StaticPool

    from app.db.base import Base

    # JSONB has no SQLite DDL rendering — swap it for generic JSON.
    # Test-local only, same pattern as test_repositories.py and
    # test_users.py; production models are untouched. The swap is
    # idempotent: re-running it over already-swapped columns is a
    # no-op, so repeated fixture use is safe.
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, postgresql.JSONB):
                column.type = JSON()

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with maker() as session:
        yield session
        await session.rollback()
    await engine.dispose()


# ── Phase 37 W3: suite-wide per-test DB isolation ─────────────────
# Rebinds the module-global engine / AsyncSessionLocal for EVERY test
# (autouse), closing the gap the db_session docstring above discloses.
#
# DESIGN (verified by grep at implementation time, not assumed):
# * The production rebind set is exactly 5 names in 3 modules:
#   ``app.db.base.engine``, ``app.db.base.AsyncSessionLocal``,
#   ``app.db.session.engine``, ``app.db.session.AsyncSessionLocal``
#   (by-value re-exports of base), ``app.api.deps.AsyncSessionLocal``
#   (``deps.py:128`` by-value import). ``app.db.__init__`` re-exports
#   the same names but has no engine/AsyncSessionLocal consumer, so it
#   is not rebound. Every other production consumer resolves the
#   factory lazily — function-local ``from app.db.base import
#   AsyncSessionLocal`` (director_learning journal/queries/knobs/
#   labeler/engine, observer_pilot pov_journal/roster, live_stats)
#   or ``from app.db.session import AsyncSessionLocal``
#   (vision_intelligence service, live_director build function,
#   event_bus, main lifespan helper) — so a setattr on the owning
#   module redirects them at call time. ``base.get_db`` /
#   ``get_db_context`` / ``init_db`` / ``close_db`` / ``check_db_health``
#   read the base-module globals ``engine`` / ``AsyncSessionLocal`` at
#   call time, so they follow the rebind too; the 13 routers that
#   ``from app.db.session import get_db`` bind the FUNCTION object,
#   not the engine, and need no change. Zero module-level by-value
#   captures of the factory exist in production (both
#   ``DbSlotResolver(AsyncSessionLocal)`` sites sit inside builder
#   FUNCTIONS). The sessionmaker is bound to its engine at creation,
#   so rebinding ``engine`` alone would be a lie — both names are
#   swapped together, atomically per test, via ``monkeypatch``.
# * Per-test DB is a FILE (``tmp_path/isolated.db``), never ``:memory:``:
#   sync ``TestClient`` tests run the lifespan in the portal thread's
#   own event loop, and a StaticPool ``:memory:`` connection created
#   in one loop cannot be used from another. A file DB with
#   ``NullPool`` (the Phase 27 Stack precedent: every checkout opens
#   a fresh connection in the CALLING loop, nothing is pooled across
#   loops) is loop-safe in both directions. ``asyncio.run`` is used
#   for create_all/dispose from this SYNC fixture, so it serves sync
#   and async tests alike (an async autouse fixture cannot serve sync
#   tests); the same ``asyncio.run`` + NullPool pattern is already
#   proven in-tree by the Phase 27 Stack and the Phase 32/35-W2/36-W2
#   fixtures. ``_register_engine_listeners`` is applied for parity
#   with ``base.create_engine()`` (log-only listeners).
# * ORDERING: pytest runs autouse fixtures before explicitly requested
#   ones at the same scope (verified by probe), so tests that
#   monkeypatch ``deps``/``base`` ``AsyncSessionLocal`` themselves
#   (vision_intelligence, integrity_credentials_auth,
#   phase32_ws_dossier_auth incl. its manual save/restore,
#   phase36_w1/w2) override the per-test factory and ``monkeypatch``
#   teardown restores it afterwards; tests that build their OWN
#   engines (Phase 27 learning, repositories, users, ...) are
#   untouched and already isolated. Lifespan rebuilds its singletons
#   (live director, vision intelligence, monitor) per ``TestClient``
#   context, capturing the then-current factory; shutdown never calls
#   ``close_db`` (verified), so this fixture's dispose is the only
#   one — no double-dispose.
# * COST (measured, not estimated): with ``PRAGMA synchronous=OFF``
#   on the test engine only (Phase 37 W3-fix), fixture setup is
#   ~0.15 s per test (25 tests in 8.23 s). WITHOUT the pragma the
#   same file took 148.82 s with setups of 6.02–7.16 s: every DDL
#   statement fsyncs (~85 ms each) on this VM's home disk. The
#   earlier "0.12–0.21 s" claim was measured on a path where the
#   fsync cost did not apply and is FALSE for this fixture — do
#   not repeat it. No template copy, deliberately: per-test
#   create_all reads the LIVE metadata, so a test that mutates
#   ``Base.metadata`` at runtime cannot go stale against a
#   session-start snapshot.
# * WHAT IT DOES NOT DO: it does not isolate the nested-subprocess
#   pytest runs (Phase 35 W5 / Phase 36 W3 proof tests) — those are
#   separate interpreters with their own conftest load, each test
#   inside them still gets its own isolated DB the same way. It does
#   not touch ``./test.db`` at all: the shared engine still exists
#   (created at import) but nothing under test can reach it any
#   more, since every path goes through one of the 5 rebound names.
#   NO test file was edited for the rebind itself (conftest-only
#   mechanism); the ONE test file edited for this work is
#   ``tests/integration/test_api/test_tournament_endpoints.py``,
#   whose 3 tests asserted the OLD premise (reads 503 because the
#   shared ./test.db was never initialised) — they now assert the
#   live-DB contract (200/404) plus an explicit dead-DB 503 test.
#   No test file installs an import-time fpdf stub any more
#   (verified by grep at implementation time: zero column-0
#   ``sys.modules.setdefault("fpdf", ...)`` sites remain), so the
#   Phase 37 W5-ownership constraint on stub files is vacuous here.
#   If this fixture is ever removed, the suite silently returns to
#   sharing ``./test.db`` — that is why the docstring states the
#   mechanism instead of hiding it.
@pytest.fixture(autouse=True)
def _isolated_db_per_test(tmp_path, monkeypatch):
    """Rebind the DB globals to a fresh per-test file DB.

    Setup: build a ``NullPool`` file engine on
    ``tmp_path/isolated.db``, ``create_all`` the 32-table metadata,
    rebind the 5 names listed above. Teardown: dispose the engine;
    ``monkeypatch`` restores the shared ``./test.db`` globals.
    Note: the 32 tables are ``app.db.base.Base`` metadata only — ``drop_events``/``zone_phases`` (``app.analysis.models``) are NOT created, 2 fewer than the inventory's 34 tables; no test needs them through the fixture.
    """
    import asyncio

    from sqlalchemy import JSON
    from sqlalchemy import event as sa_event
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.pool import NullPool

    from app.api import deps as deps_module
    from app.db import base as base_module
    from app.db import session as session_module

    # JSONB has no SQLite DDL rendering — swap it for generic JSON.
    # Same idempotent pattern as the db_session fixture above and
    # test_repositories.py / test_users.py; production models untouched.
    for table in base_module.Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, postgresql.JSONB):
                column.type = JSON()

    db_path = tmp_path / "isolated.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        poolclass=NullPool,
    )
    base_module._register_engine_listeners(engine)

    # TEST-ONLY fsync fix (Phase 37 W3-fix): every DDL statement on
    # this file DB fsyncs (~85 ms each on this VM's home disk), so
    # create_all over the 32-table metadata cost ~6 s per test
    # (measured: 25 tests in 148.82 s, setups 6.02–7.16 s).
    # PRAGMA synchronous=OFF drops create_all to ~0.14 s (measured
    # by probe: plain 6.003 s -> synchronous=OFF 0.137 s). The
    # per-test DBs are throwaway files deleted after each test, so
    # durability on OS crash is irrelevant and data visibility
    # within a test is identical. Applied to the TEST engine only —
    # never to production engines (app/db/base.py untouched).
    @sa_event.listens_for(engine.sync_engine, "connect")
    def _disable_sync_for_test_db(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA synchronous=OFF")
        cursor.close()

    async def _create_all():
        async with engine.begin() as conn:
            await conn.run_sync(base_module.Base.metadata.create_all)

    try:
        asyncio.run(_create_all())
    except Exception:
        # Setup failed (e.g. /tmp full): the file may already exist
        # as a 0-byte stub — remove it so a failed setup never
        # leaves debris behind, then propagate the real error.
        try:
            asyncio.run(engine.dispose())
        except Exception:
            pass
        try:
            db_path.unlink()
        except FileNotFoundError:
            pass
        raise
    maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    monkeypatch.setattr(base_module, "engine", engine)
    monkeypatch.setattr(base_module, "AsyncSessionLocal", maker)
    monkeypatch.setattr(session_module, "engine", engine)
    monkeypatch.setattr(session_module, "AsyncSessionLocal", maker)
    monkeypatch.setattr(deps_module, "AsyncSessionLocal", maker)
    yield
    asyncio.run(engine.dispose())
    # /tmp is a 512 MB tmpfs: a 32-table sqlite file is ~370 KB, so
    # keeping one per test would exhaust it over a full-suite run
    # (measured: 1320 files / 489 MB before this unlink existed).
    # The file is useless after the test — remove it so live /tmp
    # usage stays at ~1 DB. The empty tmp_path dir itself is left
    # for pytest's own numbered-dir retention to reap.
    try:
        db_path.unlink()
    except FileNotFoundError:
        pass


@pytest_asyncio.fixture
async def test_tournament(db_session):
    """Create a test tournament"""
    from app.db.models import Tournament, TournamentStatus
    
    tournament = Tournament(
        id=str(uuid.uuid4()),
        name="Test Tournament",
        slug="test-tournament",
        description="Test tournament for unit tests",
        status=TournamentStatus.DRAFT,
        start_date=datetime.utcnow(),
        end_date=datetime.utcnow() + timedelta(days=3),
        points_per_kill=1,
        organizer="Test Org",
        is_public=True,
    )
    
    db_session.add(tournament)
    await db_session.flush()
    
    return tournament


@pytest_asyncio.fixture
async def test_team(db_session):
    """Create a test team"""
    from app.db.models import Team
    
    team = Team(
        id=str(uuid.uuid4()),
        name="Test Team",
        tag="TST",
        is_active=True,
    )
    
    db_session.add(team)
    await db_session.flush()
    
    return team


@pytest_asyncio.fixture
async def test_players(db_session, test_team):
    """Create test players"""
    from app.db.models import Player, PlayerRole
    
    players = []
    for i in range(4):
        player = Player(
            id=str(uuid.uuid4()),
            team_id=test_team.id,
            name=f"Test Player {i+1}",
            ign=f"TestPlayer{i+1}",
            role=PlayerRole.FRAGGER if i == 0 else PlayerRole.SUPPORT,
            is_active=True,
        )
        db_session.add(player)
        players.append(player)
    
    await db_session.flush()
    return players


@pytest_asyncio.fixture
async def test_match(db_session, test_tournament):
    """Create a test match"""
    from app.db.models import Match, MatchStatus, MatchMap
    
    match = Match(
        id=str(uuid.uuid4()),
        tournament_id=test_tournament.id,
        match_number=1,
        map_name=MatchMap.ERANGEL,
        status=MatchStatus.SCHEDULED,
        scheduled_time=datetime.utcnow(),
    )
    
    db_session.add(match)
    await db_session.flush()
    
    return match


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EVENT BUS FIXTURES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest_asyncio.fixture
async def event_bus():
    """Create a test event bus with mock Redis"""
    from app.core.event_bus import EventBus
    
    bus = EventBus()
    
    # Mock Redis connection
    bus._redis = AsyncMock()
    bus._pubsub_redis = AsyncMock()
    bus._consumer_redis = AsyncMock()
    bus._running = True
    
    # Mock Redis methods
    bus._redis.ping = AsyncMock(return_value=True)
    bus._redis.xadd = AsyncMock(return_value="test-event-id")
    bus._redis.publish = AsyncMock(return_value=1)
    
    return bus


@pytest.fixture
def sample_event():
    """Create a sample event"""
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "kill",
        "timestamp_ms": 1000,
        "source": "test",
        "data": {
            "killer_id": "player_1",
            "victim_id": "player_2",
            "weapon": "M416",
            "headshot": True,
        },
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AGENT FIXTURES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest_asyncio.fixture
async def mock_event_bus():
    """Create a fully mocked event bus for agent testing"""
    bus = AsyncMock()
    bus.publish = AsyncMock(return_value="test-event-id")
    bus.subscribe = Mock(return_value="subscriber-id")
    bus.unsubscribe = Mock(return_value=True)
    bus.is_running = True
    return bus


@pytest_asyncio.fixture
async def team_agent(mock_event_bus):
    """Create a test team agent"""
    from app.agents.team_agents.team_tracker import TeamTracker
    
    from app.agents.base_agent import AgentConfig

    agent = TeamTracker(
        config=AgentConfig(agent_id="test_team_001", agent_type="team_agent"),
        event_bus=mock_event_bus,
        team_id="test_team_001",
        team_name="Test Team",
        short_name="TEST",
        player_names=["Player1", "Player2", "Player3", "Player4"],
    )
    
    return agent


@pytest_asyncio.fixture
async def fight_detector(mock_event_bus):
    """Create a test fight detector"""
    from app.agents.fight_agents.fight_detector import FightDetector
    
    from app.agents.base_agent import AgentConfig

    detector = FightDetector(
        config=AgentConfig(agent_id="fight_detector", agent_type="fight_detector"),
        event_bus=mock_event_bus,
    )
    return detector


@pytest_asyncio.fixture
async def observer_director(mock_event_bus):
    """Create a test observer director"""
    from app.agents.observer_agents.director_ai import ObserverDirector
    
    director = ObserverDirector(mock_event_bus)
    return director


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# VISION FIXTURES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def sample_frame():
    """Create a sample video frame"""
    import numpy as np
    return np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8)


@pytest.fixture
def sample_minimap_frame():
    """Create a sample minimap frame"""
    import numpy as np
    return np.random.randint(0, 255, (300, 500, 3), dtype=np.uint8)


@pytest.fixture
def sample_kill_feed_frame():
    """Create a sample kill feed frame"""
    import numpy as np
    return np.random.randint(0, 255, (400, 200, 3), dtype=np.uint8)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ML FIXTURES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def sample_fight_features():
    """Create sample fight features"""
    from app.ml.prediction.fight_outcome_model import FightFeatures
    
    return FightFeatures(
        fight_id="test_fight_001",
        team_a_total_hp=350.0,
        team_b_total_hp=280.0,
        team_a_hp_percent=87.5,
        team_b_hp_percent=70.0,
        team_a_players_alive=4,
        team_b_players_alive=3,
        team_a_damage_dealt=500.0,
        team_b_damage_dealt=300.0,
        distance_between_teams=50.0,
        team_a_cover_quality=60.0,
        team_b_cover_quality=40.0,
        team_a_has_high_ground=True,
        team_b_has_high_ground=False,
        zone_phase=4,
        teams_alive=8,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMMENTARY FIXTURES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def sample_commentary_prompt():
    """Create a sample commentary prompt.

    Phase 22 (W3): the dead ``app.ml.commentary.llm_chain`` module this
    fixture used to import was deleted (zero production callers, stale
    paid-provider defaults). No test consumed this fixture; it now
    returns a plain dict with the same fields so any future consumer
    migrates to the live commentary types in ``app.ai`` /
    ``agents.commentary_agents`` instead of resurrecting the chain.
    """
    return {
        "event_type": "kill",
        "event_description": "ScoutOP eliminates Jonathan with M416",
        "player_name": "ScoutOP",
        "team_name": "Team Soul",
        "opponent_name": "Jonathan",
        "opponent_team": "GodLike",
        "weapon": "M416",
        "damage": 100,
        "headshot": True,
        "distance": 50.0,
        "zone_phase": 3,
        "teams_alive": 12,
        "style": "hinglish",
        "tone": "excited",
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BROADCAST FIXTURES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest_asyncio.fixture
async def mock_obs_client():
    """Create a mock OBS client"""
    client = AsyncMock()
    client.is_connected = Mock(return_value=True)
    client.send_command = AsyncMock(return_value=Mock(success=True, data={}))
    client.switch_scene = AsyncMock(return_value=True)
    client.start_streaming = AsyncMock(return_value=True)
    client.stop_streaming = AsyncMock(return_value=True)
    return client


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ANALYTICS FIXTURES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def sample_team_results():
    """Create sample team results for points calculation"""
    return [
        {"team_id": "team_001", "team_name": "Team A", "placement": 1, "kills": 10, "damage": 2500, "squad_wipes": 2, "first_blood": True, "players_lost": 0},
        {"team_id": "team_002", "team_name": "Team B", "placement": 2, "kills": 8, "damage": 2000, "squad_wipes": 1, "first_blood": False},
        {"team_id": "team_003", "team_name": "Team C", "placement": 3, "kills": 6, "damage": 1500, "squad_wipes": 0, "first_blood": False},
        {"team_id": "team_004", "team_name": "Team D", "placement": 4, "kills": 4, "damage": 1000, "squad_wipes": 1, "first_blood": False},
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# UTILITY FIXTURES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def sample_game_state():
    """Create a sample game state"""
    return {
        "match_time": 600.0,
        "zone_phase": 4,
        "teams_alive": 12,
        "players_alive": 48,
        "active_fights": [
            {
                "fight_id": "fight_001",
                "team_a": "team_001",
                "team_b": "team_002",
                "intensity": 75.0,
                "is_active": True,
            }
        ],
        "active_clutches": [],
        "current_feed": "team_pov",
        "scene_duration": 8.0,
        "replay_queue": [
            {"priority": 80, "clip_id": "clip_001"},
        ],
    }


@pytest.fixture
def mock_response():
    """Create a mock HTTP response"""
    response = Mock()
    response.status_code = 200
    response.json = Mock(return_value={"success": True})
    response.text = "OK"
    return response


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def assert_event_has_fields(event: dict, required_fields: List[str]):
    """Assert that an event dictionary has required fields"""
    for field in required_fields:
        assert field in event, f"Event missing required field: {field}"


def create_test_id(prefix: str = "test") -> str:
    """Create a test ID"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


async def wait_for_condition(condition, timeout: float = 5.0, interval: float = 0.1) -> bool:
    """Wait for a condition to be true"""
    import time
    start = time.time()
    
    while time.time() - start < timeout:
        if await condition() if asyncio.iscoroutinefunction(condition.__class__) else condition():
            return True
        await asyncio.sleep(interval)
    
    return False