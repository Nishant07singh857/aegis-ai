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
