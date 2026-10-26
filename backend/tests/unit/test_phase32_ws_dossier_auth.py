"""Phase 32 — Worker 2 (WS kill-switch hole K-H1 + dossier auth K-H2).

Covers, against docs/FULL_PROJECT_INSPECTION.md (2026-10-24 re-inspection):
  * K-H1: ``authenticate_websocket`` applies the REST kill-switch check
    (DB user's live ``is_active`` flag); a token frozen at WS connect
    time does NOT keep working after deactivation — ``websocket_live``
    re-validates on every action frame, so the next action after the
    kill switch gets an "unauthorized" ack (fail closed). Active users
    keep working and role checks stay intact (viewer / anonymous still
    rejected).
  * K-H2: dossier GETs (fights, dossier, both PDFs) require the same
    operator-or-admin gate as POST ingest — 401 unauthenticated,
    403 for an authenticated viewer, 200 for an operator; a deactivated
    operator's token is 401 here too (kill switch covers dossiers).
  * REST kill-switch behaviour itself is unchanged (see
    test_integrity_credentials_auth.py, re-run alongside this file).

DB tests use their own FILE SQLite engine (not :memory:) so the
TestClient portal thread shares the same database as the test thread.
Dossier service dependencies are overridden with fakes that return
valid schema objects — what is under test is the AUTH WIRING, not the
dossier computation or PDF rendering (fpdf2 is not installed in this
environment, so the router's PDF functions are stubbed to return
bytes; the 401/403 gates run before any handler body either way).
"""

from __future__ import annotations

import asyncio
import sys
import types
import uuid
from enum import Enum

import pytest
from sqlalchemy import JSON
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# --- fpdf stub (same pattern as test_live_events.py) -------------------
# app.main pulls in the dossiers router, which imports fpdf2. fpdf2 is not
# installed in this test environment and PDF generation is not exercised
# here, so a minimal stub is installed before importing app.main. This
# stubs an optional dependency, not product behaviour.
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

from fastapi.testclient import TestClient  # noqa: E402

from app.api import deps  # noqa: E402
from app.api.deps import authenticate_websocket  # noqa: E402
from app.api.v1.users.service import UserService  # noqa: E402
from app.core.security import security_manager  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.models.team import Team  # noqa: E402
from app.db.models.tournament import Tournament  # noqa: E402
import importlib  # noqa: E402

# NOTE: app.dossiers.__init__ shadows the submodule attribute
# (``from .router import router`` binds the APIRouter to the package
# attribute), so fetch the real module from sys.modules instead.
dossiers_router = importlib.import_module("app.dossiers.router")  # noqa: E402
from app.dossiers.schemas import DossierOut, MatchReportData  # noqa: E402
from app.main import app  # noqa: E402

pytestmark = pytest.mark.unit


def _run(coro):
    """Run one coroutine from a sync test (no loop is running here)."""
    return asyncio.run(coro)


def _sqlite_friendly_metadata():
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, postgresql.JSONB):
                column.type = JSON()


def _fake_dossier():
    return DossierOut(
        team_id="t1",
        team_name="Fake Team",
        computed_at="2026-10-24T00:00:00+00:00",
        data_freshness="insufficient_data",
        playstyle={
            "label": "insufficient_data",
            "evidence": "no fights sampled",
        },
        tendencies={},
    )


class _FakeFightService:
    async def list_fights(self, **kwargs):
        return [], 0

    async def ingest(self, payload):
        raise AssertionError("ingest must not run in an auth test")


class _FakeDossierService:
    async def get_dossier(self, team_id, force_recompute=False):
        return _fake_dossier()


class _FakeReportService:
    async def get_match_report_data(self, match_id):
        return MatchReportData(match_id=match_id)


@pytest.fixture()
def ctx(tmp_path):
    """File-SQLite engine + users + tokens + TestClient, torn down after."""
    db_path = tmp_path / "p32.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    _sqlite_friendly_metadata()

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with maker() as session:
            svc = UserService(session)
            cup = Tournament(name="P32 Cup", slug=f"p32-{uuid.uuid4().hex[:8]}")
            session.add(cup)
            team = Team(name="P32 Team", is_active=True)
            session.add(team)
            await session.flush()
            resp_op, _ = await svc.create_user(
                username="p32_op", password="x", role="operator",
                tournament_id=cup.id, team_id=team.id,
            )
            resp_view, _ = await svc.create_user(
                username="p32_view", password="x", role="viewer",
                tournament_id=cup.id, team_id=team.id,
            )
            await session.commit()
            return {
                "cup_id": cup.id,
                "op_id": resp_op.id,
                "view_id": resp_view.id,
                "op_token": security_manager.create_access_token(
                    user_id=resp_op.id, username="p32_op", role="operator",
                    team_id=team.id, tournament_id=cup.id,
                ),
                "view_token": security_manager.create_access_token(
                    user_id=resp_view.id, username="p32_view", role="viewer",
                    team_id=team.id, tournament_id=cup.id,
                ),
            }

    data = _run(_setup())
    original_factory = deps.AsyncSessionLocal
    deps.AsyncSessionLocal = maker
    app.dependency_overrides[dossiers_router.get_fight_service] = lambda: _FakeFightService()
    app.dependency_overrides[dossiers_router.get_dossier_service] = lambda: _FakeDossierService()
    app.dependency_overrides[dossiers_router.get_report_service] = lambda: _FakeReportService()
    orig_match_pdf = dossiers_router.match_report_pdf
    orig_team_pdf = dossiers_router.team_dossier_pdf
    dossiers_router.match_report_pdf = lambda data: b"%PDF-fake-match"
    dossiers_router.team_dossier_pdf = lambda name, data: b"%PDF-fake-team"
    client = TestClient(app)
    try:
        yield {"client": client, "maker": maker, **data}
    finally:
        app.dependency_overrides.clear()
        dossiers_router.match_report_pdf = orig_match_pdf
        dossiers_router.team_dossier_pdf = orig_team_pdf
        deps.AsyncSessionLocal = original_factory


async def _deactivate(maker, cup_id):
    async with maker() as session:
        svc = UserService(session)
        count = await svc.deactivate_tournament(cup_id)
        await session.commit()
        return count


def _ws_action_ack(ws, action, cap=60):
    """Send one action frame; read frames until its action_ack arrives."""
    ws.send_json({"action": action})
    for _ in range(cap):
        frame = ws.receive_json()
        if frame.get("type") == "action_ack":
            return frame.get("data", {})
    raise AssertionError(f"no action_ack within {cap} frames for {action!r}")


# ── K-H1: authenticate_websocket kill-switch ───────────────────────────


def test_ws_auth_active_deactivated_ghost_operator_apikey(ctx, monkeypatch):
    op_token = ctx["op_token"]

    # Active operator token authenticates.
    principal = _run(authenticate_websocket(None, token=op_token))
    assert principal is not None and principal.username == "p32_op"

    # Ghost token (no DB row) is rejected, not just unknown-role.
    ghost = security_manager.create_access_token(
        user_id=str(uuid.uuid4()), username="ghost", role="operator",
    )
    assert _run(authenticate_websocket(None, token=ghost)) is None

    # Configured operator principal (no DB row by design) still passes.
    op_principal = security_manager.create_access_token(
        user_id="operator", username="operator", is_superuser=True,
        scopes=["admin"], role="admin",
    )
    assert _run(authenticate_websocket(None, token=op_principal)) is not None

    # X-API-Key path is untouched by the DB check, exactly as on REST.
    monkeypatch.setattr(deps.settings, "ADMIN_API_KEYS", ["p32-test-api-key"])
    key_principal = _run(authenticate_websocket(None, token="p32-test-api-key"))
    assert key_principal is not None and key_principal.username == "operator"

    # DB lookup failure fails closed (not fail-open).
    async def _boom(user_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(deps, "_db_user_is_active", _boom)
    assert _run(authenticate_websocket(None, token=op_token)) is None

    # Kill switch: the SAME unexpired token is rejected after deactivation.
    assert _run(_deactivate(ctx["maker"], ctx["cup_id"])) == 2
    assert _run(authenticate_websocket(None, token=op_token)) is None


# ── K-H1: live WS control channel, deactivation mid-connection ─────────


def test_ws_live_action_fails_closed_after_deactivation(ctx):
    client = ctx["client"]
    with client.websocket_connect(f"/ws/live?token={ctx['op_token']}") as ws:
        first = ws.receive_json()
        assert first.get("type") == "connected"
        # Active: control action succeeds.
        ack = _ws_action_ack(ws, "start_scrim")
        assert ack.get("status") == "success", f"active action ack: {ack}"
        # Kill switch mid-connection, then the NEXT action fails closed
        # even though the socket never reconnected.
        assert _run(_deactivate(ctx["maker"], ctx["cup_id"])) == 2
        ack = _ws_action_ack(ws, "start_scrim")
        assert ack.get("status") == "unauthorized", f"deactivated ack: {ack}"


def test_ws_live_role_checks_intact(ctx):
    client = ctx["client"]
    # Viewer token: valid auth, insufficient role → unauthorized.
    with client.websocket_connect(f"/ws/live?token={ctx['view_token']}") as ws:
        assert ws.receive_json().get("type") == "connected"
        ack = _ws_action_ack(ws, "start_scrim")
        assert ack.get("status") == "unauthorized"
    # No token at all → unauthorized (passive subscription itself stays open).
    with client.websocket_connect("/ws/live") as ws:
        assert ws.receive_json().get("type") == "connected"
        ack = _ws_action_ack(ws, "start_scrim")
        assert ack.get("status") == "unauthorized"


# ── K-H2: dossier GETs require operator-or-admin ────────────────────────


def test_dossier_gets_unauth_401_viewer_403_operator_200(ctx):
    client = ctx["client"]
    op = {"Authorization": f"Bearer {ctx['op_token']}"}
    view = {"Authorization": f"Bearer {ctx['view_token']}"}
    urls = [
        "/api/v1/dossiers/fights",
        "/api/v1/dossiers/dossier?team_id=t1",
        "/api/v1/dossiers/report/match/m1.pdf",
        "/api/v1/dossiers/report/team/t1.pdf",
    ]
    for url in urls:
        assert client.get(url).status_code == 401, url
        assert client.get(url, headers=view).status_code == 403, url
        resp = client.get(url, headers=op)
        assert resp.status_code == 200, (url, resp.status_code, resp.text[:200])
    pdf = client.get("/api/v1/dossiers/report/team/t1.pdf", headers=op)
    assert "application/pdf" in pdf.headers.get("content-type", "")


def test_dossier_get_deactivated_operator_401(ctx):
    client = ctx["client"]
    op = {"Authorization": f"Bearer {ctx['op_token']}"}
    assert client.get("/api/v1/dossiers/fights", headers=op).status_code == 200
    assert _run(_deactivate(ctx["maker"], ctx["cup_id"])) == 2
    assert client.get("/api/v1/dossiers/fights", headers=op).status_code == 401
