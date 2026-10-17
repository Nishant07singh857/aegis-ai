# backend/tests/conftest.py
"""
AEGIS AI - Test Configuration
Shared fixtures, mock objects, and test utilities for the entire test suite.
"""

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
    """Create a test database session"""
    from app.db.base import AsyncSessionLocal
    
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def test_tournament(db_session):
    """Create a test tournament"""
    from app.db.models import Tournament, TournamentStatus, TournamentFormat
    
    tournament = Tournament(
        id=str(uuid.uuid4()),
        name="Test Tournament",
        slug="test-tournament",
        description="Test tournament for unit tests",
        status=TournamentStatus.DRAFT,
        format=TournamentFormat.BATTLE_ROYALE,
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
        short_name="TEST",
        tag="TST",
        country="India",
        primary_color="#FF0000",
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
            is_captain=(i == 0),
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
        match_name="Test Match 1",
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
    """Create a sample commentary prompt"""
    from app.ml.commentary.llm_chain import CommentaryPrompt, CommentaryStyle, CommentaryTone
    
    return CommentaryPrompt(
        event_type="kill",
        event_description="ScoutOP eliminates Jonathan with M416",
        player_name="ScoutOP",
        team_name="Team Soul",
        opponent_name="Jonathan",
        opponent_team="GodLike",
        weapon="M416",
        damage=100,
        headshot=True,
        distance=50.0,
        zone_phase=3,
        teams_alive=12,
        style=CommentaryStyle.HINGLISH,
        tone=CommentaryTone.EXCITED,
    )


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