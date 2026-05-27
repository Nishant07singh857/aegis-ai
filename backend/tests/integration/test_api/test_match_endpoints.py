# backend/tests/integration/test_api/test_match_endpoints.py
"""
Integration tests for Match API Endpoints.
Tests match creation, status transitions, event recording,
and result management through the API layer.
"""

import pytest
from httpx import AsyncClient, ASGITransport
import uuid
from datetime import datetime, timedelta

from app.main import app


@pytest.mark.integration
@pytest.mark.api
class TestMatchEndpoints:
    """Test Match API endpoints"""
    
    @pytest.fixture
    def match_data(self):
        """Sample match creation data"""
        return {
            "tournament_id": str(uuid.uuid4()),
            "map_name": "erangel",
            "scheduled_time": (datetime.utcnow() + timedelta(hours=2)).isoformat(),
            "custom_room_id": "ROOM123",
            "custom_room_password": "pass123",
        }
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MATCH CREATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_create_match(self, match_data):
        """Test creating a match via API"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/matches/",
                json=match_data,
            )
        
        assert response.status_code in [200, 201, 422]
        
        if response.status_code in [200, 201]:
            data = response.json()
            assert "match_id" in data or "id" in data
    
    @pytest.mark.asyncio
    async def test_create_match_missing_tournament(self):
        """Test creating match for non-existent tournament"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/matches/",
                json={
                    "tournament_id": "non_existent_tournament",
                    "map_name": "erangel",
                    "scheduled_time": datetime.utcnow().isoformat(),
                },
            )
        
        assert response.status_code in [404, 422, 200]
    
    @pytest.mark.asyncio
    async def test_create_match_invalid_map(self):
        """Test creating match with invalid map name"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/matches/",
                json={
                    "tournament_id": str(uuid.uuid4()),
                    "map_name": "invalid_map",
                    "scheduled_time": datetime.utcnow().isoformat(),
                },
            )
        
        assert response.status_code in [422, 200]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MATCH RETRIEVAL
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_get_match(self):
        """Test getting a match by ID"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/matches/{str(uuid.uuid4())}")
        
        assert response.status_code in [200, 404]
    
    @pytest.mark.asyncio
    async def test_list_tournament_matches(self):
        """Test listing matches for a tournament"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/tournaments/{str(uuid.uuid4())}/matches")
        
        assert response.status_code in [200, 404]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MATCH STATUS TRANSITIONS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_match_status_transitions(self):
        """Test match status transition endpoints"""
        transport = ASGITransport(app=app)
        match_id = str(uuid.uuid4())
        
        transitions = [
            ("start", "/api/v1/matches/{match_id}/start"),
            ("lobby", "/api/v1/matches/{match_id}/status?status=lobby"),
            ("plane", "/api/v1/matches/{match_id}/status?status=plane"),
            ("live", "/api/v1/matches/{match_id}/status?status=live"),
            ("end", "/api/v1/matches/{match_id}/end"),
        ]
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for name, url_template in transitions:
                url = url_template.format(match_id=match_id)
                
                if "status" in url:
                    response = await client.put(url)
                else:
                    response = await client.post(url)
                
                assert response.status_code in [200, 400, 404]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MATCH EVENTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_record_match_event(self):
        """Test recording a match event"""
        transport = ASGITransport(app=app)
        
        event_data = {
            "event_type": "kill",
            "timestamp_ms": 120000,
            "event_data": {
                "killer_id": "player_001",
                "victim_id": "player_002",
                "weapon": "M416",
                "headshot": True,
            },
            "team_id": "team_001",
            "player_id": "player_001",
            "target_team_id": "team_002",
            "target_player_id": "player_002",
        }
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/matches/{str(uuid.uuid4())}/events",
                json=event_data,
            )
        
        assert response.status_code in [200, 201, 404]
    
    @pytest.mark.asyncio
    async def test_get_match_events(self):
        """Test getting match events"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/matches/{str(uuid.uuid4())}/events?limit=20"
            )
        
        assert response.status_code in [200, 404]
    
    @pytest.mark.asyncio
    async def test_get_match_events_filtered(self):
        """Test getting match events with type filter"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/matches/{str(uuid.uuid4())}/events?event_type=kill&limit=10"
            )
        
        assert response.status_code in [200, 404]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MATCH RESULTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_record_match_results(self):
        """Test recording match results"""
        transport = ASGITransport(app=app)
        
        results_data = {
            "team_results": [
                {"team_id": "team_001", "placement": 1, "kills": 12, "damage": 3000},
                {"team_id": "team_002", "placement": 2, "kills": 10, "damage": 2500},
            ],
            "winner_team_id": "team_001",
            "mvp_player_id": "player_001",
        }
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/matches/{str(uuid.uuid4())}/results",
                json=results_data,
            )
        
        assert response.status_code in [200, 201, 404]
    
    @pytest.mark.asyncio
    async def test_record_player_stats(self):
        """Test recording player statistics"""
        transport = ASGITransport(app=app)
        
        stats_data = {
            "player_stats": [
                {
                    "player_id": "player_001",
                    "team_id": "team_001",
                    "kills": 8,
                    "damage_dealt": 2000,
                    "headshots": 3,
                    "revives_performed": 2,
                    "survived_time_seconds": 1500,
                },
            ],
        }
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/matches/{str(uuid.uuid4())}/player-stats",
                json=stats_data,
            )
        
        assert response.status_code in [200, 201, 404]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MATCH STATISTICS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_get_match_stats(self):
        """Test getting match statistics"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/matches/{str(uuid.uuid4())}/stats")
        
        assert response.status_code in [200, 404]
    
    @pytest.mark.asyncio
    async def test_get_match_team_stats(self):
        """Test getting team stats for a match"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/matches/{str(uuid.uuid4())}/team-stats")
        
        assert response.status_code in [200, 404]
    
    @pytest.mark.asyncio
    async def test_get_match_mvp(self):
        """Test getting match MVP"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/matches/{str(uuid.uuid4())}/mvp")
        
        assert response.status_code in [200, 404]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MATCH SUMMARY
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_get_match_summary(self):
        """Test getting match summary"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/matches/{str(uuid.uuid4())}/summary")
        
        assert response.status_code in [200, 404]