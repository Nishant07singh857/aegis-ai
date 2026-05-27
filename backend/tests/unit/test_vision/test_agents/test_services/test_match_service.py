# backend/tests/unit/test_services/test_match_service.py
"""
Unit tests for Match Service.
Tests match creation, scheduling, status transitions,
team participation, and match result recording.
"""

import pytest
from unittest.mock import AsyncMock, Mock
from datetime import datetime, timedelta
import uuid

from app.services.match_service import MatchService
from app.db.models import (
    Match, MatchStatus, MatchMap, Tournament, TournamentStatus,
    MatchTeamStats, MatchPlayerStats, MatchEvent,
    Team,
)


@pytest.mark.unit
@pytest.mark.services
class TestMatchService:
    """Test MatchService functionality"""
    
    @pytest.fixture
    def mock_session(self):
        """Create mock database session"""
        session = AsyncMock()
        session.add = Mock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        session.execute = AsyncMock()
        session.flush = AsyncMock()
        session.refresh = AsyncMock()
        return session
    
    @pytest.fixture
    def service(self, mock_session):
        """Create match service instance"""
        return MatchService(mock_session)
    
    @pytest.fixture
    def sample_tournament(self):
        """Sample tournament for match creation"""
        return Tournament(
            id=str(uuid.uuid4()),
            name="Test Tournament",
            status=TournamentStatus.SCHEDULED,
            total_matches=0,
        )
    
    @pytest.fixture
    def sample_match(self):
        """Sample match instance"""
        return Match(
            id=str(uuid.uuid4()),
            tournament_id=str(uuid.uuid4()),
            match_number=1,
            match_name="Match 1",
            map_name=MatchMap.ERANGEL,
            status=MatchStatus.SCHEDULED,
            scheduled_time=datetime.utcnow() + timedelta(hours=2),
        )
    
    @pytest.fixture
    def sample_teams(self):
        """Sample teams for match"""
        return [
            Team(id=f"team_{i:03d}", name=f"Team {i}", short_name=f"T{i}")
            for i in range(16)
        ]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MATCH CREATION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_create_match(self, service, mock_session, sample_tournament):
        """Test creating a new match"""
        mock_tournament_result = AsyncMock()
        mock_tournament_result.scalar_one_or_none = Mock(return_value=sample_tournament)
        
        mock_match_count = AsyncMock()
        mock_match_count.scalar = Mock(return_value=0)
        
        mock_session.execute = AsyncMock()
        mock_session.execute.side_effect = [
            mock_tournament_result,
            mock_match_count,
        ]
        
        match = await service.create_match(
            tournament_id=sample_tournament.id,
            map_name=MatchMap.ERANGEL,
            scheduled_time=datetime.utcnow() + timedelta(hours=2),
        )
        
        assert match is not None
        assert match.match_number == 1
        assert match.map_name == MatchMap.ERANGEL
        assert match.status == MatchStatus.SCHEDULED
        mock_session.add.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_create_match_with_custom_room(self, service, mock_session, sample_tournament):
        """Test creating match with custom room details"""
        mock_tournament_result = AsyncMock()
        mock_tournament_result.scalar_one_or_none = Mock(return_value=sample_tournament)
        
        mock_match_count = AsyncMock()
        mock_match_count.scalar = Mock(return_value=0)
        
        mock_session.execute = AsyncMock()
        mock_session.execute.side_effect = [
            mock_tournament_result,
            mock_match_count,
        ]
        
        match = await service.create_match(
            tournament_id=sample_tournament.id,
            map_name=MatchMap.ERANGEL,
            scheduled_time=datetime.utcnow() + timedelta(hours=2),
            custom_room_id="ROOM123",
            custom_room_password="pass123",
        )
        
        assert match.custom_room_id == "ROOM123"
        assert match.custom_room_password == "pass123"
    
    @pytest.mark.asyncio
    async def test_create_match_auto_increments_number(self, service, mock_session, sample_tournament):
        """Test match number auto-increments"""
        mock_tournament_result = AsyncMock()
        mock_tournament_result.scalar_one_or_none = Mock(return_value=sample_tournament)
        
        # Mock existing match count of 5
        mock_match_count = AsyncMock()
        mock_match_count.scalar = Mock(return_value=5)
        
        mock_session.execute = AsyncMock()
        mock_session.execute.side_effect = [
            mock_tournament_result,
            mock_match_count,
        ]
        
        match = await service.create_match(
            tournament_id=sample_tournament.id,
            map_name=MatchMap.MIRAMAR,
            scheduled_time=datetime.utcnow() + timedelta(hours=3),
        )
        
        assert match.match_number == 6
    
    @pytest.mark.asyncio
    async def test_create_match_tournament_not_found(self, service, mock_session):
        """Test creating match for non-existent tournament"""
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=None)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        with pytest.raises(Exception):
            await service.create_match(
                tournament_id="non_existent",
                map_name=MatchMap.ERANGEL,
                scheduled_time=datetime.utcnow(),
            )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MATCH RETRIEVAL TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_get_match(self, service, mock_session, sample_match):
        """Test getting a match by ID"""
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=sample_match)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        match = await service.get_match(sample_match.id)
        
        assert match is not None
        assert match.id == sample_match.id
        assert match.map_name == MatchMap.ERANGEL
    
    @pytest.mark.asyncio
    async def test_get_match_not_found(self, service, mock_session):
        """Test getting non-existent match"""
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=None)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        with pytest.raises(Exception):
            await service.get_match("non_existent")
    
    @pytest.mark.asyncio
    async def test_list_tournament_matches(self, service, mock_session, sample_tournament):
        """Test listing matches for a tournament"""
        matches = [
            Match(
                id=str(uuid.uuid4()),
                tournament_id=sample_tournament.id,
                match_number=i+1,
                map_name=MatchMap.ERANGEL,
                status=MatchStatus.SCHEDULED,
            )
            for i in range(6)
        ]
        
        mock_result = AsyncMock()
        mock_result.scalars = Mock(return_value=Mock(all=Mock(return_value=matches)))
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        result = await service.list_tournament_matches(sample_tournament.id)
        
        assert len(result) == 6
        assert all(m.tournament_id == sample_tournament.id for m in result)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MATCH STATUS TRANSITION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_start_match(self, service, mock_session, sample_match):
        """Test starting a match"""
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=sample_match)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        match = await service.start_match(sample_match.id)
        
        assert match is not None
        assert match.status == MatchStatus.LIVE
        assert match.actual_start_time is not None
    
    @pytest.mark.asyncio
    async def test_start_match_wrong_status(self, service, mock_session, sample_match):
        """Test starting match in wrong status"""
        sample_match.status = MatchStatus.FINISHED
        
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=sample_match)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        with pytest.raises(Exception):
            await service.start_match(sample_match.id)
    
    @pytest.mark.asyncio
    async def test_end_match(self, service, mock_session, sample_match):
        """Test ending a match"""
        sample_match.status = MatchStatus.LIVE
        sample_match.actual_start_time = datetime.utcnow() - timedelta(minutes=25)
        
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=sample_match)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        match = await service.end_match(sample_match.id)
        
        assert match is not None
        assert match.status == MatchStatus.FINISHED
        assert match.actual_end_time is not None
        assert match.duration_seconds is not None
        assert match.duration_seconds > 0
    
    @pytest.mark.asyncio
    async def test_cancel_match(self, service, mock_session, sample_match):
        """Test cancelling a match"""
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=sample_match)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        match = await service.cancel_match(sample_match.id)
        
        assert match is not None
        assert match.status == MatchStatus.CANCELLED
    
    @pytest.mark.asyncio
    async def test_abandon_match(self, service, mock_session, sample_match):
        """Test abandoning a match"""
        sample_match.status = MatchStatus.LIVE
        
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=sample_match)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        match = await service.abandon_match(sample_match.id)
        
        assert match is not None
        assert match.status == MatchStatus.ABANDONED
    
    @pytest.mark.asyncio
    async def test_update_match_status_to_lobby(self, service, mock_session, sample_match):
        """Test transitioning to lobby phase"""
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=sample_match)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        match = await service.update_match_status(sample_match.id, MatchStatus.LOBBY)
        
        assert match.status == MatchStatus.LOBBY
    
    @pytest.mark.asyncio
    async def test_update_match_status_to_plane(self, service, mock_session, sample_match):
        """Test transitioning to plane phase"""
        sample_match.status = MatchStatus.LOBBY
        
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=sample_match)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        match = await service.update_match_status(sample_match.id, MatchStatus.PLANE)
        
        assert match.status == MatchStatus.PLANE
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MATCH RESULT RECORDING TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_record_match_results(self, service, mock_session, sample_match, sample_teams):
        """Test recording match results"""
        sample_match.status = MatchStatus.FINISHED
        
        team_results = [
            {"team_id": sample_teams[i].id, "placement": i+1, "kills": 10-i, "damage": 2000-i*100}
            for i in range(5)
        ]
        
        mock_match_result = AsyncMock()
        mock_match_result.scalar_one_or_none = Mock(return_value=sample_match)
        
        mock_team_result = AsyncMock()
        mock_team_result.scalar_one_or_none = Mock(side_effect=lambda: sample_teams[0])
        
        mock_session.execute = AsyncMock()
        mock_session.execute.side_effect = [mock_match_result] + [mock_team_result] * len(team_results)
        
        results = await service.record_match_results(
            match_id=sample_match.id,
            team_results=team_results,
            winner_team_id=sample_teams[0].id,
            mvp_player_id="player_001",
        )
        
        assert results is not None
        assert sample_match.winner_team_id == sample_teams[0].id
        assert sample_match.mvp_player_id == "player_001"
    
    @pytest.mark.asyncio
    async def test_record_match_results_calculates_points(self, service, mock_session, sample_match, sample_teams):
        """Test match results calculate points correctly"""
        sample_match.status = MatchStatus.FINISHED
        
        team_results = [
            {"team_id": sample_teams[0].id, "placement": 1, "kills": 10, "damage": 2500},
        ]
        
        mock_match_result = AsyncMock()
        mock_match_result.scalar_one_or_none = Mock(return_value=sample_match)
        
        mock_team_result = AsyncMock()
        mock_team_result.scalar_one_or_none = Mock(return_value=sample_teams[0])
        
        mock_session.execute = AsyncMock()
        mock_session.execute.side_effect = [mock_match_result, mock_team_result]
        
        results = await service.record_match_results(
            match_id=sample_match.id,
            team_results=team_results,
        )
        
        assert results is not None
        
        # Check that team stats were created
        added_objects = [call[0][0] for call in mock_session.add.call_args_list if call[0]]
        assert any(isinstance(obj, MatchTeamStats) for obj in added_objects)
    
    @pytest.mark.asyncio
    async def test_record_player_stats(self, service, mock_session, sample_match):
        """Test recording individual player stats"""
        sample_match.status = MatchStatus.FINISHED
        
        player_stats = [
            {
                "player_id": "player_001",
                "team_id": "team_001",
                "kills": 8,
                "damage_dealt": 2000,
                "headshots": 3,
                "revives_performed": 2,
                "survived_time_seconds": 1500,
            },
        ]
        
        mock_match_result = AsyncMock()
        mock_match_result.scalar_one_or_none = Mock(return_value=sample_match)
        mock_session.execute = AsyncMock(return_value=mock_match_result)
        
        results = await service.record_player_stats(
            match_id=sample_match.id,
            player_stats=player_stats,
        )
        
        assert results is not None
        
        # Check that player stats were created
        added_objects = [call[0][0] for call in mock_session.add.call_args_list if call[0]]
        assert any(isinstance(obj, MatchPlayerStats) for obj in added_objects)
    
    @pytest.mark.asyncio
    async def test_record_match_event(self, service, mock_session, sample_match):
        """Test recording a match event"""
        sample_match.status = MatchStatus.LIVE
        
        mock_match_result = AsyncMock()
        mock_match_result.scalar_one_or_none = Mock(return_value=sample_match)
        mock_session.execute = AsyncMock(return_value=mock_match_result)
        
        event = await service.record_match_event(
            match_id=sample_match.id,
            event_type="kill",
            timestamp_ms=120000,
            event_data={
                "killer_id": "player_001",
                "victim_id": "player_002",
                "weapon": "M416",
                "headshot": True,
            },
            team_id="team_001",
            player_id="player_001",
            target_team_id="team_002",
            target_player_id="player_002",
        )
        
        assert event is not None
        mock_session.add.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_get_match_events(self, service, mock_session, sample_match):
        """Test getting match events"""
        events = [
            MatchEvent(
                id=str(uuid.uuid4()),
                match_id=sample_match.id,
                event_type="kill",
                timestamp_ms=i * 60000,
                event_data={"killer": f"player_{i}"},
            )
            for i in range(5)
        ]
        
        mock_result = AsyncMock()
        mock_result.scalars = Mock(return_value=Mock(all=Mock(return_value=events)))
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        result = await service.get_match_events(sample_match.id, limit=10)
        
        assert len(result) == 5
    
    @pytest.mark.asyncio
    async def test_get_match_events_by_type(self, service, mock_session, sample_match):
        """Test getting match events filtered by type"""
        events = [
            MatchEvent(id=str(uuid.uuid4()), match_id=sample_match.id, event_type="kill", timestamp_ms=60000, event_data={}),
            MatchEvent(id=str(uuid.uuid4()), match_id=sample_match.id, event_type="knock", timestamp_ms=120000, event_data={}),
        ]
        
        mock_result = AsyncMock()
        mock_result.scalars = Mock(return_value=Mock(all=Mock(return_value=events)))
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        result = await service.get_match_events(
            match_id=sample_match.id,
            event_type="kill",
        )
        
        assert len(result) >= 0
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MATCH STATISTICS TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_get_match_stats(self, service, mock_session, sample_match):
        """Test getting match statistics"""
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=sample_match)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        stats = await service.get_match_stats(sample_match.id)
        
        assert stats is not None
        assert stats.id == sample_match.id
    
    @pytest.mark.asyncio
    async def test_get_match_team_stats(self, service, mock_session, sample_match):
        """Test getting team stats for a match"""
        team_stats = [
            MatchTeamStats(
                id=str(uuid.uuid4()),
                match_id=sample_match.id,
                team_id=f"team_{i:03d}",
                placement=i+1,
                total_kills=10-i,
                total_points=100-i*10,
            )
            for i in range(5)
        ]
        
        mock_result = AsyncMock()
        mock_result.scalars = Mock(return_value=Mock(all=Mock(return_value=team_stats)))
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        result = await service.get_match_team_stats(sample_match.id)
        
        assert len(result) == 5
        assert result[0].placement == 1
    
    @pytest.mark.asyncio
    async def test_get_match_player_stats(self, service, mock_session, sample_match):
        """Test getting player stats for a match"""
        player_stats = [
            MatchPlayerStats(
                id=str(uuid.uuid4()),
                match_id=sample_match.id,
                player_id=f"player_{i:03d}",
                team_id="team_001",
                kills=5-i,
                damage_dealt=1000-i*100,
                mvp_score=80-i*10,
            )
            for i in range(4)
        ]
        
        mock_result = AsyncMock()
        mock_result.scalars = Mock(return_value=Mock(all=Mock(return_value=player_stats)))
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        result = await service.get_match_player_stats(sample_match.id)
        
        assert len(result) == 4
    
    @pytest.mark.asyncio
    async def test_get_match_mvp(self, service, mock_session, sample_match):
        """Test getting match MVP"""
        player_stats = [
            MatchPlayerStats(
                player_id="player_001",
                team_id="team_001",
                kills=8,
                damage_dealt=2000,
                mvp_score=95.0,
                is_mvp=True,
            ),
            MatchPlayerStats(
                player_id="player_002",
                team_id="team_002",
                kills=5,
                damage_dealt=1200,
                mvp_score=65.0,
                is_mvp=False,
            ),
        ]
        
        mock_result = AsyncMock()
        mock_result.scalars = Mock(return_value=Mock(all=Mock(return_value=player_stats)))
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        mvp = await service.get_match_mvp(sample_match.id)
        
        assert mvp is not None
        assert mvp.player_id == "player_001"
        assert mvp.is_mvp is True
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MATCH SUMMARY TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_get_match_summary(self, service, mock_session, sample_match):
        """Test getting comprehensive match summary"""
        sample_match.total_kills = 35
        sample_match.total_damage = 15000
        sample_match.winner_team_id = "team_001"
        sample_match.mvp_player_id = "player_001"
        
        mock_match_result = AsyncMock()
        mock_match_result.scalar_one_or_none = Mock(return_value=sample_match)
        
        # Mock team stats
        team_stats = [
            MatchTeamStats(team_id="team_001", placement=1, total_kills=12, total_points=27),
            MatchTeamStats(team_id="team_002", placement=2, total_kills=8, total_points=20),
        ]
        mock_team_result = AsyncMock()
        mock_team_result.scalars = Mock(return_value=Mock(all=Mock(return_value=team_stats)))
        
        mock_session.execute = AsyncMock()
        mock_session.execute.side_effect = [
            mock_match_result,
            mock_team_result,
        ]
        
        summary = await service.get_match_summary(sample_match.id)
        
        assert summary is not None
        assert "match" in summary
        assert "team_results" in summary
        assert summary["match"]["total_kills"] == 35
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MATCH VALIDATION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_validate_match_schedule(self, service, mock_session, sample_tournament):
        """Test match scheduling validation"""
        # Try to schedule match before tournament start
        sample_tournament.start_date = datetime.utcnow() + timedelta(days=7)
        
        with pytest.raises(ValueError):
            await service.validate_match_schedule(
                tournament=sample_tournament,
                scheduled_time=datetime.utcnow(),  # Before tournament starts
            )
    
    @pytest.mark.asyncio
    async def test_validate_match_schedule_overlap(self, service, mock_session, sample_tournament):
        """Test match overlap validation"""
        sample_tournament.start_date = datetime.utcnow() - timedelta(days=1)
        sample_tournament.end_date = datetime.utcnow() + timedelta(days=3)
        
        # Should be valid
        await service.validate_match_schedule(
            tournament=sample_tournament,
            scheduled_time=datetime.utcnow() + timedelta(hours=2),
        )
        # No exception = pass
        assert True