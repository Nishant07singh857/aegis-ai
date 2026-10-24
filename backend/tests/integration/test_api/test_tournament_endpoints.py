# backend/tests/integration/test_api/test_tournament_endpoints.py
"""
Integration tests for the REAL Tournament API surface (H30 rewrite).

History: the previous version probed ``/api/v1/tournaments/`` (plural —
never a route), accepted ``[200, 201, 422]`` for creates, if-gated every
downstream step on create-success, accepted 200 for a non-existent
tournament ("200 if mock, 404 if real"), and asserted ``/health`` returns
200 + ``{"status": "healthy"}`` — an endpoint that does not exist.

The actual surface (app.api.v1.tournament.router, mounted at /api/v1;
read from the code and verified by probing this environment):

* Singular resource: ``/api/v1/tournament`` — GET list / POST create
  (201), GET ``/tournament/{id}``, GET ``/tournament/active``, PUT/PATCH/
  DELETE ``/tournament/{id}``, POST ``/tournament/{id}/activate``.
* Every write requires admin auth — unauthenticated: **401**, and auth
  runs before body validation, so even a malformed create is 401, never
  the 422 the old suite imagined.
* Reads are DB-backed. This test environment has no database connected
  (the configured DATABASE_URL points at a Postgres that is not running),
  and the service honestly surfaces that as **503 "Database unavailable"**
  instead of inventing rows. With a live DB, list would be 200 and an
  unknown id 404 — see BACKLOG.
* ``GET /api/v1/tournaments`` (plural) is a different, DEMO_MODE-gated
  demo card in app.main: **503** when off.
* ``/health`` / ``/health/detailed`` do not exist: **404**. The real
  root document is ``GET /`` -> 200.

BACKLOG (backend divergences, not fixed here): (1) tournament reads
503 on DB failure rather than distinguishing "no such tournament" (404)
— acceptable honest failure, but it means 404-for-unknown-id is only
observable with a live DB; (2) there are no tournament sub-resources for
teams/standings/matches under either spelling (404 below).
"""

import sys
import types
import uuid
from datetime import datetime, timedelta
from enum import Enum

import pytest
from httpx import ASGITransport, AsyncClient

# --- fpdf stub: see test_live_events.py / test_main_reliability.py --------
_fpdf_stub = types.ModuleType("fpdf")
_fpdf_stub.__path__ = []


class _FPDF:
    def __init__(self, *args, **kwargs):
        pass


class _MethodReturnValue(Enum):
    NONE = 0


class _XPos(Enum):
    LEFT = 0
    RIGHT = 1
    LMARGIN = 2


class _YPos(Enum):
    TOP = 0
    NEXT = 1


_fpdf_stub.FPDF = _FPDF
_enums_stub = types.ModuleType("fpdf.enums")
_enums_stub.MethodReturnValue = _MethodReturnValue
_enums_stub.XPos = _XPos
_enums_stub.YPos = _YPos
sys.modules.setdefault("fpdf", _fpdf_stub)
sys.modules.setdefault("fpdf.enums", _enums_stub)

from app.main import app  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.api]


@pytest.fixture()
def transport():
    return ASGITransport(app=app)


@pytest.fixture()
def tournament_data():
    return {
        "name": "Integration Test Tournament",
        "description": "Tournament created during integration testing",
        "start_date": (datetime.utcnow() + timedelta(days=7)).isoformat(),
        "end_date": (datetime.utcnow() + timedelta(days=10)).isoformat(),
        "organizer": "Test Org",
        "points_per_kill": 1,
        "prize_pool": "₹5,00,000",
        "is_public": True,
    }


class TestTournamentEndpoints:
    # ---------- reads (DB honestly unavailable in this env) ----------

    async def test_list_tournaments_db_unavailable_503(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/tournament")
        assert response.status_code == 503
        assert "Database unavailable" in response.json()["detail"]

    async def test_get_tournament_db_unavailable_503(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/tournament/{uuid.uuid4()}")
        assert response.status_code == 503
        assert "Database unavailable" in response.json()["detail"]

    async def test_get_active_tournament_db_unavailable_503(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/tournament/active")
        assert response.status_code == 503

    async def test_plural_tournaments_is_demo_gated_503(self, transport):
        # The plural path the old suite used is the DEMO_MODE demo card.
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/tournaments")
        assert response.status_code == 503
        assert "DEMO_MODE" in response.json()["detail"]

    # ---------- writes (admin auth is the first, exact contract) ----------

    async def test_create_tournament_requires_admin_401(self, transport, tournament_data):
        # Unconditional: the old test accepted [200, 201, 422] and only
        # asserted a body if create had succeeded (it never could here).
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/v1/tournament", json=tournament_data)
        assert response.status_code == 401
        assert "detail" in response.json()

    async def test_create_tournament_missing_fields_still_401(self, transport):
        # Auth precedes validation: a malformed body is 401, not 422.
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/tournament", json={"name": "Incomplete Tournament"}
            )
        assert response.status_code == 401

    async def test_update_tournament_requires_admin_401(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.put(
                f"/api/v1/tournament/{uuid.uuid4()}",
                json={"name": "Updated", "slug": "updated"},
            )
        assert response.status_code == 401

    async def test_delete_tournament_requires_admin_401(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(f"/api/v1/tournament/{uuid.uuid4()}")
        assert response.status_code == 401

    async def test_activate_tournament_requires_admin_401(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(f"/api/v1/tournament/{uuid.uuid4()}/activate")
        assert response.status_code == 401

    # ---------- routes that do not exist ----------

    @pytest.mark.parametrize(
        "method,url",
        [
            ("POST", f"/api/v1/tournament/{uuid.uuid4()}/teams"),
            ("GET", f"/api/v1/tournament/{uuid.uuid4()}/teams"),
            ("GET", f"/api/v1/tournament/{uuid.uuid4()}/standings"),
            ("POST", f"/api/v1/tournament/{uuid.uuid4()}/start"),
            ("POST", f"/api/v1/tournament/{uuid.uuid4()}/stop"),
        ],
    )
    async def test_invented_tournament_subroutes_are_404(self, transport, method, url):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.request(method, url, json={} if method == "POST" else None)
        assert response.status_code == 404, f"{method} {url}"

    # ---------- health fiction, replaced with the real root ----------

    async def test_health_endpoint_does_not_exist(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
            detailed = await client.get("/health/detailed")
        assert response.status_code == 404
        assert detailed.status_code == 404

    async def test_root_document_200(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/")
        assert response.status_code == 200
        assert response.json()["status_endpoint"] == "/api/v1/system/status"
