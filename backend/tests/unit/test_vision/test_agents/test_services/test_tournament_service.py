# backend/tests/unit/test_services/test_tournament_service.py
"""
Unit tests for Tournament Service.
Tests tournament CRUD operations, team registration,
match scheduling, and tournament lifecycle management.
"""

import pytest
from unittest.mock import AsyncMock, Mock
from datetime import datetime, timedelta
import uuid

from app.services.tournament_service import TournamentService
from app.db.models import (
    Tournament, TournamentStatus, TournamentFormat,
    TournamentTeam, Team,
)


@pytest.mark.unit
@pytest.mark.services
class TestTournamentService:
    """Test TournamentService functionality"""
    
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
        """Create tournament service instance"""
        return TournamentService(mock_session)
    
    @pytest.fixture
    def sample_tournament_data(self):
        """Sample tournament creation data"""
        return {
            "name": "AEGIS Invitational 2024",
            "description": "Premier BGMI tournament",
            "start_date": datetime.utcnow() + timedelta(days=7),
            "end_date": datetime.utcnow() + timedelta(days=10),
            "organizer": "AEGIS AI",
            "points_per_kill": 1,
            "format": TournamentFormat.BATTLE_ROYALE,
            "prize_pool": "₹10,00,000",
            "max_teams": 16,
            "min_teams": 8,
            "is_public": True,
        }
    
    @pytest.fixture
    def sample_tournament(self):
        """Sample tournament instance"""
        return Tournament(
            id=str(uuid.uuid4()),
            name="Test Tournament",
            slug="test-tournament",
            status=TournamentStatus.DRAFT,
            start_date=datetime.utcnow(),
            end_date=datetime.utcnow() + timedelta(days=3),
            points_per_kill=1,
            organizer="Test Org",
        )
    
    @pytest.fixture
    def sample_team(self):
        """Sample team instance"""
        return Team(
            id=str(uuid.uuid4()),
            name="Team Soul",
            short_name="SOUL",
            tag="SOUL",
            country="India",
            is_active=True,
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TOURNAMENT CREATION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_create_tournament(self, service, mock_session, sample_tournament_data):
        """Test creating a new tournament"""
        mock_session.execute = AsyncMock()
        
        tournament = await service.create_tournament(**sample_tournament_data)
        
        assert tournament is not None
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_create_tournament_generates_slug(self, service, mock_session, sample_tournament_data):
        """Test tournament creation generates correct slug"""
        mock_session.execute = AsyncMock()
        
        tournament = await service.create_tournament(**sample_tournament_data)
        
        assert tournament is not None
        assert tournament.slug is not None
        assert " " not in tournament.slug
        assert tournament.slug == tournament.slug.lower()
    
    @pytest.mark.asyncio
    async def test_create_tournament_sets_defaults(self, service, mock_session, sample_tournament_data):
        """Test tournament creation sets correct defaults"""
        mock_session.execute = AsyncMock()
        
        tournament = await service.create_tournament(**sample_tournament_data)
        
        assert tournament is not None
        assert tournament.status == TournamentStatus.DRAFT
        assert tournament.total_matches == 0
        assert tournament.placement_points is not None
        assert len(tournament.placement_points) == 16
    
    @pytest.mark.asyncio
    async def test_create_tournament_with_duplicate_name(self, service, mock_session, sample_tournament_data):
        """Test creating tournament with duplicate name"""
        # Mock that tournament already exists
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=Tournament(
            id="existing", name=sample_tournament_data["name"]
        ))
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        with pytest.raises(Exception):
            await service.create_tournament(**sample_tournament_data)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TOURNAMENT RETRIEVAL TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_get_tournament(self, service, mock_session, sample_tournament):
        """Test getting a tournament by ID"""
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=sample_tournament)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        tournament = await service.get_tournament(sample_tournament.id)
        
        assert tournament is not None
        assert tournament.id == sample_tournament.id
        assert tournament.name == "Test Tournament"
    
    @pytest.mark.asyncio
    async def test_get_tournament_not_found(self, service, mock_session):
        """Test getting non-existent tournament"""
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=None)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        with pytest.raises(Exception):
            await service.get_tournament("non_existent_id")
    
    @pytest.mark.asyncio
    async def test_list_tournaments(self, service, mock_session):
        """Test listing tournaments"""
        tournaments = [
            Tournament(id=str(uuid.uuid4()), name=f"Tournament {i}")
            for i in range(3)
        ]
        
        mock_result = AsyncMock()
        mock_result.scalars = Mock(return_value=Mock(all=Mock(return_value=tournaments)))
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        result = await service.list_tournaments(limit=10, offset=0)
        
        assert len(result) == 3
    
    @pytest.mark.asyncio
    async def test_list_tournaments_by_status(self, service, mock_session):
        """Test listing tournaments filtered by status"""
        tournaments = [
            Tournament(id=str(uuid.uuid4()), name="Live Tourney", status=TournamentStatus.LIVE),
        ]
        
        mock_result = AsyncMock()
        mock_result.scalars = Mock(return_value=Mock(all=Mock(return_value=tournaments)))
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        result = await service.list_tournaments(status=TournamentStatus.LIVE)
        
        assert len(result) == 1
        assert result[0].status == TournamentStatus.LIVE
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TOURNAMENT UPDATE TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_update_tournament(self, service, mock_session, sample_tournament):
        """Test updating tournament details"""
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=sample_tournament)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        updated = await service.update_tournament(
            tournament_id=sample_tournament.id,
            name="Updated Tournament Name",
            description="Updated description",
        )
        
        assert updated is not None
        assert updated.name == "Updated Tournament Name"
        assert updated.description == "Updated description"
    
    @pytest.mark.asyncio
    async def test_update_tournament_status(self, service, mock_session, sample_tournament):
        """Test updating tournament status"""
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=sample_tournament)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        updated = await service.update_tournament_status(
            tournament_id=sample_tournament.id,
            status=TournamentStatus.SCHEDULED,
        )
        
        assert updated is not None
        assert updated.status == TournamentStatus.SCHEDULED
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TEAM REGISTRATION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_register_team(self, service, mock_session, sample_tournament, sample_team):
        """Test registering a team for tournament"""
        # Mock tournament retrieval
        mock_tournament_result = AsyncMock()
        mock_tournament_result.scalar_one_or_none = Mock(return_value=sample_tournament)
        
        # Mock team retrieval
        mock_team_result = AsyncMock()
        mock_team_result.scalar_one_or_none = Mock(return_value=sample_team)
        
        # Mock existing registration check (no existing)
        mock_existing_result = AsyncMock()
        mock_existing_result.scalar_one_or_none = Mock(return_value=None)
        
        mock_session.execute = AsyncMock()
        mock_session.execute.side_effect = [
            mock_tournament_result,
            mock_team_result,
            mock_existing_result,
        ]
        
        registration = await service.register_team(
            tournament_id=sample_tournament.id,
            team_id=sample_team.id,
        )
        
        assert registration is not None
        mock_session.add.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_register_team_already_registered(self, service, mock_session, sample_tournament, sample_team):
        """Test registering a team that's already registered"""
        existing_reg = TournamentTeam(
            id=str(uuid.uuid4()),
            tournament_id=sample_tournament.id,
            team_id=sample_team.id,
        )
        
        mock_tournament_result = AsyncMock()
        mock_tournament_result.scalar_one_or_none = Mock(return_value=sample_tournament)
        
        mock_team_result = AsyncMock()
        mock_team_result.scalar_one_or_none = Mock(return_value=sample_team)
        
        mock_existing_result = AsyncMock()
        mock_existing_result.scalar_one_or_none = Mock(return_value=existing_reg)
        
        mock_session.execute = AsyncMock()
        mock_session.execute.side_effect = [
            mock_tournament_result,
            mock_team_result,
            mock_existing_result,
        ]
        
        with pytest.raises(Exception):
            await service.register_team(
                tournament_id=sample_tournament.id,
                team_id=sample_team.id,
            )
    
    @pytest.mark.asyncio
    async def test_unregister_team(self, service, mock_session):
        """Test unregistering a team"""
        registration = TournamentTeam(
            id=str(uuid.uuid4()),
            tournament_id="t_id",
            team_id="team_id",
        )
        
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=registration)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        result = await service.unregister_team("t_id", "team_id")
        
        assert result is True
        mock_session.delete.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_get_registered_teams(self, service, mock_session, sample_tournament):
        """Test getting registered teams for tournament"""
        registrations = [
            TournamentTeam(id=str(uuid.uuid4()), tournament_id=sample_tournament.id, team_id=f"team_{i}")
            for i in range(5)
        ]
        
        mock_result = AsyncMock()
        mock_result.scalars = Mock(return_value=Mock(all=Mock(return_value=registrations)))
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        teams = await service.get_registered_teams(sample_tournament.id)
        
        assert len(teams) == 5
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TOURNAMENT LIFECYCLE TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_start_tournament(self, service, mock_session, sample_tournament):
        """Test starting a tournament"""
        sample_tournament.status = TournamentStatus.SCHEDULED
        
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=sample_tournament)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        result = await service.start_tournament(sample_tournament.id)
        
        assert result is not None
        assert result.status == TournamentStatus.LIVE
    
    @pytest.mark.asyncio
    async def test_start_tournament_not_scheduled(self, service, mock_session, sample_tournament):
        """Test starting tournament in wrong status"""
        sample_tournament.status = TournamentStatus.DRAFT  # Not scheduled
        
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=sample_tournament)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        with pytest.raises(Exception):
            await service.start_tournament(sample_tournament.id)
    
    @pytest.mark.asyncio
    async def test_complete_tournament(self, service, mock_session, sample_tournament):
        """Test completing a tournament"""
        sample_tournament.status = TournamentStatus.LIVE
        
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=sample_tournament)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        result = await service.complete_tournament(sample_tournament.id)
        
        assert result is not None
        assert result.status == TournamentStatus.COMPLETED
    
    @pytest.mark.asyncio
    async def test_cancel_tournament(self, service, mock_session, sample_tournament):
        """Test cancelling a tournament"""
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=sample_tournament)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        result = await service.cancel_tournament(sample_tournament.id)
        
        assert result is not None
        assert result.status == TournamentStatus.CANCELLED
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TOURNAMENT STATISTICS TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_get_tournament_standings(self, service, mock_session, sample_tournament):
        """Test getting tournament standings"""
        registrations = [
            TournamentTeam(
                id=str(uuid.uuid4()),
                tournament_id=sample_tournament.id,
                team_id=f"team_{i}",
                total_points=100 - i * 10,
                total_kills=20 - i * 2,
                rank=i + 1,
            )
            for i in range(5)
        ]
        
        mock_result = AsyncMock()
        mock_result.scalars = Mock(return_value=Mock(all=Mock(return_value=registrations)))
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        standings = await service.get_tournament_standings(sample_tournament.id)
        
        assert len(standings) == 5
        assert standings[0].rank == 1
        assert standings[0].total_points == 100
    
    @pytest.mark.asyncio
    async def test_get_tournament_stats(self, service, mock_session, sample_tournament):
        """Test getting tournament statistics"""
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=sample_tournament)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        # Mock match count
        mock_count_result = AsyncMock()
        mock_count_result.scalar = Mock(return_value=6)
        mock_session.execute = AsyncMock()
        mock_session.execute.side_effect = [
            mock_result,
            mock_count_result,  # match count
            mock_count_result,  # team count
            mock_count_result,  # total kills
        ]
        
        stats = await service.get_tournament_stats(sample_tournament.id)
        
        assert stats is not None
        assert "total_matches" in stats
        assert "total_teams" in stats
        assert "total_kills" in stats
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TOURNAMENT DELETION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_delete_tournament(self, service, mock_session, sample_tournament):
        """Test deleting a tournament (soft delete)"""
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=sample_tournament)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        result = await service.delete_tournament(sample_tournament.id)
        
        assert result is True
        # Should soft delete
        assert sample_tournament.is_deleted is True
        assert sample_tournament.deleted_at is not None
    
    @pytest.mark.asyncio
    async def test_hard_delete_tournament(self, service, mock_session, sample_tournament):
        """Test hard deleting a tournament"""
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = Mock(return_value=sample_tournament)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        result = await service.delete_tournament(sample_tournament.id, soft=False)
        
        assert result is True
        mock_session.delete.assert_called_once()