# backend/tests/integration/test_api/test_match_endpoints.py
"""
Integration tests for the REAL Match API surface (H30 rewrite).

History: the previous version accepted interchangeable codes everywhere
(``[200, 404]``, ``[200, 201, 404]``, even ``[422, 200]`` for an invalid
map) and if-gated its only body assertions on create-success, so a broken
create asserted nothing at all. Exact codes only here; 500 never passes.

The actual match surface (read from app.main and verified by probing):
there is NO matches CRUD router. ``MatchService`` exists as a service
layer (covered by tests/unit/.../test_services/test_match_service.py) but
is not exposed as REST create/read/update endpoints. What HTTP exposes:

* ``GET /api/v1/matches`` and ``GET /api/v1/matches/live`` — demo cards,
  DEMO_MODE gated: **503** when off (the default).
* ``GET /api/v1/matches/{match_id}/player-stats`` — the one real read:
  **200** with ``{"data": {"match_id": ..., "players": [...]}}``; an
  unknown match honestly returns an empty players list (never a 500).
* ``POST /api/v1/matches`` — path exists for GET only: **405**.
* Every other old URL (create/get/events/results/stats/mvp/summary,
  status transitions) — **404**, kept below as explicit contract tests.

BACKLOG (backend divergence, not fixed here): matches have no REST
lifecycle API at all, so API-level match management is impossible today;
the old suite disguised that with interchangeable status codes.
"""

import sys
import types
import uuid
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


class TestMatchEndpoints:
    async def test_list_matches_demo_gated_503(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/matches")
        assert response.status_code == 503
        assert "DEMO_MODE" in response.json()["detail"]

    async def test_live_match_demo_gated_503(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/matches/live")
        assert response.status_code == 503

    async def test_create_match_is_405(self, transport):
        # No POST route exists for matches; the path itself is GET-only.
        # Unconditional assertion — the old if-gated body asserted nothing
        # whenever create did not return 200/201 (i.e. always).
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/matches",
                json={
                    "tournament_id": str(uuid.uuid4()),
                    "map_name": "erangel",
                    "scheduled_time": "2030-01-01T00:00:00",
                },
            )
        assert response.status_code == 405

    async def test_get_match_player_stats_empty_200(self, transport):
        match_id = str(uuid.uuid4())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/matches/{match_id}/player-stats")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["match_id"] == match_id
        assert data["players"] == []  # nothing recorded -> honest empty

    @pytest.mark.parametrize(
        "method,url",
        [
            ("GET", f"/api/v1/matches/{uuid.uuid4()}"),
            ("POST", f"/api/v1/matches/{uuid.uuid4()}/start"),
            ("POST", f"/api/v1/matches/{uuid.uuid4()}/end"),
            ("PUT", f"/api/v1/matches/{uuid.uuid4()}/status?status=live"),
            ("POST", f"/api/v1/matches/{uuid.uuid4()}/events"),
            ("GET", f"/api/v1/matches/{uuid.uuid4()}/events"),
            ("POST", f"/api/v1/matches/{uuid.uuid4()}/results"),
            ("GET", f"/api/v1/matches/{uuid.uuid4()}/stats"),
            ("GET", f"/api/v1/matches/{uuid.uuid4()}/team-stats"),
            ("GET", f"/api/v1/matches/{uuid.uuid4()}/mvp"),
            ("GET", f"/api/v1/matches/{uuid.uuid4()}/summary"),
            ("GET", f"/api/v1/tournaments/{uuid.uuid4()}/matches"),
        ],
    )
    async def test_invented_match_routes_are_404(self, transport, method, url):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.request(method, url, json={} if method in ("POST", "PUT") else None)
        assert response.status_code == 404, f"{method} {url}"
