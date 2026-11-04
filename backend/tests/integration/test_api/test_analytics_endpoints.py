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
import uuid
from contextlib import contextmanager
from types import ModuleType

import pytest
from httpx import ASGITransport, AsyncClient

# --- fpdf stub: Phase 33 context-manager treatment (LOCAL COPY) -----------
# app.main pulls in the dossiers router, which imports fpdf2. fpdf2 is
# not installed in this test environment and PDF generation is not
# exercised here. The stub below is the Phase 33 pattern, copied here
# because this file lives in tests/integration/ and cannot import the
# helper from tests/unit/test_phase33_db_boot_fail_fast.py (pytest
# only puts each test file's own directory on sys.path). The stub is
# installed ONLY inside ``_fpdf_stub_if_absent`` and torn down on
# exit — sys.modules is restored exactly and stub-bound app.main /
# app.dossiers.* modules are evicted — so this file installs NO
# import-time stub of its own (the old sys.modules.setdefault stub
# was the import-time leak behind the dossier-PDF order-dependence).
# The import happens ONCE at module level because the whole file
# exercises app.main; the skip is ImportError-only — any other import
# failure propagates and fails loudly.

_MISSING = object()  # sentinel: sys.modules entry was absent before the stub


def _build_fpdf_stub():
    """Build the minimal fpdf package stub (import stand-in only).

    ``app.dossiers.pdf`` imports ``FPDF`` and ``fpdf.enums``
    (``MethodReturnValue``, ``XPos``, ``YPos``) at module level, so
    the stub must be a package with an ``enums`` submodule. It is
    only a stand-in for import: no test here generates a PDF, and
    the stub's ``FPDF`` has no PDF methods — PDF generation against
    it fails, honestly, instead of pretending to work.
    """
    import enum

    stub = ModuleType("fpdf")
    stub.__path__ = []  # mark as a package so fpdf.enums can load

    class _FPDF:  # minimal stand-in; no test here touches PDF code
        def __init__(self, *args, **kwargs):
            pass

    stub.FPDF = _FPDF
    enums = ModuleType("fpdf.enums")

    class XPos(enum.Enum):
        LEFT = "LEFT"
        CENTER = "CENTER"
        RIGHT = "RIGHT"
        LMARGIN = "LMARGIN"
        RMARGIN = "RMARGIN"

    class YPos(enum.Enum):
        TOP = "TOP"
        NEXT = "NEXT"
        LAST = "LAST"
        TMARGIN = "TMARGIN"
        BMARGIN = "BMARGIN"

    class MethodReturnValue(enum.Enum):
        VALUE = "VALUE"

    enums.XPos = XPos
    enums.YPos = YPos
    enums.MethodReturnValue = MethodReturnValue
    return stub, enums


@contextmanager
def _fpdf_stub_if_absent():
    """Install the fpdf stub only if fpdf2 is really absent, with teardown.

    On exit, ``sys.modules["fpdf"]`` / ``sys.modules["fpdf.enums"]``
    are restored to exactly their pre-context state (the previous
    module object, or absent), and every ``app.main`` /
    ``app.dossiers.*`` module first imported inside the context is
    evicted from ``sys.modules``. Eviction matters: without it, a
    later ``import app.main`` in the same process would return the
    module bound to the stub's ``FPDF`` even after the stub itself
    was removed. Modules already present before the context are
    never evicted — this context only undoes what it caused.
    """
    try:
        import fpdf  # noqa: F401

        yield
        return
    except ImportError:
        pass
    prior_fpdf = sys.modules.get("fpdf", _MISSING)
    prior_enums = sys.modules.get("fpdf.enums", _MISSING)
    modules_before = set(sys.modules)
    stub, enums = _build_fpdf_stub()
    sys.modules["fpdf"] = stub
    sys.modules["fpdf.enums"] = enums
    try:
        yield
    finally:
        for name, prior in (("fpdf", prior_fpdf), ("fpdf.enums", prior_enums)):
            if prior is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior
        for name in list(sys.modules):
            if name not in modules_before and (
                name == "app.main" or name.startswith("app.dossiers")
            ):
                sys.modules.pop(name, None)


def _load_main_module():
    """Import app.main under the teardown-guarded fpdf stub.

    Skip ONLY on a missing-module import failure. Every other import
    failure propagates and FAILS loudly: a real setup/configuration
    error must never be reported as a skip.
    """
    with _fpdf_stub_if_absent():
        try:
            import app.main as main_module
        except ImportError as exc:
            pytest.skip(f"app.main not importable here (missing dependency): {exc}")
        return main_module


main_module = _load_main_module()
app = main_module.app

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
