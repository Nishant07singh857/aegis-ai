# backend/tests/unit/test_services/test_analytics_service.py
"""
Unit tests for Analytics Service.
Tests points calculation, leaderboard generation, heatmap creation,
report generation, trend analysis, and comparative analytics.
"""

import pytest
from unittest.mock import AsyncMock

from app.services.analytics_service import AnalyticsService
from app.analytics.points_calculator import TournamentPoints
from app.analytics.heatmap_generator import HeatmapType


@pytest.mark.unit
@pytest.mark.services
class TestAnalyticsServicePoints:
    """Test Analytics Service - Points & Scoring"""
    
    @pytest.fixture
    def mock_session(self):
        """Create mock database session"""
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        return session
    
    @pytest.fixture
    def service(self, mock_session):
        """Create analytics service instance"""
        return AnalyticsService(mock_session)
    
    @pytest.fixture
    def sample_team_results(self):
        """Sample team results for points calculation"""
        return [
            {"team_id": "team_001", "team_name": "Team Soul", "placement": 1, "kills": 12, "damage": 3000, "squad_wipes": 2, "first_blood": True, "players_lost": 0},
            {"team_id": "team_002", "team_name": "GodLike", "placement": 2, "kills": 10, "damage": 2500, "squad_wipes": 1, "first_blood": False},
            {"team_id": "team_003", "team_name": "Revenant", "placement": 3, "kills": 8, "damage": 2000, "squad_wipes": 1, "first_blood": False},
            {"team_id": "team_004", "team_name": "Orange Rock", "placement": 4, "kills": 6, "damage": 1500, "squad_wipes": 0, "first_blood": False},
            {"team_id": "team_005", "team_name": "TSM Entity", "placement": 5, "kills": 5, "damage": 1200, "squad_wipes": 1, "first_blood": False},
        ]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # POINTS CALCULATION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_calculate_match_points(self, service, sample_team_results):
        """Test calculating points for a match"""
        match_points = await service.calculate_match_points(
            match_id="match_001",
            team_results=sample_team_results,
        )
        
        assert match_points is not None
        assert len(match_points) == len(sample_team_results)
        
        # Winner should have most points
        winner_points = match_points.get("team_001", {})
        assert winner_points.get("placement_points", 0) > 0
        assert winner_points.get("kill_points", 0) > 0
    
    @pytest.mark.asyncio
    async def test_calculate_match_points_with_bonuses(self, service, sample_team_results):
        """Test bonus points calculation"""
        match_points = await service.calculate_match_points(
            match_id="match_001",
            team_results=sample_team_results,
        )
        
        # Team with first blood should have bonus points
        first_blood_team = match_points.get("team_001", {})
        assert first_blood_team.get("bonus_points", 0) > 0
        
        # Team with flawless victory (no players lost + winner) gets bonus
        assert first_blood_team.get("bonus_points", 0) >= 2  # First blood + flawless
    
    @pytest.mark.asyncio
    async def test_get_tournament_standings(self, service, sample_team_results):
        """Test getting tournament standings"""
        # Calculate points first
        await service.calculate_match_points("match_001", sample_team_results)
        
        standings = await service.get_tournament_standings()
        
        assert standings is not None
        assert len(standings) == len(sample_team_results)
        assert standings[0].team_name == "Team Soul"  # Winner should be first
    
    @pytest.mark.asyncio
    async def test_get_team_points(self, service, sample_team_results):
        """Test getting points for specific team"""
        await service.calculate_match_points("match_001", sample_team_results)
        
        team_points = await service.get_team_points("team_001")
        
        assert team_points is not None
        assert team_points.team_name == "Team Soul"
        assert team_points.grand_total > 0
    
    @pytest.mark.asyncio
    async def test_get_team_points_not_found(self, service):
        """Test getting points for unknown team"""
        team_points = await service.get_team_points("unknown_team")
        
        assert team_points is None
    
    @pytest.mark.asyncio
    async def test_get_match_points_breakdown(self, service, sample_team_results):
        """Test getting match points breakdown"""
        await service.calculate_match_points("match_001", sample_team_results)
        
        breakdown = await service.get_match_points("match_001")
        
        assert breakdown is not None
        assert len(breakdown) == len(sample_team_results)
        assert "team_001" in breakdown
    
    @pytest.mark.asyncio
    async def test_update_points_configuration(self, service):
        """Test updating points configuration"""
        await service.update_points_config(
            points_per_kill=2,
            placement_points={1: 20, 2: 15, 3: 12},
        )
        
        assert service.points_calculator.points_per_kill == 2
        assert service.points_calculator.placement_points.first == 20
    
    @pytest.mark.asyncio
    async def test_reset_points(self, service, sample_team_results):
        """Test resetting all points"""
        await service.calculate_match_points("match_001", sample_team_results)
        
        await service.reset_points()
        
        standings = await service.get_tournament_standings()
        assert len(standings) == 0


@pytest.mark.unit
@pytest.mark.services
class TestAnalyticsServiceLeaderboard:
    """Test Analytics Service - Leaderboard"""
    
    @pytest.fixture
    def mock_session(self):
        session = AsyncMock()
        session.execute = AsyncMock()
        return session
    
    @pytest.fixture
    def service(self, mock_session):
        return AnalyticsService(mock_session)
    
    @pytest.mark.asyncio
    async def test_generate_overall_leaderboard(self, service):
        """Test generating overall leaderboard"""
        # Setup some team points
        service.points_calculator.team_points = {
            "team_001": TournamentPoints(team_id="team_001", team_name="Team A", grand_total=100, total_kills=20, total_damage=5000),
            "team_002": TournamentPoints(team_id="team_002", team_name="Team B", grand_total=85, total_kills=15, total_damage=4000),
            "team_003": TournamentPoints(team_id="team_003", team_name="Team C", grand_total=70, total_kills=12, total_damage=3000),
        }
        
        leaderboard = await service.generate_overall_leaderboard()
        
        assert leaderboard is not None
        assert leaderboard.leaderboard_type.value == "overall"
        assert len(leaderboard.entries) == 3
        assert leaderboard.entries[0].team_name == "Team A"
        assert leaderboard.entries[0].rank == 1
    
    @pytest.mark.asyncio
    async def test_generate_kills_leaderboard(self, service):
        """Test generating kills leaderboard"""
        service.points_calculator.team_points = {
            "team_001": TournamentPoints(team_id="team_001", team_name="Team A", total_kills=25, total_damage=6000, grand_total=90),
            "team_002": TournamentPoints(team_id="team_002", team_name="Team B", total_kills=30, total_damage=5500, grand_total=85),
            "team_003": TournamentPoints(team_id="team_003", team_name="Team C", total_kills=15, total_damage=3500, grand_total=70),
        }
        
        leaderboard = await service.generate_kills_leaderboard()
        
        assert leaderboard is not None
        assert leaderboard.leaderboard_type.value == "kills"
        assert leaderboard.entries[0].team_name == "Team B"  # Most kills
        assert leaderboard.entries[0].total_kills == 30
    
    @pytest.mark.asyncio
    async def test_generate_damage_leaderboard(self, service):
        """Test generating damage leaderboard"""
        service.points_calculator.team_points = {
            "team_001": TournamentPoints(team_id="team_001", team_name="Team A", total_damage=6000, total_kills=20, grand_total=90),
            "team_002": TournamentPoints(team_id="team_002", team_name="Team B", total_damage=4500, total_kills=18, grand_total=80),
        }
        
        leaderboard = await service.generate_damage_leaderboard()
        
        assert leaderboard is not None
        assert leaderboard.leaderboard_type.value == "damage"
        assert leaderboard.entries[0].team_name == "Team A"
    
    @pytest.mark.asyncio
    async def test_get_top_teams_display(self, service):
        """Test getting formatted top teams for display"""
        service.points_calculator.team_points = {
            f"team_{i:03d}": TournamentPoints(
                team_id=f"team_{i:03d}",
                team_name=f"Team {i}",
                grand_total=100 - i * 5,
                total_kills=20 - i,
                total_damage=5000 - i * 200,
                matches_played=3,
                wwcd_count=1 if i == 0 else 0,
            )
            for i in range(5)
        }
        
        await service.generate_overall_leaderboard()
        
        display = await service.get_top_teams_display(limit=3)
        
        assert len(display) == 3
        assert "rank" in display[0]
        assert "team" in display[0]
        assert "points" in display[0]
        assert "kills" in display[0]
    
    @pytest.mark.asyncio
    async def test_rank_movement_tracking(self, service):
        """Test rank movement between updates"""
        service.points_calculator.team_points = {
            "team_001": TournamentPoints(team_id="team_001", team_name="Team A", grand_total=100, previous_rank=2),
            "team_002": TournamentPoints(team_id="team_002", team_name="Team B", grand_total=95, previous_rank=1),
        }
        
        leaderboard = await service.generate_overall_leaderboard()
        
        # Team A moved up from rank 2 to rank 1
        assert leaderboard.entries[0].team_name == "Team A"
        assert leaderboard.entries[0].rank_change == 1  # Moved up 1 position


@pytest.mark.unit
@pytest.mark.services
class TestAnalyticsServiceHeatmaps:
    """Test Analytics Service - Heatmaps"""
    
    @pytest.fixture
    def mock_session(self):
        session = AsyncMock()
        session.execute = AsyncMock()
        return session
    
    @pytest.fixture
    def service(self, mock_session):
        return AnalyticsService(mock_session)
    
    @pytest.mark.asyncio
    async def test_add_heatmap_position(self, service):
        """Test adding position to heatmap data"""
        await service.add_heatmap_position(
            heatmap_type=HeatmapType.LANDING,
            world_x=4000.0,
            world_y=5000.0,
            weight=2.0,
            team_id="team_001",
        )
        
        # Should not raise exception
        assert True
    
    @pytest.mark.asyncio
    async def test_add_positions_batch(self, service):
        """Test adding multiple positions at once"""
        positions = [(4000, 5000), (4100, 5100), (3900, 4900)]
        weights = [1.0, 0.8, 1.2]
        
        await service.add_heatmap_positions_batch(
            heatmap_type=HeatmapType.DEATH,
            positions=positions,
            weights=weights,
        )
        
        assert True
    
    @pytest.mark.asyncio
    async def test_generate_heatmap(self, service):
        """Test generating a heatmap"""
        # Add some positions first
        for _ in range(100):
            import random
            x = random.uniform(3000, 5000)
            y = random.uniform(4000, 6000)
            await service.add_heatmap_position(HeatmapType.LANDING, x, y)
        
        heatmap = await service.generate_heatmap(
            heatmap_type=HeatmapType.LANDING,
            map_name="erangel",
            resolution=50,
            title="Landing Heatmap",
        )
        
        assert heatmap is not None
        assert heatmap.heatmap_type == HeatmapType.LANDING
        assert heatmap.title == "Landing Heatmap"
        assert heatmap.data_points > 0
    
    @pytest.mark.asyncio
    async def test_generate_all_heatmaps(self, service):
        """Test generating all heatmap types"""
        # Add positions for multiple types
        for _ in range(50):
            import random
            x = random.uniform(2000, 6000)
            y = random.uniform(2000, 6000)
            await service.add_heatmap_position(HeatmapType.LANDING, x, y)
            await service.add_heatmap_position(HeatmapType.DEATH, x + 500, y + 500)
            await service.add_heatmap_position(HeatmapType.FIGHT, x - 200, y - 200)
        
        heatmaps = await service.generate_all_heatmaps("erangel")
        
        assert heatmaps is not None
        assert len(heatmaps) > 0
    
    @pytest.mark.asyncio
    async def test_get_heatmap_hotspots(self, service):
        """Test getting hotspots from a heatmap"""
        for _ in range(200):
            import random
            x = random.uniform(3500, 4500)
            y = random.uniform(4500, 5500)
            await service.add_heatmap_position(HeatmapType.LANDING, x, y, weight=2.0)
        
        heatmap = await service.generate_heatmap(HeatmapType.LANDING, "erangel", 40)
        hotspots = await service.get_heatmap_hotspots(heatmap.heatmap_id)
        
        assert hotspots is not None
        assert isinstance(hotspots, list)
    
    @pytest.mark.asyncio
    async def test_get_heatmap_data(self, service):
        """Test getting heatmap data for API"""
        for _ in range(50):
            import random
            await service.add_heatmap_position(
                HeatmapType.LANDING,
                random.uniform(3000, 5000),
                random.uniform(4000, 6000),
            )
        
        heatmap = await service.generate_heatmap(HeatmapType.LANDING, "erangel", 30)
        data = await service.get_heatmap_data(heatmap.heatmap_id)
        
        assert data is not None
        assert "type" in data
        assert "data_points" in data
        assert "hotspots" in data


@pytest.mark.unit
@pytest.mark.services
class TestAnalyticsServiceReports:
    """Test Analytics Service - Reports"""
    
    @pytest.fixture
    def mock_session(self):
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        return session
    
    @pytest.fixture
    def service(self, mock_session):
        return AnalyticsService(mock_session)
    
    @pytest.mark.asyncio
    async def test_generate_match_report(self, service):
        """Test generating a match report"""
        match_data = {
            "match_name": "Grand Finals Match 1",
            "map_name": "Erangel",
            "duration": 1800,
            "winner_team": "Team Soul",
            "mvp": "ScoutOP",
        }
        
        team_results = [
            {"team_id": "t1", "team_name": "Team Soul", "placement": 1, "kills": 12, "damage": 3000, "points": 27},
            {"team_id": "t2", "team_name": "GodLike", "placement": 2, "kills": 10, "damage": 2500, "points": 22},
        ]
        
        player_stats = [
            {"player_name": "ScoutOP", "team_name": "Team Soul", "kills": 8, "damage": 2000, "knocks": 3, "revives": 1, "assists": 2},
            {"player_name": "Mortal", "team_name": "Team Soul", "kills": 4, "damage": 1000, "knocks": 2, "revives": 2, "assists": 3},
        ]
        
        key_events = [
            {"type": "team_wipe", "importance": 0.9, "description": "Team Soul wipes GodLike"},
            {"type": "clutch_win", "importance": 0.95, "player": "ScoutOP", "description": "1v3 clutch"},
        ]
        
        report = await service.generate_match_report(
            match_id="match_001",
            match_data=match_data,
            team_results=team_results,
            player_stats=player_stats,
            key_events=key_events,
        )
        
        assert report is not None
        assert report.title is not None
        assert report.winner_team == "Team Soul"
        assert report.mvp_player == "ScoutOP"
        assert report.total_kills > 0
    
    @pytest.mark.asyncio
    async def test_generate_player_report(self, service):
        """Test generating a player report"""
        player_stats = {
            "kills": 8,
            "knocks": 3,
            "assists": 2,
            "damage": 2000,
            "headshots": 4,
            "revives": 1,
            "healing": 300,
            "survival_time": 1500,
            "placement": 1,
            "favorite_weapon": "M416",
            "weapon_accuracy": {"M416": 45.5, "AKM": 38.2},
        }
        
        historical_stats = {
            "avg_kills": 5.5,
            "avg_damage": 1500,
            "avg_survival": 1200,
        }
        
        report = await service.generate_player_report(
            player_id="player_001",
            player_name="ScoutOP",
            team_name="Team Soul",
            match_id="match_001",
            player_stats=player_stats,
            historical_stats=historical_stats,
        )
        
        assert report is not None
        assert report.player_name == "ScoutOP"
        assert report.kills == 8
        assert report.performance_score > 0
        assert report.rating in ["Excellent", "Good", "Average", "Below Average", "Poor"]
    
    @pytest.mark.asyncio
    async def test_get_match_report(self, service):
        """Test retrieving a match report"""
        match_data = {"match_name": "Test Match", "map_name": "Erangel", "duration": 1200, "winner_team": "Team A", "mvp": "Player1"}
        team_results = [{"team_name": "Team A", "placement": 1, "kills": 10, "points": 25}]
        player_stats = [{"player_name": "Player1", "kills": 6, "damage": 1500}]
        
        await service.generate_match_report("match_001", match_data, team_results, player_stats, [])
        
        report_data = await service.get_match_report("match_001")
        
        assert report_data is not None
        assert "title" in report_data
        assert "winner" in report_data
        assert "total_kills" in report_data
    
    @pytest.mark.asyncio
    async def test_get_player_reports(self, service):
        """Test retrieving player reports"""
        for i in range(3):
            await service.generate_player_report(
                player_id="player_001",
                player_name="ScoutOP",
                team_name="Team Soul",
                match_id=f"match_{i:03d}",
                player_stats={"kills": 5 + i, "damage": 1000 + i * 200, "survival_time": 1000 + i * 100},
            )
        
        reports = await service.get_player_reports("player_001", limit=5)
        
        assert len(reports) == 3
    
    @pytest.mark.asyncio
    async def test_generate_tournament_recap(self, service):
        """Test generating tournament recap report"""
        matches_data = [
            {"match_name": "Match 1", "winner_team": "Team A", "mvp": "Player1", "total_kills": 25},
            {"match_name": "Match 2", "winner_team": "Team B", "mvp": "Player2", "total_kills": 30},
            {"match_name": "Match 3", "winner_team": "Team A", "mvp": "Player3", "total_kills": 28},
        ]
        
        recap = await service.generate_tournament_recap(
            tournament_name="AEGIS Invitational",
            matches_data=matches_data,
            final_standings=[
                {"team_name": "Team A", "rank": 1, "total_points": 85},
                {"team_name": "Team B", "rank": 2, "total_points": 75},
            ],
        )
        
        assert recap is not None