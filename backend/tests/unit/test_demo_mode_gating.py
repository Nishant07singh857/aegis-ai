# backend/tests/unit/test_demo_mode_gating.py
"""Phase 13 (W3 dynamic binding): demo-mode gating + rename propagation.

- With DEMO_MODE off (the default), every inline endpoint in app.main that
  serves the hardcoded demo dataset must answer 503 with an honest detail —
  no demo team/player name may leak into any live render or scoring path.
- With DEMO_MODE on, the same endpoints serve the demo dataset (simulator).
- The WS `standings` connect frame carries demo squads only in DEMO_MODE.
- Team renames propagate through the teams read cache: after the cache is
  invalidated (what every write does), list_teams reflects the new name —
  i.e. no template can hold a stale hardcoded name past the cache TTL.

No network, no DB writes, no secrets.

Import note: app.main pulls in the dossiers router, which imports fpdf2.
fpdf2 is not installed in this test environment (and PDF generation is not
exercised here), so a minimal fpdf stub is installed in sys.modules before
the import. This stubs an optional dependency, not product behavior.
"""

from __future__ import annotations

import sys
import types
from enum import Enum
from unittest.mock import patch

import pytest

# --- fpdf stub (see module docstring) --------------------------------------
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

import app.main as main_module  # noqa: E402
from app.api.v1.teams.demo_data import DEMO_TEAMS  # noqa: E402

DEMO_NAMES = [t["name"] for t in DEMO_TEAMS] + [
    p for t in DEMO_TEAMS for p in t["players"]
]
# Also the hardcoded demo match/tournament cards.
DEMO_NAMES += ["BGIS Semifinals - Match 4", "BGIS 2024"]

GATED_ENDPOINTS = [
    # NOTE: the legacy GET /api/v1/players was REMOVED (not gated) — W1's
    # DB-backed players router now owns that path. Gating it would have
    # shadowed the real endpoint (module-level @app.get registers first).
    "get_mvp",
    "get_matches",
    "get_live_match",
    "get_tournaments",
    "get_standings",
    "get_kill_feed",
    "get_analytics_summary",
]


def _body_text(resp) -> str:
    return resp.body.decode("utf-8", errors="replace")


@pytest.mark.unit
class TestDemoGating:
    @pytest.mark.parametrize("func_name", GATED_ENDPOINTS)
    async def test_demo_endpoints_answer_503_with_no_name_leak(self, func_name):
        """Default (DEMO_MODE off): honest 503, zero hardcoded names."""
        with patch.object(main_module, "DEMO_MODE", False):
            resp = await getattr(main_module, func_name)()
        assert resp.status_code == 503, func_name
        text = _body_text(resp)
        for name in DEMO_NAMES:
            assert name not in text, f"demo name {name!r} leaked from {func_name}"
        assert "DEMO_MODE" in text  # the detail points at the fix

    async def test_demo_endpoints_serve_demo_when_enabled(self):
        """DEMO_MODE=1: the simulator dataset is served (explicit opt-in)."""
        with patch.object(main_module, "DEMO_MODE", True):
            standings = await main_module.get_standings()
            summary = await main_module.get_analytics_summary()
        assert standings.status_code == 200
        assert "SouL" in _body_text(standings)
        assert summary.status_code == 200

    async def test_live_teams_empty_by_default(self):
        with patch.object(main_module, "DEMO_MODE", False):
            assert main_module._live_teams() == []

    async def test_live_teams_demo_when_enabled(self):
        with patch.object(main_module, "DEMO_MODE", True):
            teams = main_module._live_teams()
        assert [t["name"] for t in teams] == [t["name"] for t in DEMO_TEAMS]

    async def test_ws_connect_standings_frame_has_no_demo_names(self):
        """The live WS connect frame must not push demo squads by default."""

        class _FakeWebSocket:
            def __init__(self):
                self.frames = []

            async def accept(self):
                pass

            async def send_json(self, payload):
                self.frames.append(payload)

        ws = _FakeWebSocket()
        manager = main_module.ConnectionManager()
        with patch.object(main_module, "DEMO_MODE", False):
            await manager.connect(ws)
        standings_frames = [f for f in ws.frames if f.get("type") == "standings"]
        assert standings_frames, "expected a standings frame on connect"
        for frame in standings_frames:
            assert frame["data"] == [], "demo squads leaked into WS standings frame"


@pytest.mark.unit
class TestRenamePropagation:
    async def test_team_rename_reflects_after_cache_invalidation(self):
        """Rename in the (stubbed) store -> visible on next read past the
        cache TTL/invalidation. No read path holds a hardcoded name."""
        from app.api.v1.teams import service as teams_service
        from app.api.v1.teams.schemas import TeamResponse
        from app.api.v1.teams.service import TeamService

        teams_service._local_cache.clear()
        svc = TeamService(db=None)

        current = {"name": "Old Name"}

        async def fake_fetch(skip: int = 0, limit: int = 100):
            return [TeamResponse(id="t1", name=current["name"], source="db")]

        svc._fetch_list_teams = fake_fetch  # type: ignore[method-assign]

        assert (await svc.list_teams())[0].name == "Old Name"

        # The rename lands (a write bumps the cache generation, exactly as
        # create/update/delete do), and the next read reflects it.
        current["name"] = "New Name"
        teams_service._invalidate_teams_cache()

        assert (await svc.list_teams())[0].name == "New Name"


@pytest.mark.unit
class TestNoRouteShadowing:
    async def test_players_path_served_by_db_router(self):
        """GET /api/v1/players must be served by W1's DB-backed players
        router, never by a demo fallback.

        main.py's legacy demo endpoint was removed outright (not merely
        gated): with DEMO_MODE off a gated legacy copy would have answered
        503 here and hidden the real endpoint.
        """
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=main_module.app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.get("/api/v1/players")
        text = resp.text
        assert "DEMO_MODE" not in text, "demo fallback is shadowing the real router"
        assert resp.status_code in (200, 503)
        if resp.status_code == 200:
            assert "data" in resp.json(), "expected the {data} envelope"
