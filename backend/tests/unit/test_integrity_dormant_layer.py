# backend/tests/unit/test_integrity_dormant_layer.py
"""
Integrity Fix Pack — Worker D (Dormant Layer Honesty, C3/C4/H5 family).

Pins the honest behaviour of the dormant layer after
FULL_PROJECT_INSPECTION findings H13/H14/C3/C4/H17/H18/H15/H9/H5/H33:

- ZonePredictor never fabricates a random prediction (H13)
- ClipDetector never invents a multi-kill from missing fields (H14)
- FightTrainer / FightEvaluator are honestly retired (C3/C4)
- AICommentaryStudio never returns a fake wav path / success (H17)
- FightOutcomeModel never silently returns 0.5/0.5 on model error,
  and record_outcome really counts (H18)
- TransitionController rejects Bible-banned fade/swipe (H15)
- Mock OBS websocket client never lies about being connected (H9)
- Orchestrator never constructs the deprecated director stack (H33)
  and its docstring tells the truth about production dormancy (H5)
"""

import ast
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _load_module_standalone(name: str, relative_path: str):
    """Load a module file directly, bypassing its package __init__.

    The fight_detection package ``__init__`` imports the torch-based
    model module; torch is absent in some environments and the retired
    trainer/evaluator modules are deliberately import-light (torch is
    a guarded import there), so they are loaded by file path here.
    """
    spec = importlib.util.spec_from_file_location(
        name, BACKEND_ROOT / relative_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def _make_agent(cls):
    from app.agents.base_agent import AgentConfig
    from app.core.event_bus import EventBus

    return cls(AgentConfig(agent_id="test-integrity"), EventBus())


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# H13 — ZonePredictor: no random predictions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestZonePredictorHonesty:
    @pytest.fixture
    def predictor(self):
        from app.agents.prediction_agents.zone_predictor import ZonePredictor

        return _make_agent(ZonePredictor)

    def test_predict_next_zone_unavailable_deterministic(self, predictor):
        from app.agents.prediction_agents.zone_predictor import (
            ZonePredictionUnavailable,
        )

        reasons = []
        for _ in range(2):
            with pytest.raises(ZonePredictionUnavailable) as excinfo:
                predictor.predict_next_zone(
                    current_center_x=4000.0,
                    current_center_y=4000.0,
                    current_radius=1500.0,
                    current_phase=2,
                    map_name="erangel",
                )
            reasons.append(str(excinfo.value))
        # Deterministic: the same explicit unavailable result every
        # call — never a random centre/confidence.
        assert reasons[0] == reasons[1]
        assert "dormant" in reasons[0].lower()
        # And nothing was recorded as a prediction.
        assert predictor.get_prediction() is None
        assert predictor.get_stats()["predictions_made"] == 0

    def test_get_zone_probability_unavailable(self, predictor):
        from app.agents.prediction_agents.zone_predictor import (
            ZonePrediction,
            ZonePredictionUnavailable,
        )

        fake = ZonePrediction(prediction_id="fake")
        with pytest.raises(ZonePredictionUnavailable):
            predictor.get_zone_probability(100.0, 100.0, fake)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# H14 — ClipDetector: no invented multi-kill
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestClipDetectorHonesty:
    @pytest.fixture
    def detector(self):
        from app.agents.highlight_agents.clip_detector import ClipDetector

        return _make_agent(ClipDetector)

    def test_missing_fields_produce_no_clip(self, detector):
        clip = detector.detect_clip("multi_kill", {}, {"match_id": "m1"})
        assert clip is None
        assert detector.clips == {}

    def test_missing_time_span_produce_no_clip(self, detector):
        clip = detector.detect_clip(
            "multi_kill",
            {"player_name": "Hero", "kill_count": 4},
            {"match_id": "m1"},
        )
        assert clip is None

    def test_real_multi_kill_still_detected(self, detector):
        clip = detector.detect_clip(
            "multi_kill",
            {
                "player_name": "Hero",
                "team_name": "T1",
                "kill_count": 4,
                "time_span": 4.2,
                "start_time_ms": 900000,
                "end_time_ms": 904200,
            },
            {"match_id": "m1"},
        )
        assert clip is not None
        assert clip.category.value == "multi_kill"
        assert "4 kills" in clip.title


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# C3 / C4 — trainer & evaluator honestly retired
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestFightTrainerRetired:
    def test_construction_raises_honest_error(self):
        trainer_mod = _load_module_standalone(
            "retired_trainer_under_test", "app/ml/fight_detection/trainer.py"
        )
        with pytest.raises(trainer_mod.FightTrainerRetiredError) as excinfo:
            trainer_mod.FightTrainer(None, None)
        assert "RETIRED" in str(excinfo.value)
        assert "labels" in str(excinfo.value)

    def test_train_and_loss_raise_via_unbound_instance(self):
        trainer_mod = _load_module_standalone(
            "retired_trainer_under_test2", "app/ml/fight_detection/trainer.py"
        )
        bare = object.__new__(trainer_mod.FightTrainer)
        with pytest.raises(trainer_mod.FightTrainerRetiredError):
            bare.train()
        with pytest.raises(trainer_mod.FightTrainerRetiredError):
            bare._compute_loss((MagicMock(),), {})


class TestFightEvaluatorRetired:
    def test_construction_raises_honest_error(self):
        eval_mod = _load_module_standalone(
            "retired_evaluator_under_test", "app/ml/fight_detection/evaluator.py"
        )
        with pytest.raises(eval_mod.FightEvaluatorRetiredError) as excinfo:
            eval_mod.FightEvaluator()
        assert "RETIRED" in str(excinfo.value)

    def test_evaluate_and_process_outputs_raise(self):
        eval_mod = _load_module_standalone(
            "retired_evaluator_under_test2", "app/ml/fight_detection/evaluator.py"
        )
        bare = object.__new__(eval_mod.FightEvaluator)
        with pytest.raises(eval_mod.FightEvaluatorRetiredError):
            bare.evaluate(MagicMock(), MagicMock())
        with pytest.raises(eval_mod.FightEvaluatorRetiredError):
            bare._process_outputs((MagicMock(),))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# H17 — AICommentaryStudio: no fake path / success
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestVoiceStudioHonesty:
    def test_generate_casting_audio_honest_failure(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from app.intelligence.voice_engine import AICommentaryStudio

        studio = AICommentaryStudio(mode="xtts_local")
        result = studio.generate_casting_audio("What a play!", emotion="hype")

        assert isinstance(result, dict)
        assert result["synthesized"] is False
        assert result["audio_path"] is None
        assert result["path"] is None
        assert result["reason"]
        # No wav file was created anywhere it could claim as output.
        assert list(tmp_path.rglob("*.wav")) == []

    @pytest.mark.asyncio
    async def test_cast_live_event_honest_failure(self):
        from app.intelligence.voice_engine import AICommentaryStudio

        studio = AICommentaryStudio(mode="elevenlabs")
        result = await studio.cast_live_event("clutch", "Unbelievable!")
        assert result["synthesized"] is False
        assert result["audio_path"] is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# H18 — FightOutcomeModel: no silent 0.5/0.5; real counting
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestFightOutcomeModelHonesty:
    def test_model_error_raises_not_fifty_fifty(self):
        from app.ml.prediction.fight_outcome_model import (
            FightFeatures,
            FightOutcomeModel,
            FightOutcomeUnavailable,
        )

        model = FightOutcomeModel()
        broken = MagicMock()
        broken.predict_proba.side_effect = RuntimeError("boom")
        model.model = broken

        with pytest.raises(FightOutcomeUnavailable):
            model.predict(FightFeatures(fight_id="f-err"))
        # Failed prediction is not counted as a prediction.
        assert model.get_stats()["total_predictions"] == 0

    def test_rule_based_fallback_labelled_and_counted(self):
        from app.ml.prediction.fight_outcome_model import (
            FightFeatures,
            FightOutcome,
            FightOutcomeModel,
        )

        model = FightOutcomeModel()
        pred = model.predict(
            FightFeatures(
                fight_id="f1",
                team_a_hp_percent=90.0,
                team_b_hp_percent=40.0,
            )
        )
        # Rule-based path is labelled, not dressed as an ensemble.
        assert pred.model_name == "fight_outcome_rule_based_heuristic"

        # record_outcome really counts: correct for f1, unknown for f2.
        assert model.record_outcome("f1", pred.predicted_outcome) is True
        assert model.record_outcome("unknown-fight", FightOutcome.TEAM_A_WIN) is False
        stats = model.get_stats()
        assert stats["outcomes_recorded"] == 1
        assert stats["correct_predictions"] == 1
        assert stats["accuracy"] == 1.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# H15 — TransitionController: fade/swipe rejected
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestTransitionControllerBible:
    @pytest.fixture
    def controller(self):
        from app.broadcast.transition_controller import TransitionController

        obs = AsyncMock()
        obs.send_command = AsyncMock(
            return_value=SimpleNamespace(success=True)
        )
        return TransitionController(obs)

    def test_defaults_have_no_fade_or_swipe(self, controller):
        names = " ".join(controller.transitions.keys()).lower()
        types = " ".join(
            str(t.transition_type.value) for t in controller.transitions.values()
        ).lower()
        assert "fade" not in names and "swipe" not in names
        assert "fade" not in types and "swipe" not in types

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "banned", ["Quick Fade", "Slow Fade", "Dramatic Fade", "Swipe Left", "Swipe Right"]
    )
    async def test_banned_transitions_rejected_with_error(self, controller, banned):
        from app.broadcast.transition_controller import BannedTransitionError

        with pytest.raises(BannedTransitionError):
            await controller.set_transition(banned)

    @pytest.mark.asyncio
    async def test_hard_cut_still_works(self, controller):
        assert await controller.set_transition("Cut") is True
        assert controller.active_transition == "Cut"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# H9 — Mock OBS client: never lies about connection
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestMockOBSClientHonesty:
    @pytest.mark.asyncio
    async def test_connect_does_not_claim_connected(self):
        from app.production.websocket_client import OBSWebsocketClient

        state = SimpleNamespace(is_obs_connected=False)
        client = OBSWebsocketClient(state)
        result = await client.connect()
        assert result is False
        assert state.is_obs_connected is False
        assert client.ws is None

    @pytest.mark.asyncio
    async def test_send_command_fails_honestly(self):
        from app.production.websocket_client import OBSWebsocketClient

        state = SimpleNamespace(is_obs_connected=True)  # even if flagged
        client = OBSWebsocketClient(state)
        with pytest.raises(ConnectionError):
            await client.send_command("SetCurrentProgramScene", {"sceneName": "X"})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# H5 / H33 — Orchestrator honesty
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DEPRECATED_DIRECTOR_CALLS = {
    "ObserverDirector",
    "POVSelector",
    "SafetyController",
    "PlaybookManager",
}


class TestOrchestratorHonesty:
    def test_no_deprecated_director_constructions(self):
        tree = ast.parse(
            (BACKEND_ROOT / "app/agents/orchestrator.py").read_text()
        )
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    called.add(func.id)
                elif isinstance(func, ast.Attribute):
                    called.add(func.attr)
        assert called.isdisjoint(DEPRECATED_DIRECTOR_CALLS), (
            f"deprecated director stack still constructed: "
            f"{called & DEPRECATED_DIRECTOR_CALLS}"
        )

    def test_orchestrator_does_not_claim_production(self):
        from app.agents import orchestrator

        doc = orchestrator.__doc__ or ""
        assert "DORMANT IN PRODUCTION" in doc
        assert "never imports or starts it" in doc

    def test_observer_director_attribute_is_honest_none(self):
        from app.agents.orchestrator import AgentOrchestrator

        orch = AgentOrchestrator(MagicMock())
        assert orch.observer_director is None
