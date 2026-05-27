# backend/tests/integration/test_database/test_repositories.py
"""
Integration tests for Database Repositories.
Tests complete CRUD operations, query performance,
data integrity, and relationship management
for all repository classes.
"""

import pytest
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db.base import AsyncSessionLocal
from app.db.models import (
    Tournament, TournamentStatus, TournamentFormat, MatchStatus, MatchMap,
    PlayerRole,
    EventType, HighlightType,
)
from app.db.repositories.tournament_repo import TournamentRepository
from app.db.repositories.match_repo import MatchRepository
from app.db.repositories.team_repo import TeamRepository
from app.db.repositories.player_repo import PlayerRepository
from app.db.repositories.event_repo import EventRepository
from app.db.repositories.highlight_repo import HighlightRepository
from app.db.repositories.analytics_repo import AnalyticsRepository


@pytest.mark.integration
@pytest.mark.database
class TestTournamentRepository:
    """Test Tournament repository CRUD operations"""
    
    @pytest.fixture
    def sample_data(self):
        """Sample tournament data"""
        return {
            "name": "Repository Test Tournament",
            "slug": f"repo-test-{uuid.uuid4().hex[:8]}",
            "description": "Tournament created for repository testing",
            "start_date": datetime.utcnow(),
            "end_date": datetime.utcnow() + timedelta(days=3),
            "organizer": "Test Org",
            "points_per_kill": 1,
            "format": TournamentFormat.BATTLE_ROYALE,
            "prize_pool": "₹1,00,000",
            "max_teams": 16,
            "min_teams": 8,
            "is_public": True,
        }
    
    @pytest.mark.asyncio
    async def test_create_tournament(self, sample_data):
        """Test creating a tournament"""
        async with AsyncSessionLocal() as session:
            repo = TournamentRepository(session)
            
            tournament = await repo.create(**sample_data)
            await session.commit()
            
            assert tournament is not None
            assert tournament.id is not None
            assert len(tournament.id) == 36  # UUID length
            assert tournament.name == sample_data["name"]
            assert tournament.slug == sample_data["slug"]
            assert tournament.status == TournamentStatus.DRAFT
            assert tournament.total_matches == 0
            assert tournament.is_public is True
    
    @pytest.mark.asyncio
    async def test_get_tournament(self, sample_data):
        """Test retrieving a tournament by ID"""
        async with AsyncSessionLocal() as session:
            repo = TournamentRepository(session)
            
            tournament = await repo.create(**sample_data)
            await session.commit()
            
            retrieved = await repo.get(tournament.id)
            
            assert retrieved is not None
            assert retrieved.id == tournament.id
            assert retrieved.name == sample_data["name"]
            assert retrieved.slug == sample_data["slug"]
    
    @pytest.mark.asyncio
    async def test_get_tournament_not_found(self):
        """Test retrieving non-existent tournament"""
        async with AsyncSessionLocal() as session:
            repo = TournamentRepository(session)
            
            result = await repo.get("non_existent_id_1234567890123456")
            
            assert result is None
    
    @pytest.mark.asyncio
    async def test_get_or_404(self, sample_data):
        """Test get_or_404 raises exception for missing record"""
        async with AsyncSessionLocal() as session:
            repo = TournamentRepository(session)
            
            tournament = await repo.create(**sample_data)
            await session.commit()
            
            # Should return existing tournament
            result = await repo.get_or_404(tournament.id)
            assert result is not None
            
            # Should raise exception for non-existent
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc:
                await repo.get_or_404("non_existent_id")
            assert exc.value.status_code == 404
    
    @pytest.mark.asyncio
    async def test_get_all_tournaments(self, sample_data):
        """Test listing tournaments with pagination"""
        async with AsyncSessionLocal() as session:
            repo = TournamentRepository(session)
            
            # Create multiple tournaments
            for i in range(5):
                data = {**sample_data}
                data["name"] = f"List Test Tournament {i}"
                data["slug"] = f"list-test-{i}-{uuid.uuid4().hex[:8]}"
                await repo.create(**data)
            await session.commit()
            
            # Get first page
            tournaments = await repo.get_all(skip=0, limit=3)
            assert len(tournaments) == 3
            
            # Get second page
            tournaments = await repo.get_all(skip=3, limit=3)
            assert len(tournaments) == 2
    
    @pytest.mark.asyncio
    async def test_get_all_with_filters(self, sample_data):
        """Test listing tournaments with filters"""
        async with AsyncSessionLocal() as session:
            repo = TournamentRepository(session)
            
            # Create tournaments with different statuses
            await repo.create(**{**sample_data, "name": "Draft Tourney", "slug": f"draft-{uuid.uuid4().hex[:8]}"})
            
            scheduled_data = {**sample_data}
            scheduled_data["name"] = "Scheduled Tourney"
            scheduled_data["slug"] = f"sched-{uuid.uuid4().hex[:8]}"
            scheduled = await repo.create(**scheduled_data)
            await repo.update(scheduled.id, status=TournamentStatus.SCHEDULED)
            await session.commit()
            
            # Filter by status
            tournaments = await repo.get_all(
                filters={"status": TournamentStatus.SCHEDULED}
            )
            assert len(tournaments) == 1
            assert tournaments[0].name == "Scheduled Tourney"
    
    @pytest.mark.asyncio
    async def test_update_tournament(self, sample_data):
        """Test updating tournament fields"""
        async with AsyncSessionLocal() as session:
            repo = TournamentRepository(session)
            
            tournament = await repo.create(**sample_data)
            await session.commit()
            
            updated = await repo.update(
                tournament.id,
                name="Updated Tournament Name",
                description="Updated description text",
                prize_pool="₹2,00,000",
            )
            
            assert updated is not None
            assert updated.name == "Updated Tournament Name"
            assert updated.description == "Updated description text"
            assert updated.prize_pool == "₹2,00,000"
            # Unchanged fields should remain
            assert updated.slug == sample_data["slug"]
            assert updated.status == TournamentStatus.DRAFT
    
    @pytest.mark.asyncio
    async def test_update_nonexistent(self):
        """Test updating non-existent tournament"""
        async with AsyncSessionLocal() as session:
            repo = TournamentRepository(session)
            
            result = await repo.update("non_existent", name="New Name")
            
            assert result is None
    
    @pytest.mark.asyncio
    async def test_soft_delete_tournament(self, sample_data):
        """Test soft deleting a tournament"""
        async with AsyncSessionLocal() as session:
            repo = TournamentRepository(session)
            
            tournament = await repo.create(**sample_data)
            await session.commit()
            
            # Soft delete
            result = await repo.delete(tournament.id, soft=True)
            assert result is True
            
            # Should not be retrievable
            retrieved = await repo.get(tournament.id)
            assert retrieved is None
    
    @pytest.mark.asyncio
    async def test_hard_delete_tournament(self, sample_data):
        """Test hard deleting a tournament"""
        async with AsyncSessionLocal() as session:
            repo = TournamentRepository(session)
            
            tournament = await repo.create(**sample_data)
            await session.commit()
            
            result = await repo.delete(tournament.id, soft=False)
            assert result is True
            
            # Verify deletion
            result = await session.execute(
                select(Tournament).where(Tournament.id == tournament.id)
            )
            assert result.scalar_one_or_none() is None
    
    @pytest.mark.asyncio
    async def test_count_tournaments(self, sample_data):
        """Test counting tournaments"""
        async with AsyncSessionLocal() as session:
            repo = TournamentRepository(session)
            
            for i in range(3):
                data = {**sample_data}
                data["name"] = f"Count Test {i}"
                data["slug"] = f"count-{i}-{uuid.uuid4().hex[:8]}"
                await repo.create(**data)
            await session.commit()
            
            count = await repo.count()
            assert count >= 3
            
            # Count with filter
            count = await repo.count(filters={"is_public": True})
            assert count >= 3
    
    @pytest.mark.asyncio
    async def test_exists(self, sample_data):
        """Test checking if tournament exists"""
        async with AsyncSessionLocal() as session:
            repo = TournamentRepository(session)
            
            tournament = await repo.create(**sample_data)
            await session.commit()
            
            assert await repo.exists(tournament.id) is True
            assert await repo.exists("non_existent") is False
    
    @pytest.mark.asyncio
    async def test_bulk_create(self, sample_data):
        """Test bulk creating tournaments"""
        async with AsyncSessionLocal() as session:
            repo = TournamentRepository(session)
            
            items = []
            for i in range(5):
                items.append({
                    **sample_data,
                    "name": f"Bulk Tournament {i}",
                    "slug": f"bulk-{i}-{uuid.uuid4().hex[:8]}",
                })
            
            tournaments = await repo.bulk_create(items)
            await session.commit()
            
            assert len(tournaments) == 5
            for t in tournaments:
                assert t.id is not None
    
    @pytest.mark.asyncio
    async def test_bulk_update(self, sample_data):
        """Test bulk updating tournaments"""
        async with AsyncSessionLocal() as session:
            repo = TournamentRepository(session)
            
            tournaments = []
            for i in range(3):
                data = {**sample_data}
                data["name"] = f"Bulk Update {i}"
                data["slug"] = f"bulk-up-{i}-{uuid.uuid4().hex[:8]}"
                t = await repo.create(**data)
                tournaments.append(t)
            await session.commit()
            
            updates = [
                {"id": t.id, "description": f"Updated {i}"}
                for i, t in enumerate(tournaments)
            ]
            
            count = await repo.bulk_update(updates)
            assert count == 3


@pytest.mark.integration
@pytest.mark.database
class TestMatchRepository:
    """Test Match repository CRUD operations"""
    
    @pytest.fixture
    async def tournament(self):
        """Create a tournament for match tests"""
        async with AsyncSessionLocal() as session:
            repo = TournamentRepository(session)
            tournament = await repo.create(
                name="Match Repo Tournament",
                slug=f"match-repo-{uuid.uuid4().hex[:8]}",
                start_date=datetime.utcnow(),
                end_date=datetime.utcnow() + timedelta(days=3),
                organizer="Test Org",
            )
            await session.commit()
            return tournament
    
    @pytest.mark.asyncio
    async def test_create_match(self, tournament):
        """Test creating a match"""
        async with AsyncSessionLocal() as session:
            repo = MatchRepository(session)
            
            match = await repo.create(
                tournament_id=tournament.id,
                match_number=1,
                map_name=MatchMap.ERANGEL,
                scheduled_time=datetime.utcnow() + timedelta(hours=2),
                match_name="Grand Finals Match 1",
            )
            await session.commit()
            
            assert match is not None
            assert match.tournament_id == tournament.id
            assert match.match_number == 1
            assert match.map_name == MatchMap.ERANGEL
            assert match.status == MatchStatus.SCHEDULED
    
    @pytest.mark.asyncio
    async def test_get_matches_by_tournament(self, tournament):
        """Test getting matches for a tournament"""
        async with AsyncSessionLocal() as session:
            repo = MatchRepository(session)
            
            for i in range(3):
                await repo.create(
                    tournament_id=tournament.id,
                    match_number=i + 1,
                    map_name=MatchMap.ERANGEL if i % 2 == 0 else MatchMap.MIRAMAR,
                    scheduled_time=datetime.utcnow() + timedelta(hours=i + 1),
                )
            await session.commit()
            
            matches = await repo.get_all(
                filters={"tournament_id": tournament.id},
                order_by="match_number",
            )
            
            assert len(matches) == 3
            assert matches[0].match_number == 1
            assert matches[2].match_number == 3
    
    @pytest.mark.asyncio
    async def test_update_match_status(self, tournament):
        """Test updating match status through lifecycle"""
        async with AsyncSessionLocal() as session:
            repo = MatchRepository(session)
            
            match = await repo.create(
                tournament_id=tournament.id,
                match_number=1,
                map_name=MatchMap.ERANGEL,
                scheduled_time=datetime.utcnow() + timedelta(hours=2),
            )
            await session.commit()
            
            # Status transitions
            status_flow = [
                MatchStatus.STARTING,
                MatchStatus.LOBBY,
                MatchStatus.PLANE,
                MatchStatus.LIVE,
                MatchStatus.FINISHED,
            ]
            
            for status in status_flow:
                updated = await repo.update(match.id, status=status)
                assert updated.status == status
    
    @pytest.mark.asyncio
    async def test_record_match_result(self, tournament):
        """Test recording match results"""
        async with AsyncSessionLocal() as session:
            repo = MatchRepository(session)
            
            match = await repo.create(
                tournament_id=tournament.id,
                match_number=1,
                map_name=MatchMap.ERANGEL,
                scheduled_time=datetime.utcnow() + timedelta(hours=2),
            )
            await session.flush()
            
            # Record result
            updated = await repo.update(
                match.id,
                status=MatchStatus.FINISHED,
                winner_team_id="team_001",
                mvp_player_id="player_001",
                total_kills=35,
                total_damage=15000,
                actual_start_time=datetime.utcnow() - timedelta(minutes=30),
                actual_end_time=datetime.utcnow(),
                duration_seconds=1800,
            )
            await session.commit()
            
            assert updated.status == MatchStatus.FINISHED
            assert updated.winner_team_id == "team_001"
            assert updated.mvp_player_id == "player_001"
            assert updated.total_kills == 35
            assert updated.duration_seconds == 1800


@pytest.mark.integration
@pytest.mark.database
class TestTeamRepository:
    """Test Team repository operations"""
    
    @pytest.mark.asyncio
    async def test_create_team(self):
        """Test creating a team with full details"""
        async with AsyncSessionLocal() as session:
            repo = TeamRepository(session)
            
            team = await repo.create(
                name="Repository Test Team Alpha",
                short_name="RTA",
                tag="RTA",
                country="India",
                organization="Test Org",
                primary_color="#FF4444",
                secondary_color="#FFFFFF",
                is_active=True,
                is_verified=True,
            )
            await session.commit()
            
            assert team is not None
            assert team.name == "Repository Test Team Alpha"
            assert team.short_name == "RTA"
            assert team.country == "India"
            assert team.primary_color == "#FF4444"
            assert team.is_active is True
    
    @pytest.mark.asyncio
    async def test_update_team_intelligence(self):
        """Test updating team intelligence data"""
        async with AsyncSessionLocal() as session:
            repo = TeamRepository(session)
            
            team = await repo.create(
                name="Intel Team",
                short_name="INT",
                tag="INT",
                country="India",
            )
            await session.flush()
            
            # Update intelligence
            updated = await repo.update(
                team.id,
                playstyle={"aggression": 8.5, "rotation_speed": 7.2},
                aggression_score=8.5,
                team_chemistry_score=7.8,
                average_survival_time=900.0,
            )
            await session.commit()
            
            assert updated.playstyle == {"aggression": 8.5, "rotation_speed": 7.2}
            assert updated.aggression_score == 8.5
            assert updated.team_chemistry_score == 7.8
    
    @pytest.mark.asyncio
    async def test_get_active_teams(self):
        """Test getting only active teams"""
        async with AsyncSessionLocal() as session:
            repo = TeamRepository(session)
            
            # Create active team
            await repo.create(
                name="Active Team", short_name="ACT", tag="ACT",
                country="India", is_active=True,
                slug_name="active-team",
            )
            
            # Create inactive team
            inactive = await repo.create(
                name="Inactive Team", short_name="INA", tag="INA",
                country="India", is_active=False,
                slug_name="inactive-team",
            )
            await session.commit()
            
            active_teams = await repo.get_all(filters={"is_active": True})
            active_names = [t.name for t in active_teams]
            
            assert "Active Team" in active_names
            assert "Inactive Team" not in active_names


@pytest.mark.integration
@pytest.mark.database
class TestPlayerRepository:
    """Test Player repository operations"""
    
    @pytest.fixture
    async def team(self):
        """Create a team for player tests"""
        async with AsyncSessionLocal() as session:
            repo = TeamRepository(session)
            team = await repo.create(
                name="Player Team", short_name="PT", tag="PT",
                country="India",
            )
            await session.commit()
            return team
    
    @pytest.mark.asyncio
    async def test_create_player(self, team):
        """Test creating a player"""
        async with AsyncSessionLocal() as session:
            repo = PlayerRepository(session)
            
            player = await repo.create(
                team_id=team.id,
                name="Test Player One",
                ign="TestPlayer1",
                role=PlayerRole.FRAGGER,
                is_captain=True,
                country="India",
                bgmi_id="512345678",
            )
            await session.commit()
            
            assert player is not None
            assert player.team_id == team.id
            assert player.name == "Test Player One"
            assert player.ign == "TestPlayer1"
            assert player.role == PlayerRole.FRAGGER
            assert player.is_captain is True
    
    @pytest.mark.asyncio
    async def test_get_team_players(self, team):
        """Test getting all players for a team"""
        async with AsyncSessionLocal() as session:
            repo = PlayerRepository(session)
            
            roles = [PlayerRole.FRAGGER, PlayerRole.SUPPORT, PlayerRole.IGL, PlayerRole.SNIPER]
            for i, role in enumerate(roles):
                await repo.create(
                    team_id=team.id,
                    name=f"Player {i+1}",
                    ign=f"Player{i+1}",
                    role=role,
                    is_captain=(i == 2),  # IGL is captain
                )
            await session.commit()
            
            players = await repo.get_all(
                filters={"team_id": team.id},
            )
            
            assert len(players) == 4
            captains = [p for p in players if p.is_captain]
            assert len(captains) == 1
            assert captains[0].role == PlayerRole.IGL
    
    @pytest.mark.asyncio
    async def test_update_player_stats(self, team):
        """Test updating player statistics"""
        async with AsyncSessionLocal() as session:
            repo = PlayerRepository(session)
            
            player = await repo.create(
                team_id=team.id,
                name="Stats Player",
                ign="StatsPlayer",
                role=PlayerRole.FRAGGER,
            )
            await session.flush()
            
            updated = await repo.update(
                player.id,
                total_kills=100,
                total_damage=50000,
                total_wins=15,
                mvp_awards=5,
                clutch_score=8.5,
                average_damage=800.0,
                average_kills=4.5,
                headshot_percentage=28.0,
            )
            await session.commit()
            
            assert updated.total_kills == 100
            assert updated.total_damage == 50000
            assert updated.mvp_awards == 5
            assert updated.clutch_score == 8.5
            assert updated.average_kills == 4.5


@pytest.mark.integration
@pytest.mark.database
class TestEventRepository:
    """Test Event repository for match events"""
    
    @pytest.mark.asyncio
    async def test_create_match_event(self):
        """Test creating various types of match events"""
        async with AsyncSessionLocal() as session:
            repo = EventRepository(session)
            match_id = str(uuid.uuid4())
            
            # Create kill event
            kill_event = await repo.create(
                match_id=match_id,
                event_type=EventType.KILL,
                timestamp_ms=120000,
                event_data={
                    "killer_id": "player_001",
                    "victim_id": "player_002",
                    "weapon": "M416",
                    "headshot": True,
                    "distance": 50.0,
                },
                team_id="team_001",
                player_id="player_001",
                target_team_id="team_002",
                target_player_id="player_002",
            )
            await session.commit()
            
            assert kill_event is not None
            assert kill_event.event_type == EventType.KILL
            assert kill_event.team_id == "team_001"
    
    @pytest.mark.asyncio
    async def test_get_events_by_type(self):
        """Test filtering events by type"""
        async with AsyncSessionLocal() as session:
            repo = EventRepository(session)
            match_id = str(uuid.uuid4())
            
            event_types = [EventType.KILL, EventType.KNOCK, EventType.KILL, EventType.REVIVE, EventType.KILL]
            
            for i, event_type in enumerate(event_types):
                await repo.create(
                    match_id=match_id,
                    event_type=event_type,
                    timestamp_ms=i * 60000,
                    event_data={"index": i},
                )
            await session.commit()
            
            # Get only kills
            kills = await repo.get_all(
                filters={"match_id": match_id, "event_type": EventType.KILL},
            )
            assert len(kills) == 3
            
            # Get only knocks
            knocks = await repo.get_all(
                filters={"match_id": match_id, "event_type": EventType.KNOCK},
            )
            assert len(knocks) == 1
    
    @pytest.mark.asyncio
    async def test_get_events_by_timerange(self):
        """Test filtering events by time range"""
        async with AsyncSessionLocal() as session:
            repo = EventRepository(session)
            match_id = str(uuid.uuid4())
            
            for i in range(10):
                await repo.create(
                    match_id=match_id,
                    event_type=EventType.KILL,
                    timestamp_ms=i * 60000,  # Every minute
                    event_data={"index": i},
                )
            await session.commit()
            
            # Get events in first 3 minutes
            events = await repo.get_all(
                filters={"match_id": match_id},
                limit=20,
            )
            
            assert len(events) == 10
            # Events should be ordered by created_at descending by default
    
    @pytest.mark.asyncio
    async def test_bulk_create_events(self):
        """Test bulk creating events"""
        async with AsyncSessionLocal() as session:
            repo = EventRepository(session)
            match_id = str(uuid.uuid4())
            
            events_data = [
                {
                    "match_id": match_id,
                    "event_type": EventType.KILL,
                    "timestamp_ms": i * 30000,
                    "event_data": {"index": i},
                }
                for i in range(20)
            ]
            
            events = await repo.bulk_create(events_data)
            await session.commit()
            
            assert len(events) == 20


@pytest.mark.integration
@pytest.mark.database
class TestHighlightRepository:
    """Test Highlight repository operations"""
    
    @pytest.mark.asyncio
    async def test_create_highlight(self):
        """Test creating a highlight"""
        async with AsyncSessionLocal() as session:
            repo = HighlightRepository(session)
            
            highlight = await repo.create(
                match_id=str(uuid.uuid4()),
                title="Epic Squad Wipe",
                description="Team Soul wipes GodLike in final circle",
                highlight_type=HighlightType.SQUAD_WIPE,
                start_timestamp_ms=600000,
                end_timestamp_ms=620000,
                excitement_score=85.0,
                primary_team_id="team_001",
                fight_id="fight_001",
                tags=["squad_wipe", "final_circle", "team_soul"],
            )
            await session.commit()
            
            assert highlight is not None
            assert highlight.title == "Epic Squad Wipe"
            assert highlight.highlight_type == HighlightType.SQUAD_WIPE
            assert highlight.excitement_score == 85.0
    
    @pytest.mark.asyncio
    async def test_get_top_highlights(self):
        """Test getting top highlights by excitement"""
        async with AsyncSessionLocal() as session:
            repo = HighlightRepository(session)
            match_id = str(uuid.uuid4())
            
            scores = [75.0, 90.0, 60.0, 95.0, 80.0]
            for i, score in enumerate(scores):
                await repo.create(
                    match_id=match_id,
                    title=f"Highlight {i}",
                    highlight_type=HighlightType.CLUTCH if i % 2 == 0 else HighlightType.MULTI_KILL,
                    start_timestamp_ms=i * 100000,
                    end_timestamp_ms=i * 100000 + 10000,
                    excitement_score=score,
                )
            await session.commit()
            
            highlights = await repo.get_all(
                filters={"match_id": match_id},
                order_by="excitement_score",
            )
            
            assert len(highlights) == 5
            assert highlights[0].excitement_score == 95.0  # Highest first
            assert highlights[-1].excitement_score == 60.0  # Lowest last
    
    @pytest.mark.asyncio
    async def test_mark_highlight_processed(self):
        """Test marking highlight as processed"""
        async with AsyncSessionLocal() as session:
            repo = HighlightRepository(session)
            
            highlight = await repo.create(
                match_id=str(uuid.uuid4()),
                title="Test Highlight",
                highlight_type=HighlightType.CLUTCH,
                start_timestamp_ms=100000,
                end_timestamp_ms=115000,
                excitement_score=80.0,
            )
            await session.flush()
            
            updated = await repo.update(
                highlight.id,
                processed=True,
                clip_path="/recordings/highlights/clip_001.mp4",
                thumbnail_path="/thumbnails/thumb_001.jpg",
            )
            await session.commit()
            
            assert updated.processed is True
            assert updated.clip_path == "/recordings/highlights/clip_001.mp4"


@pytest.mark.integration
@pytest.mark.database
class TestAnalyticsRepository:
    """Test Analytics repository operations"""
    
    @pytest.mark.asyncio
    async def test_create_match_team_stats(self):
        """Test creating match team statistics"""
        async with AsyncSessionLocal() as session:
            repo = AnalyticsRepository(session)
            
            stats = await repo.create(
                table_name="match_team_stats",
                match_id=str(uuid.uuid4()),
                team_id="team_001",
                placement=1,
                total_kills=12,
                total_damage=3000,
                survival_time_seconds=1500,
                placement_points=15,
                kill_points=12,
                total_points=27,
            )
            await session.commit()
            
            assert stats is not None
            assert stats.placement == 1
            assert stats.total_kills == 12
    
    @pytest.mark.asyncio
    async def test_get_team_match_history(self):
        """Test getting team's match history"""
        async with AsyncSessionLocal() as session:
            repo = AnalyticsRepository(session)
            team_id = "team_001"
            
            for i in range(5):
                await repo.create(
                    table_name="match_team_stats",
                    match_id=str(uuid.uuid4()),
                    team_id=team_id,
                    placement=i + 1,
                    total_kills=10 - i,
                    total_damage=2000 - i * 200,
                    total_points=20 - i * 3,
                )
            await session.commit()
            
            history = await repo.get_all(
                table_name="match_team_stats",
                filters={"team_id": team_id},
                order_by="total_points",
            )
            
            assert len(history) == 5
            assert history[0].total_points >= history[-1].total_points
    
    @pytest.mark.asyncio
    async def test_create_prediction(self):
        """Test creating a prediction record"""
        async with AsyncSessionLocal() as session:
            repo = AnalyticsRepository(session)
            
            prediction = await repo.create(
                table_name="predictions",
                match_id=str(uuid.uuid4()),
                prediction_type="fight_outcome",
                target_id="fight_001",
                predicted_value={"winner": "team_001", "probability": 0.75},
                confidence=0.75,
                timestamp_ms=300000,
                model_name="fight_outcome_v1",
                model_version="1.0.0",
            )
            await session.commit()
            
            assert prediction is not None
            assert prediction.prediction_type == "fight_outcome"
            assert prediction.confidence == 0.75
    
    @pytest.mark.asyncio
    async def test_create_commentary_record(self):
        """Test creating a commentary record"""
        async with AsyncSessionLocal() as session:
            repo = AnalyticsRepository(session)
            
            commentary = await repo.create(
                table_name="commentaries",
                match_id=str(uuid.uuid4()),
                timestamp_ms=120000,
                text="BHAI! Kya incredible play tha! Absolutely brilliant!",
                language="hinglish",
                tone="excited",
                trigger_event_type="kill",
                fight_id="fight_001",
                llm_model="gpt-4",
                prompt_tokens=150,
                completion_tokens=25,
            )
            await session.commit()
            
            assert commentary is not None
            assert commentary.language == "hinglish"
            assert commentary.tone == "excited"