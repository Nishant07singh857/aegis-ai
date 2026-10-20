# backend/tests/unit/test_mvp_unification.py
"""Phase 21 (W2) — MVP unification + damage honesty.

Coverage:
- The canonical PRD formula in ``mvp_calculator`` is the single
  producer: kills*5 + damage*0.05 + revives*3 + survival*0.02
  + clutches*10 + headshots*2 (exact, known-input checks).
- Alias handling + missing-stat honesty: absent stats contribute 0;
  ``clutch_attempts`` alone never counts as a clutch; damage ``None``
  contributes 0 but is preserved as ``None`` (never estimated).
- ``MatchService.record_player_stats`` computes + persists the
  canonical score (caller-supplied ``mvp_score`` is ignored), keeps
  unknown damage NULL, and flags the top scorer as MVP.
- Demo-gated endpoints in ``app.main`` no longer serve kills-derived
  damage or a competing MVP formula: demo MVP uses the canonical
  score with ``damage: null`` + a demo label; the demo analytics
  summary reports ``total_damage: null``.

No network, no real DB, no secrets. Importing ``app.main`` needs the
same fpdf stub as ``test_demo_mode_gating`` (optional PDF dependency).
"""

from __future__ import annotations

import sys
import types
from enum import Enum
from unittest.mock import AsyncMock, Mock, patch

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

from app.agents.analytics_agents.mvp_calculator import (  # noqa: E402
    compute_mvp_score,
    compute_mvp_score_from_stats,
    extract_clutches,
)


@pytest.mark.unit
class TestCanonicalFormula:
    def test_known_inputs_exact(self):
        # 8*5 + 2000*0.05 + 2*3 + 1500*0.02 + 1*10 + 3*2
        # = 40 + 100 + 6 + 30 + 10 + 6 = 192
        assert (
            compute_mvp_score(
                kills=8,
                damage=2000,
                revives=2,
                survival_seconds=1500,
                clutches=1,
                headshots=3,
            )
            == pytest.approx(192.0)
        )

    def test_zero_inputs(self):
        assert compute_mvp_score() == 0.0

    def test_missing_damage_contributes_zero(self):
        assert compute_mvp_score(kills=4, damage=None) == pytest.approx(20.0)

    def test_each_term_independently(self):
        assert compute_mvp_score(kills=1) == pytest.approx(5.0)
        assert compute_mvp_score(damage=100) == pytest.approx(5.0)
        assert compute_mvp_score(revives=1) == pytest.approx(3.0)
        assert compute_mvp_score(survival_seconds=100) == pytest.approx(2.0)
        assert compute_mvp_score(clutches=1) == pytest.approx(10.0)
        assert compute_mvp_score(headshots=1) == pytest.approx(2.0)

    def test_from_stats_aliases(self):
        stats = {
            "finishes": 8,
            "damage_dealt": 2000,
            "revives_performed": 2,
            "survived_time_seconds": 1500,
            "clutch_wins": 1,
            "headshots": 3,
        }
        assert compute_mvp_score_from_stats(stats) == pytest.approx(192.0)

    def test_clutches_extraction(self):
        assert extract_clutches({"clutches": 2}) == 2
        assert extract_clutches({"clutch_wins": 3}) == 3
        # An attempt is NOT a clutch won — never counted.
        assert extract_clutches({"clutch_attempts": 5}) == 0
        assert extract_clutches({}) == 0


@pytest.mark.unit
class TestRecordPlayerStatsWiring:
    @pytest.fixture
    def mock_session(self):
        session = AsyncMock()
        session.add = Mock()
        session.commit = AsyncMock()
        session.execute = AsyncMock()
        return session

    @pytest.fixture
    def fake_match(self):
        match = Mock()
        match.id = "match-1"
        return match

    async def _record(self, mock_session, fake_match, player_stats):
        from app.services.match_service import MatchService

        result = AsyncMock()
        result.scalar_one_or_none = Mock(return_value=fake_match)
        mock_session.execute = AsyncMock(return_value=result)
        svc = MatchService(mock_session)
        return await svc.record_player_stats("match-1", player_stats)

    async def test_computes_canonical_and_ignores_caller_score(
        self, mock_session, fake_match
    ):
        created = await self._record(
            mock_session,
            fake_match,
            [
                {
                    "player_id": "p1",
                    "team_id": "t1",
                    "kills": 8,
                    "damage_dealt": 2000,
                    "headshots": 3,
                    "revives_performed": 2,
                    "survived_time_seconds": 1500,
                    "clutches": 1,
                    "mvp_score": 9999.0,  # competing value: must be ignored
                }
            ],
        )
        assert created[0].mvp_score == pytest.approx(192.0)
        assert created[0].damage_dealt == 2000
        assert created[0].is_mvp is True  # sole player = top scorer

    async def test_unknown_damage_stays_null(self, mock_session, fake_match):
        """No real damage delivered → persisted as None, score uses 0."""
        created = await self._record(
            mock_session,
            fake_match,
            [
                {
                    "player_id": "p1",
                    "team_id": "t1",
                    "kills": 4,
                    # no damage key at all
                },
                {
                    "player_id": "p2",
                    "team_id": "t1",
                    "kills": 1,
                    "damage_dealt": None,
                },
            ],
        )
        assert created[0].damage_dealt is None
        assert created[1].damage_dealt is None
        assert created[0].mvp_score == pytest.approx(20.0)
        assert created[1].mvp_score == pytest.approx(5.0)
        # Top canonical scorer flagged.
        assert created[0].is_mvp is True
        assert created[1].is_mvp is False


@pytest.mark.unit
class TestDemoEndpointsHonesty:
    async def test_demo_mvp_uses_canonical_formula_and_null_damage(self):
        import json

        import app.main as main_module

        with patch.object(main_module, "DEMO_MODE", True):
            resp = await main_module.get_mvp()
        assert resp.status_code == 200
        body = json.loads(resp.body.decode("utf-8"))
        data = body["data"]
        assert body.get("demo") is True
        assert data["is_demo"] is True
        # Demo teams carry no real damage → null, never kills-derived.
        assert data["damage"] is None
        # Canonical score from per-player kills only: kills*5.
        assert data["mvp_score"] == pytest.approx(data["kills"] * 5)

    async def test_demo_summary_damage_is_null_not_estimate(self):
        import json

        import app.main as main_module

        with patch.object(main_module, "DEMO_MODE", True):
            resp = await main_module.get_analytics_summary()
        assert resp.status_code == 200
        body = json.loads(resp.body.decode("utf-8"))
        assert body["data"]["total_damage"] is None
        assert body["data"]["is_demo"] is True
        assert body.get("demo") is True
