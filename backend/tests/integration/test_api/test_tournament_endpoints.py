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
* Reads are DB-backed. The suite gives every test a fresh per-test
  database (Phase 37 W3 conftest isolation: the module-global engine
  is rebound to a ``tmp_path`` file DB with all tables created), so
  reads run against a live, EMPTY DB: list is 200 with
  ``{"data": []}`` and an unknown id is 404. A DB failure is still
  honestly **503 "Database unavailable"** (the service's ``_fetch_*``
  converts any repo exception) — proved explicitly by
  ``test_list_tournaments_dead_db_503``, which serves sessions from
  an engine whose tables were never created, instead of depending
  on the old uninitialised shared ``./test.db`` (which is what the
  three ``*_db_unavailable_503`` tests used to assert before Phase 37).
* ``GET /api/v1/tournaments`` (plural) is a different, DEMO_MODE-gated
  demo card in app.main: **503** when off.
* ``/health`` / ``/health/detailed`` do not exist: **404**. The real
  root document is ``GET /`` -> 200.

BACKLOG (backend divergences, not fixed here): (1) [RESOLVED by the
Phase 37 W3 per-test DB: list-200 and unknown-id-404 are now covered
below, and the 503-on-failure path by ``test_list_tournaments_dead_db_503``.
The old note read: tournament reads 503 on DB failure rather than
distinguishing "no such tournament" (404) — acceptable honest failure,
but 404-for-unknown-id was only observable with a live DB.] (2) there
are no tournament sub-resources for teams/standings/matches under
either spelling (404 below).
"""

import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
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
    # ---------- reads (live per-test DB: empty, tables present) ----------

    async def test_list_tournaments_empty_db_200(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/tournament")
        assert response.status_code == 200
        assert response.json()["data"] == []

    async def test_get_tournament_unknown_id_404(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/tournament/{uuid.uuid4()}")
        assert response.status_code == 404
        assert "Tournament not found" in response.json()["detail"]

    async def test_get_active_tournament_none_404(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/tournament/active")
        assert response.status_code == 404
        assert "No active tournament" in response.json()["detail"]

    async def test_list_tournaments_dead_db_503(self, transport):
        """The 503 contract, proved explicitly: sessions served from
        an engine whose tables were never created fail every repo
        query, and the service converts that to 503 — no dependence
        on the old uninitialised shared ./test.db. The override is
        removed in ``finally`` so no later test sees it."""
        from sqlalchemy.ext.asyncio import (
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )

        from app.db.session import get_db

        dead = create_async_engine("sqlite+aiosqlite:///:memory:")
        maker = async_sessionmaker(dead, class_=AsyncSession, expire_on_commit=False)

        async def _dead_db():
            async with maker() as session:
                yield session

        app.dependency_overrides[get_db] = _dead_db
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/v1/tournament")
            assert response.status_code == 503
            assert "Database unavailable" in response.json()["detail"]
        finally:
            app.dependency_overrides.pop(get_db, None)
            await dead.dispose()

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
