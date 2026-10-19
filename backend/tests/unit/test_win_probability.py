# backend/tests/unit/test_win_probability.py
"""
Unit tests for the Phase 20 live win-probability scorer (W1 — G1).

Covers:
  * Base scoring is IDENTICAL to the Phase 19 storyteller heuristic
    (alive*3 + kills*2 + in_zone*5) — the two surfaces never contradict.
  * Signals: alive players, zone position, zone-phase scaling, form, H2H.
  * Missing data -> None / neutral bonuses, never invented percentages.
  * Probabilities sum to ~100; eliminated teams (alive=0) are excluded.
  * Delta vs the previous snapshot via the prediction service.

No network, no DB — the service is driven through an in-memory provider.
"""

from __future__ import annotations

import pytest

from app.agents.prediction_agents.win_probability import (
    BASE_WEIGHTS,
    attach_deltas,
    form_index,
    h2h_rate_matrix,
    score_live_win_probability,
    snapshot_map,
)
from app.api.v1.predictions.service import PredictionService


def _team(team_id, name, alive, kills=0, in_zone=True):
    return {
        "team_id": team_id,
        "team_name": name,
        "alive_count": alive,
        "kills": kills,
        "in_zone": in_zone,
    }


# --------------------------------------------------------------------- #
# Consistency with the Phase 19 storyteller
# --------------------------------------------------------------------- #


class TestStorytellerConsistency:
    def test_base_weights_identical_to_storyteller(self):
        """The storyteller's _heuristic_win_prob uses alive*3 + kills*2 +
        in_zone*5. The live scorer MUST keep the same base weights."""
        assert BASE_WEIGHTS == {"alive": 3.0, "kills": 2.0, "in_zone": 5.0}

    def test_same_ranking_as_storyteller_heuristic(self):
        from app.agents.base_agent import AgentConfig
        from app.agents.narrative_agents.storyteller import StorytellerAgent

        try:
            from app.core.event_bus import event_bus
        except Exception:
            event_bus = None
        agent = StorytellerAgent(AgentConfig(agent_type="storyteller"), event_bus)

        teams = [
            _team("t1", "Soul", 4, kills=10, in_zone=True),
            _team("t2", "XSpark", 3, kills=14, in_zone=True),
            _team("t3", "GodLike", 4, kills=6, in_zone=False),
        ]
        # _heuristic_win_prob returns insertion order; the storyteller
        # sorts by probability before display (generate_win_probability).
        # The live scorer returns pre-sorted, so compare sorted here.
        expected = sorted(
            agent._heuristic_win_prob(teams),
            key=lambda p: p["win_probability"],
            reverse=True,
        )
        expected = [p["team_name"] for p in expected]
        got = [p["team_name"] for p in score_live_win_probability(teams)]
        assert got == expected

    def test_storyteller_score_values_match_base(self):
        """With no form/H2H/zone input, numeric probabilities equal the
        storyteller's heuristic (not just the ordering)."""
        from app.agents.base_agent import AgentConfig
        from app.agents.narrative_agents.storyteller import StorytellerAgent

        try:
            from app.core.event_bus import event_bus
        except Exception:
            event_bus = None
        agent = StorytellerAgent(AgentConfig(agent_type="storyteller"), event_bus)

        teams = [
            _team("t1", "Soul", 4, kills=10),
            _team("t2", "XSpark", 2, kills=16),
            _team("t3", "GodLike", 3, kills=4),
        ]
        expected_raw = {
            p["team_id"]: p["win_probability"]
            for p in agent._heuristic_win_prob(teams)
        }
        # The live scorer returns pre-sorted; values are order-independent.
        got = {p["team_id"]: p["win_probability"] for p in score_live_win_probability(teams)}
        assert got == expected_raw


# --------------------------------------------------------------------- #
# Scorer behaviour
# --------------------------------------------------------------------- #


class TestScorer:
    def test_probabilities_sum_to_100(self):
        teams = [_team(f"t{i}", f"T{i}", alive=(i % 4) + 1, kills=i * 3) for i in range(8)]
        scored = score_live_win_probability(teams)
        assert scored is not None
        total = sum(p["win_probability"] for p in scored)
        assert 99.0 <= total <= 101.0

    def test_eliminated_teams_excluded(self):
        teams = [
            _team("t1", "Soul", 0, kills=20),
            _team("t2", "XSpark", 2, kills=1),
        ]
        scored = score_live_win_probability(teams)
        assert scored is not None
        assert [p["team_id"] for p in scored] == ["t2"]

    def test_all_eliminated_returns_none(self):
        teams = [_team("t1", "Soul", 0), _team("t2", "XSpark", 0)]
        assert score_live_win_probability(teams) is None

    def test_empty_returns_none(self):
        assert score_live_win_probability([]) is None

    def test_ordered_descending(self):
        teams = [
            _team("t1", "Weak", 1, kills=0),
            _team("t2", "Strong", 4, kills=12),
        ]
        scored = score_live_win_probability(teams)
        assert scored[0]["team_id"] == "t2"

    def test_zone_position_matters(self):
        teams = [
            _team("t1", "Inside", 2, kills=5, in_zone=True),
            _team("t2", "Outside", 2, kills=5, in_zone=False),
        ]
        scored = score_live_win_probability(teams)
        assert scored[0]["team_id"] == "t1"

    def test_late_zone_boosts_survival(self):
        teams = [
            _team("t1", "Alive", 4, kills=0),
            _team("t2", "Frags", 2, kills=10),
        ]
        early = score_live_win_probability(teams, zone_phase=1)
        late = score_live_win_probability(teams, zone_phase=5)
        early_gap = next(p for p in early if p["team_id"] == "t1")["win_probability"]
        late_gap = next(p for p in late if p["team_id"] == "t1")["win_probability"]
        assert late_gap > early_gap

    def test_form_bonus_shifts_leader(self):
        teams = [
            _team("t1", "Soul", 3, kills=8),
            _team("t2", "XSpark", 3, kills=8),
        ]
        plain = score_live_win_probability(teams)
        assert plain[0]["win_probability"] == plain[1]["win_probability"]

        form = {
            "t1": [
                {"match_id": f"m{i}", "placement": 1, "won": True} for i in range(5)
            ],
            "t2": [
                {"match_id": f"m{i}", "placement": 12, "won": False} for i in range(5)
            ],
        }
        with_form = score_live_win_probability(teams, form_by_team=form)
        assert with_form[0]["team_id"] == "t1"
        assert with_form[0]["breakdown"]["form_bonus"] > 0

    def test_thin_form_history_is_neutral(self):
        teams = [_team("t1", "Soul", 3, kills=8)]
        form = {"t1": [{"match_id": "m1", "placement": 1, "won": True}]}
        scored = score_live_win_probability(teams, form_by_team=form)
        assert scored[0]["breakdown"]["form_bonus"] == 0.0

    def test_missing_form_is_neutral(self):
        teams = [_team("t1", "Soul", 3, kills=8), _team("t2", "XSpark", 3, kills=8)]
        scored = score_live_win_probability(teams)
        assert all(p["breakdown"]["form_bonus"] == 0.0 for p in scored)

    def test_h2h_bonus_shifts_ranking(self):
        teams = [
            _team("t1", "Soul", 3, kills=8),
            _team("t2", "XSpark", 3, kills=8),
        ]
        h2h = {
            ("t1", "t2"): {"wins_a": 9, "wins_b": 1, "total": 10},
        }
        scored = score_live_win_probability(teams, h2h=h2h)
        assert scored[0]["team_id"] == "t1"
        assert scored[0]["breakdown"]["h2h_bonus"] > 0
        assert scored[1]["breakdown"]["h2h_bonus"] < 0

    def test_h2h_missing_is_neutral(self):
        teams = [_team("t1", "Soul", 3, kills=8), _team("t2", "XSpark", 3, kills=8)]
        scored = score_live_win_probability(teams, h2h={})
        assert all(p["breakdown"]["h2h_bonus"] == 0.0 for p in scored)

    def test_breakdown_present(self):
        scored = score_live_win_probability([_team("t1", "Soul", 4, kills=9)])
        b = scored[0]["breakdown"]
        assert b["base"] == pytest.approx(4 * 3.0 + 9 * 2.0 + 5.0)
        assert b["form_bonus"] == 0.0
        assert b["h2h_bonus"] == 0.0


class TestHelpers:
    def test_form_index(self):
        assert form_index(None) is None
        assert form_index([]) is None
        assert form_index([{"placement": 1, "won": True}]) is None  # thin history
        results = [
            {"placement": 1, "won": True},
            {"placement": 3, "won": False},
            {"placement": 9, "won": False},
            {"placement": 2, "won": False},
        ]
        assert form_index(results) == pytest.approx((1.0 + 0.5 + 0.0 + 0.5) / 4)

    def test_h2h_rate_matrix_drops_empty_pairs(self):
        rates = h2h_rate_matrix(
            {
                ("a", "b"): {"wins_a": 7, "wins_b": 3},
                ("a", "c"): {"wins_a": 0, "wins_b": 0},
            }
        )
        assert rates == {("a", "b"): pytest.approx(0.7)}

    def test_attach_deltas(self):
        current = [
            {"team_id": "t1", "win_probability": 40.0},
            {"team_id": "t2", "win_probability": 25.0},
        ]
        attach_deltas(current, {"t1": 35.0})
        assert current[0]["delta"] == pytest.approx(5.0)
        assert current[1]["delta"] is None  # new team: no invented delta

    def test_snapshot_map(self):
        assert snapshot_map(
            [{"team_id": "t1", "win_probability": 40.0}, {"team_id": "t2", "win_probability": None}]
        ) == {"t1": 40.0}


# --------------------------------------------------------------------- #
# Service (in-memory provider — no DB)
# --------------------------------------------------------------------- #


class FakePredictionProvider:
    """Real-shaped data, in-memory (no fabrication at runtime)."""

    def __init__(self):
        self.teams = [
            {"team_id": "t1", "team_name": "Soul", "alive_count": 4, "kills": 12, "in_zone": True},
            {"team_id": "t2", "team_name": "XSpark", "alive_count": 3, "kills": 14, "in_zone": True},
            {"team_id": "t3", "team_name": "GodLike", "alive_count": 0, "kills": 18, "in_zone": False},
        ]
        self.form = {
            "t1": [{"match_id": f"m{i}", "placement": 2, "won": False} for i in range(5)],
        }
        self.zone = {"phase": 5}

    async def get_live_teams(self, match_id):
        return self.teams

    async def get_team_match_results(self, team_id, limit=5):
        return self.form.get(team_id)

    async def get_head_to_head(self, a, b, kind="team"):
        if {a, b} == {"t1", "t2"}:
            return {"wins_a": 6, "wins_b": 4, "total": 10}
        return None

    async def get_zone_context(self, match_id):
        return self.zone


class TestPredictionService:
    def _service(self):
        return PredictionService(provider=FakePredictionProvider())

    async def test_win_probability_shape(self):
        out = await self._service().win_probability("match-1")
        assert out["match_id"] == "match-1"
        assert out["teams_alive"] == 2  # t3 eliminated
        assert out["zone_phase"] == 5
        assert "updated_at_utc" in out
        teams = out["teams"]
        assert [t["team_id"] for t in teams] == ["t1", "t2"]
        assert teams[0]["delta"] is None  # first snapshot: no delta
        probs = [t["win_probability"] for t in teams]
        assert 99.0 <= sum(probs) <= 101.0
        assert all(0 <= p <= 100 for p in probs)

    async def test_delta_on_second_snapshot(self):
        svc = self._service()
        first = await svc.win_probability("match-1")
        # Match state changes: t1 loses a player, t2 stays.
        svc.provider.teams = [
            {"team_id": "t1", "team_name": "Soul", "alive_count": 3, "kills": 12, "in_zone": True},
            {"team_id": "t2", "team_name": "XSpark", "alive_count": 3, "kills": 14, "in_zone": True},
        ]
        second = await svc.win_probability("match-1")
        by_id = {t["team_id"]: t for t in second["teams"]}
        assert by_id["t1"]["delta"] is not None
        assert by_id["t1"]["delta"] < 0  # lost a player -> dropped
        assert by_id["t2"]["delta"] is not None
        assert by_id["t2"]["delta"] > 0

    async def test_no_live_teams_returns_none(self):
        svc = self._service()
        svc.provider.teams = []
        assert await svc.win_probability("match-1") is None

    async def test_no_provider_returns_none(self):
        assert await PredictionService().win_probability("match-1") is None

    async def test_form_and_h2h_applied(self):
        out = await self._service().win_probability("match-1")
        # t1 has form history (2nd-place finishes) + 6-4 h2h edge vs t2,
        # and zone phase 5 boosts its 4 alive players.
        assert out["teams"][0]["team_id"] == "t1"
