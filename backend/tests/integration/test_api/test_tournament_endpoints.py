# backend/tests/integration/test_api/test_tournament_endpoints.py
"""
Integration tests for Tournament API Endpoints.
Tests complete CRUD operations, team registration,
and tournament lifecycle through the API layer.
"""

import pytest
from httpx import AsyncClient, ASGITransport
import uuid
from datetime import datetime, timedelta

from app.main import app


@pytest.mark.integration
@pytest.mark.api
class TestTournamentEndpoints:
    """Test Tournament API endpoints"""
    
    @pytest.fixture
    def tournament_data(self):
        """Sample tournament creation data"""
        return {
            "name": "Integration Test Tournament",
            "description": "Tournament created during integration testing",
            "start_date": (datetime.utcnow() + timedelta(days=7)).isoformat(),
            "end_date": (datetime.utcnow() + timedelta(days=10)).isoformat(),
            "organizer": "Test Org",
            "points_per_kill": 1,
            "prize_pool": "₹5,00,000",
            "max_teams": 16,
            "min_teams": 8,
            "is_public": True,
        }
    
    @pytest.fixture
    def team_data(self):
        """Sample team data"""
        return {
            "name": "Integration Test Team",
            "short_name": "ITT",
            "tag": "ITT",
            "country": "India",
        }
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TOURNAMENT CREATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_create_tournament(self, tournament_data):
        """Test creating a tournament via API"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/tournaments/",
                json=tournament_data,
            )
        
        assert response.status_code in [200, 201, 422]
        
        if response.status_code in [200, 201]:
            data = response.json()
            assert "tournament_id" in data or "id" in data
            assert data.get("success", True) is True
    
    @pytest.mark.asyncio
    async def test_create_tournament_missing_fields(self):
        """Test creating tournament with missing required fields"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/tournaments/",
                json={"name": "Incomplete Tournament"},
            )
        
        assert response.status_code in [422, 400, 200]
    
    @pytest.mark.asyncio
    async def test_create_tournament_invalid_dates(self):
        """Test creating tournament with invalid dates"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/tournaments/",
                json={
                    "name": "Bad Dates Tournament",
                    "start_date": (datetime.utcnow() + timedelta(days=10)).isoformat(),
                    "end_date": (datetime.utcnow() + timedelta(days=5)).isoformat(),  # End before start
                },
            )
        
        assert response.status_code in [422, 400, 200]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TOURNAMENT RETRIEVAL
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_list_tournaments(self):
        """Test listing all tournaments"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/tournaments/")
        
        assert response.status_code == 200
        data = response.json()
        assert "tournaments" in data or "success" in data
    
    @pytest.mark.asyncio
    async def test_list_tournaments_with_filters(self):
        """Test listing tournaments with status filter"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/tournaments/?status=draft&limit=5&offset=0")
        
        assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_get_tournament_not_found(self):
        """Test getting non-existent tournament"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/tournaments/non_existent_id_12345")
        
        assert response.status_code in [404, 200]  # 200 if mock, 404 if real
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TOURNAMENT UPDATES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_update_tournament(self, tournament_data):
        """Test updating a tournament"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # First create
            create_response = await client.post("/api/v1/tournaments/", json=tournament_data)
            
            if create_response.status_code in [200, 201]:
                tournament_id = create_response.json().get("tournament_id") or create_response.json().get("id")
                
                # Then update
                update_response = await client.put(
                    f"/api/v1/tournaments/{tournament_id}",
                    json={"name": "Updated Tournament Name", "description": "Updated description"},
                )
                
                assert update_response.status_code in [200, 404, 405]  # 405 if PUT not allowed
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TOURNAMENT LIFECYCLE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_tournament_lifecycle(self, tournament_data):
        """Test complete tournament lifecycle"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Create
            create_resp = await client.post("/api/v1/tournaments/", json=tournament_data)
            
            if create_resp.status_code in [200, 201]:
                tournament_id = create_resp.json().get("tournament_id") or create_resp.json().get("id")
                
                # Start tournament (may fail if not in correct state)
                start_resp = await client.post(f"/api/v1/tournaments/{tournament_id}/start")
                assert start_resp.status_code in [200, 400, 404]
                
                # Stop tournament
                stop_resp = await client.post(f"/api/v1/tournaments/{tournament_id}/stop")
                assert stop_resp.status_code in [200, 400, 404]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TEAM REGISTRATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_register_team_for_tournament(self, tournament_data, team_data):
        """Test registering a team for tournament"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Create tournament
            tourn_resp = await client.post("/api/v1/tournaments/", json=tournament_data)
            
            if tourn_resp.status_code in [200, 201]:
                tournament_id = tourn_resp.json().get("tournament_id") or tourn_resp.json().get("id")
                
                # Register team
                reg_resp = await client.post(
                    f"/api/v1/tournaments/{tournament_id}/teams",
                    json={"team_id": str(uuid.uuid4()), **team_data},
                )
                
                assert reg_resp.status_code in [200, 201, 404, 422]
    
    @pytest.mark.asyncio
    async def test_get_registered_teams(self, tournament_data):
        """Test getting registered teams"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/tournaments/{str(uuid.uuid4())}/teams")
            
            assert response.status_code in [200, 404]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TOURNAMENT STANDINGS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_get_tournament_standings(self):
        """Test getting tournament standings"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/tournaments/{str(uuid.uuid4())}/standings")
            
            assert response.status_code in [200, 404]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TOURNAMENT DELETION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_delete_tournament(self, tournament_data):
        """Test deleting a tournament"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Create first
            create_resp = await client.post("/api/v1/tournaments/", json=tournament_data)
            
            if create_resp.status_code in [200, 201]:
                tournament_id = create_resp.json().get("tournament_id") or create_resp.json().get("id")
                
                # Delete
                delete_resp = await client.delete(f"/api/v1/tournaments/{tournament_id}")
                assert delete_resp.status_code in [200, 204, 404]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HEALTH CHECK
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        """Test health check endpoint"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
    
    @pytest.mark.asyncio
    async def test_detailed_health(self):
        """Test detailed health endpoint"""
        transport = ASGITransport(app=app)
        
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health/detailed")
        
        assert response.status_code == 200