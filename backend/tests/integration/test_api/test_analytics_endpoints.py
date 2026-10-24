# backend/tests/integration/test_api/test_analytics_endpoints.py
"""
Integration tests for the REAL Analytics API surface (H30 rewrite).

History: the previous version accepted **500 as a passing status at 11
sites** and `[200, 404]` interchangeably elsewhere, against URLs that were
never checked against the routers. A 500 can never pass here.

The actual analytics surface in app.main / app.api (read from the code and
verified by probing the app in this environment, DEMO_MODE off):

* ``GET /api/v1/analytics/standings`` — demo-dataset endpoint, DEMO_MODE
  gated: **503** with an honest detail when off (its default).
* ``GET /api/v1/analytics/summary`` and ``GET /api/v1/analytics/kill_feed``
  — same gate: **503**.
* ``POST /api/v1/analytics/zone-efficiency`` — the one real analytics
  router (app.api.v1.analytics_extra); requires authentication: **401**
  unauthenticated.
* Everything the old file probed — leaderboards, team/match points,
  heatmaps, reports, trends, comparisons, dashboard — **does not exist**:
  **404**. Those assertions are kept as explicit 404 contract tests so the
  fiction cannot quietly come back; if someone builds those endpoints,
  these tests go red and must be rewritten against the real contract.

BACKLOG (backend divergence, not fixed here — backend is out of scope):
there is no production analytics read API for standings/leaderboards
outside DEMO_MODE; the frontend's analytics surfaces depend on the gated
demo endpoints (inspection §5).
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


class TestAnalyticsEndpoints:
    """Exact status contracts — 500 is never an acceptable outcome."""

    async def test_standings_demo_gated_503(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/analytics/standings?tournament_id={uuid.uuid4()}"
            )
        assert response.status_code == 503
        assert "DEMO_MODE" in response.json()["detail"]

    async def test_summary_demo_gated_503(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/analytics/summary")
        assert response.status_code == 503
        assert "DEMO_MODE" in response.json()["detail"]

    async def test_kill_feed_demo_gated_503(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/analytics/kill_feed")
        assert response.status_code == 503

    async def test_zone_efficiency_requires_auth(self, transport):
        # The one real analytics router; unauthenticated writes are 401.
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/analytics/zone-efficiency", json={"teams": {}}
            )
        assert response.status_code == 401

    @pytest.mark.parametrize(
        "method,url,body",
        [
            ("GET", "/api/v1/analytics/leaderboard/overall", None),
            ("GET", "/api/v1/analytics/leaderboard/kills", None),
            ("GET", "/api/v1/analytics/leaderboard/damage", None),
            ("GET", "/api/v1/analytics/leaderboard/display?limit=5", None),
            ("GET", f"/api/v1/analytics/teams/{uuid.uuid4()}/points", None),
            ("GET", f"/api/v1/analytics/matches/{uuid.uuid4()}/points", None),
            ("GET", f"/api/v1/analytics/matches/{uuid.uuid4()}/overview", None),
            ("POST", "/api/v1/analytics/heatmaps", {"heatmap_type": "landing", "map_name": "erangel"}),
            ("GET", f"/api/v1/analytics/heatmaps/{uuid.uuid4()}", None),
            ("GET", f"/api/v1/analytics/heatmaps/{uuid.uuid4()}/hotspots", None),
            ("POST", "/api/v1/analytics/reports/match", {"match_id": str(uuid.uuid4())}),
            ("GET", f"/api/v1/analytics/reports/match/{uuid.uuid4()}", None),
            ("POST", "/api/v1/analytics/reports/player", {"player_id": str(uuid.uuid4())}),
            ("GET", f"/api/v1/analytics/trends/{uuid.uuid4()}?metric=kills&window=5", None),
            ("GET", "/api/v1/analytics/trends/hot-players?limit=5", None),
            ("POST", "/api/v1/analytics/compare/teams", {"team_a": {}, "team_b": {}}),
            ("POST", "/api/v1/analytics/compare/players", {"player_a": {}, "player_b": {}}),
            ("GET", f"/api/v1/analytics/dashboard?tournament_id={uuid.uuid4()}", None),
        ],
    )
    async def test_invented_analytics_routes_are_404(self, transport, method, url, body):
        """Routes the old suite probed do not exist — exact 404, not [200, 500]."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            if body is None:
                response = await client.request(method, url)
            else:
                response = await client.request(method, url, json=body)
        assert response.status_code == 404, f"{method} {url}"
        assert response.status_code != 500
