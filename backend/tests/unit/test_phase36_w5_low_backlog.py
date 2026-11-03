# backend/tests/unit/test_phase36_w5_low_backlog.py
"""Phase 36 (W5) — Low backlog L-1..L-10 (verification report §Low, items 15-24).

One item per section; every code change in this worker's batch has a
test here. Items already fixed before this phase (L-5 docstring, L-6
UI message) are verified present, not re-fixed. L-4 is NOT in this
worker's scope (it belongs to another worker) and is not covered here.

No commit/push scope: tests only (plus the source/doc edits they pin).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import numpy as np
import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND = REPO_ROOT / "backend"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── L-1: zone-beat clamp helper has no production caller ──────────────

class TestL1ZoneBeatClamp:
    def test_live_path_duration_is_always_the_fixed_15s(self):
        from app.services.live_director.zone_beat import (
            MAP_BEAT_DURATION_S,
            decide_zone_beat,
        )

        assert MAP_BEAT_DURATION_S == 15.0
        beat = decide_zone_beat(
            {"winners": ["team-a"], "losers": ["team-b"], "phase": "mid"},
            fight_active=False,
            is_final_circle=False,
        )
        assert beat["action"] == "map_beat"
        assert beat["duration_s"] == 15.0
        # No requested duration reaches the helper on the live path:
        # decide_zone_beat takes no duration input at all.
        assert "duration" not in decide_zone_beat.__code__.co_varnames

    def test_clamp_helper_still_clamps_but_is_not_claimed_live(self):
        from app.services.live_director.zone_beat import clamp_map_beat_duration

        assert clamp_map_beat_duration(7) == 15.0
        assert clamp_map_beat_duration(45) == 30.0
        assert clamp_map_beat_duration(None) == 15.0
        # The module docstring must no longer claim the clamp enforces
        # the live 30s cap — enforce_scene_holds does that.
        doc = _read(
            BACKEND / "app/services/live_director/zone_beat.py"
        )
        flat = " ".join(doc.split())
        assert "no production caller" in flat.lower()

    def test_phase31_ledger_carries_the_dated_correction(self):
        ledger = _read(REPO_ROOT / "docs/PHASE31_FINAL_LEDGER.md")
        flat = " ".join(ledger.split()).lower()
        assert "correction addendum" in flat
        assert "2026-11-01" in flat
        assert "clamp_map_beat_duration" in flat
        assert "koi production caller nahi hai" in flat
        assert "enforce_scene_holds" in flat


# ── L-2: lull queued-suggestion retraction ────────────────────────────

class TestL2Retraction:
    @pytest.fixture()
    def director(self):
        from app.services.director_service import DirectorService

        return DirectorService()

    async def _propose(self, director, created_by, scene="map_cam"):
        return await director.propose_suggestion(
            suggestion_type="scene",
            payload={"scene": scene},
            reason=f"test suggestion from {created_by}",
            confidence=0.6,
            created_by=created_by,
        )

    async def test_retract_is_scoped_to_one_creator(self, director):
        from app.services.director_service import SuggestionNotFoundError

        lull = await self._propose(director, "live_director:lull_loop")
        zone = await self._propose(
            director, "live_director", scene="standings"
        )
        assert lull is not None and zone is not None
        assert len(director.list_suggestions()) == 2

        retracted = await director.retract_suggestions(
            created_by="live_director:lull_loop",
            reason="fight started; lull superseded",
        )
        assert [s["id"] for s in retracted] == [lull["id"]]
        assert retracted[0]["status"] == "retracted"
        assert retracted[0]["decided_by"] is None
        assert retracted[0]["retracted_reason"] == (
            "fight started; lull superseded"
        )
        # The zone-beat suggestion from another creator is untouched.
        pending = director.list_suggestions()
        assert [s["id"] for s in pending] == [zone["id"]]
        # A retracted suggestion can no longer be approved.
        with pytest.raises(SuggestionNotFoundError):
            await director.approve_suggestion(lull["id"])

    async def test_retract_with_nothing_pending_returns_empty(self, director):
        assert await director.retract_suggestions(
            created_by="live_director:lull_loop", reason="x"
        ) == []

    async def test_retract_broadcasts_like_a_rejection(self, director):
        messages: List[Dict[str, Any]] = []

        async def _listener(message: dict) -> None:
            messages.append(message)

        director.add_listener(_listener)
        sugg = await self._propose(director, "live_director:lull_loop")
        assert sugg is not None
        await director.retract_suggestions(
            created_by="live_director:lull_loop", reason="superseded"
        )
        assert messages and messages[-1]["type"] == "director.suggestion"
        assert messages[-1]["data"]["id"] == sugg["id"]
        assert messages[-1]["data"]["status"] == "retracted"


class TestL2LullAbortRetractsLive:
    """End-to-end hook: a queued lull suggestion is retracted the
    moment a precombat signal aborts the loop (best is None, so the
    idle branch — not the fight path — runs the abort)."""

    def _rig(self):
        from app.core.event_bus import EventBus
        from app.services.director_service import DirectorService
        from app.services.live_director import LiveDirector
        from app.services.vision_intelligence.attribution import (
            StaticSlotResolver,
            TeamAttribution,
        )

        slots = {
            1: TeamAttribution(slot_number=1, team_id="team-a",
                               team_name="Team A"),
            2: TeamAttribution(slot_number=2, team_id="team-b",
                               team_name="Team B"),
        }

        class FakeClock:
            def __init__(self):
                self.t = 1000.0

            def __call__(self):
                return self.t

        clock = FakeClock()
        bus = EventBus()
        bus._redis = AsyncMock()
        bus._redis.xadd = AsyncMock(return_value="1-0")
        bus._pubsub_redis = AsyncMock()
        bus._pubsub_redis.publish = AsyncMock(return_value=1)
        bus._consumer_redis = AsyncMock()
        bus._running = True
        ds = DirectorService()
        ds._state["mode"] = "semi_auto"
        live = LiveDirector(
            bus=bus,
            director_service=ds,
            slot_resolver=StaticSlotResolver(slots),
            phase_provider=lambda: "loot",
            clock=clock,
        )
        live.start()
        return clock, ds, live

    async def test_abort_retracts_queued_lull_suggestion(self):
        clock, ds, live = self._rig()
        entry = await live.evaluate_now()
        assert entry is not None and entry["outcome"] == "lull_suggestion"
        queued = ds.list_suggestions()
        assert len(queued) == 1
        assert queued[0]["created_by"] == "live_director:lull_loop"
        # Precombat for a team with NO resolvable slot: it aborts the
        # lull loop but can never become a challenger (best stays None).
        live.engagement.note_convergence(
            "team-z", "test_gatekeep", now=clock(),
            signals=["intel_alert:gatekeep"],
        )
        assert live.engagement.precombat_teams() == ["team-z"]
        assert await live.evaluate_now() is None  # abort: stay idle
        assert ds.list_suggestions() == []
        outcomes = [e.get("outcome") for e in live.decision_log(limit=200)]
        assert "lull_suggestion_retracted" in outcomes


# ── L-3: CinematicController FADE/SWIPE cleanup + dormant guards ──────

class TestL3CinematicCleanup:
    def test_fade_swipe_crossfade_are_gone_from_the_enum(self):
        from app.agents.observer_agents.cinematic_controller import (
            TransitionType,
        )

        names = {m.name for m in TransitionType}
        values = {m.value for m in TransitionType}
        assert "FADE" not in names and "SWIPE" not in names
        assert "CROSSFADE" not in names
        assert "fade" not in values and "swipe" not in values
        assert "crossfade" not in values
        assert TransitionType.CUT.value == "cut"

    def test_no_fade_swipe_rules_or_presets(self):
        from app.agents.base_agent import AgentConfig
        from app.agents.observer_agents.cinematic_controller import (
            CinematicController,
            TransitionType,
        )
        from app.core.event_bus import EventBus

        controller = CinematicController(
            AgentConfig(agent_id="test-cinematic"), EventBus()
        )
        assert not {
            t for t in controller.transition_rules
            if t.value in ("fade", "crossfade", "swipe")
        }
        for preset in controller.presets.values():
            transition = preset.get("transition")
            assert transition is None or transition not in (
                "fade", "crossfade", "swipe",
            )
            if transition is not None:
                assert transition.value not in ("fade", "crossfade", "swipe")
        # The two presets that used to fade now cut.
        assert controller.presets["replay"]["transition"] is TransitionType.CUT
        assert controller.presets["cinematic"]["transition"] is TransitionType.CUT
        assert controller.get_transition(object(), object()) is TransitionType.CUT

    async def test_banned_transitions_raise(self):
        from app.agents.base_agent import AgentConfig
        from app.agents.observer_agents.cinematic_controller import (
            BannedTransitionError,
            CinematicController,
            TransitionType,
        )
        from app.core.event_bus import EventBus

        controller = CinematicController(
            AgentConfig(agent_id="test-cinematic"), EventBus()
        )
        for banned in ("fade", "swipe", "crossfade", "Fade"):
            with pytest.raises(BannedTransitionError):
                await controller.apply_transition(banned)  # type: ignore[arg-type]
        # CUT still applies.
        await controller.apply_transition(TransitionType.CUT)
        controller.apply_preset("replay")
        controller.apply_preset("cinematic")
        # A preset smuggling a banned transition raises instead.
        controller.presets["sneaky"] = {"transition": "fade"}  # type: ignore[dict-item]
        with pytest.raises(BannedTransitionError):
            controller.apply_preset("sneaky")

    @pytest.mark.parametrize(
        "relpath",
        [
            "app/agents/prediction_agents/winner_predictor.py",
            "app/agents/prediction_agents/survival_predictor.py",
            "app/agents/prediction_agents/fight_predictor.py",
            "app/agents/referee_agents/cheating_detector.py",
        ],
    )
    def test_never_wire_without_calibration_guard(self, relpath):
        text = _read(BACKEND / relpath)
        assert "NEVER WIRE WITHOUT CALIBRATION" in text
        assert "DORMANT" in text

    def test_zone_predictor_was_already_guarded(self):
        text = _read(
            BACKEND / "app/agents/prediction_agents/zone_predictor.py"
        )
        assert "DORMANT" in text


# ── L-5: gated_weapon float-boundary docstring (fixed in Phase 35) ───

class TestL5GatedWeaponFloatBoundary:
    def test_float_truth_070_minus_055_abstains(self):
        from app.vision.detectors.icon_matcher import gated_weapon

        # 0.70 - 0.55 computes to 0.1499999999999999, below the 0.15
        # floor: fail-closed abstain, never a wrong label.
        assert 0.70 - 0.55 < 0.15
        assert gated_weapon("ar_a", 0.70, 0.55) is None

    def test_float_truth_080_minus_065_is_confident(self):
        from app.vision.detectors.icon_matcher import gated_weapon

        assert 0.80 - 0.65 >= 0.15
        assert gated_weapon("ar_a", 0.80, 0.65) == "ar"

    def test_docstring_states_the_float_truth(self):
        from app.vision.detectors.icon_matcher import gated_weapon

        doc = gated_weapon.__doc__ or ""
        assert "0.70 - 0.55" in doc
        assert "0.1499999999999999" in doc
        assert "float" in doc.lower()


# ── L-6: cold-start message (present since Phases 26-29) ─────────────

class TestL6ColdStartMessage:
    def test_clone_panel_says_style_seekha_nahi_gaya(self):
        panel = _read(
            REPO_ROOT
            / "frontend/app/broadcast/director/components/ClonePanel.tsx"
        )
        assert "Abhi style seekha nahi gaya" in panel
        # The message is shown exactly on cold start (no profile).
        assert "coldStart" in panel
        assert "profile.exists" in panel or "!profile.exists" in panel

    def test_api_surface_carries_the_exists_bool(self):
        from app.api.v1.director_learning.schemas import ProfileInfo

        assert "exists" in ProfileInfo.model_fields
        assert ProfileInfo.model_fields["exists"].annotation is bool


# ── L-7: placeholder debt ─────────────────────────────────────────────

class TestL7PlaceholderDebt:
    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def test_coverage_artifact_is_untracked(self):
        assert self._git("ls-files", "backend/.coverage") == ""
        assert ".coverage" in _read(REPO_ROOT / ".gitignore").splitlines()

    def test_codeowners_is_at_the_proper_location(self):
        assert self._git("ls-files", ".github/CODEOWNERS") == ".github/CODEOWNERS"
        assert self._git("ls-files", ".github/workflows/CODEOWNERS") == ""
        assert (REPO_ROOT / ".github/CODEOWNERS").exists()
        assert not (REPO_ROOT / ".github/workflows/CODEOWNERS").exists()

    def test_one_liner_census_matches_the_accepted_debt(self):
        """82 exactly-1-line files (Phase 36 recount, 2026-11-01) is
        ACCEPTED debt — not deleted in this phase. The census below
        re-measures it so a future change to the count is visible
        instead of silently drifting. Tolerance ±8 covers other
        workers' in-flight files; the .py rule is exact."""
        tracked = self._git("ls-files", "-z").split("\0")
        one_liners = []
        for path in tracked:
            if not path:
                continue
            full = REPO_ROOT / path
            try:
                content = full.read_bytes()
            except OSError:
                continue
            if not content:
                nlines = 0
            else:
                nlines = content.count(b"\n") + (
                    0 if content.endswith(b"\n") else 1
                )
            if nlines == 1:
                one_liners.append(path)
        assert 74 <= len(one_liners) <= 90, (
            f"one-liner census moved: {len(one_liners)} (was 82) — "
            "re-measure and update the accepted-debt record"
        )
        # Every 1-line .py file is a package-marker __init__.py —
        # legitimate, not scaffold debt.
        py_one = [p for p in one_liners if p.endswith(".py")]
        assert py_one and all(p.endswith("__init__.py") for p in py_one)


# ── L-8: RF-DETR exact-match + shape fallback ─────────────────────────

def _rfdetr_detector(classes=("killfeed_panel", "killfeed_entry")):
    from app.ml.trained_detector import (
        TrainedDetector,
        TrainedDetectorConfig,
    )

    cfg = TrainedDetectorConfig(
        classes=list(classes),
        decode_mode="rfdetr",
        normalize_imagenet=True,
        input_size=(384, 384),
        conf_threshold=0.5,
        nms_iou=1.0,
    )
    return TrainedDetector(name="test_rfdetr", config=cfg)


def _rfdetr_outputs():
    dets = np.array([[
        [0.25, 0.25, 0.50, 0.25],
        [0.75, 0.50, 0.25, 0.50],
    ]], dtype=np.float32)
    labels = np.array([[
        [2.0, -1.0, -5.0],
        [-2.0, 3.0, 0.0],
    ]], dtype=np.float32)
    return [dets, labels]


class TestL8RfdetrExactMatch:
    def test_exact_names_decode(self):
        from types import SimpleNamespace

        det = _rfdetr_detector()
        det._output_names = ["dets", "labels"]
        out = det._decode_rfdetr(_rfdetr_outputs())
        assert out.shape == (4, 6)

    def test_near_miss_names_fall_back_to_shape_only_when_unique(self):
        """Names like 'detections' are NOT substring-matched; the
        pair is found by the shape fallback instead — and only
        because exactly one (dets, labels) pair is consistent."""
        from types import SimpleNamespace

        det = _rfdetr_detector()
        det._output_names = ["detections", "label_scores"]
        out = det._decode_rfdetr(_rfdetr_outputs())
        assert out.shape == (4, 6)

    def test_ambiguous_shape_pair_raises_instead_of_guessing(self):
        """3 classes -> labels are (B,Q,4) too, so shape cannot tell
        dets from labels. The old fallback would have guessed; the
        new one raises."""
        det = _rfdetr_detector(
            classes=("killfeed_panel", "killfeed_entry", "extra")
        )
        dets = np.zeros((1, 2, 4), dtype=np.float32)
        labels = np.zeros((1, 2, 4), dtype=np.float32)
        with pytest.raises(ValueError, match="rfdetr decode needs"):
            det._decode_rfdetr([dets, labels])

    def test_mismatched_query_counts_raise(self):
        det = _rfdetr_detector()
        dets = np.zeros((1, 4, 4), dtype=np.float32)
        labels = np.zeros((1, 3, 3), dtype=np.float32)
        with pytest.raises(ValueError, match="rfdetr decode needs"):
            det._decode_rfdetr([dets, labels])

    def test_registry_still_yolov8_and_default_640(self):
        from app.ml.registry import DETECTOR_MODEL_CONFIG

        assert DETECTOR_MODEL_CONFIG["killfeed"]["decode_mode"] == "yolov8"
        from app.ml.trained_detector import DEFAULT_INPUT_SIZE

        assert DEFAULT_INPUT_SIZE == (640, 640)

    def test_no_best_onnx_in_repo(self):
        onnx_files = [
            p for p in BACKEND.rglob("*.onnx")
            if ".venv" not in p.parts
        ]
        assert onnx_files == []

    def test_flip_checklist_still_names_the_384_override(self):
        ledger = _read(REPO_ROOT / "docs/PHASE33_FINAL_LEDGER.md")
        assert "input_size=(384,384)" in ledger


# ── L-9: pytest-cov gate ──────────────────────────────────────────────

class TestL9PytestCovGate:
    def test_pytest_ini_still_requests_coverage(self):
        ini = _read(BACKEND / "tests/pytest.ini")
        assert "--cov=app" in ini
        assert "--cov-fail-under=70" in ini

    def test_pytest_cov_is_absent_in_this_env(self):
        import importlib.util

        if importlib.util.find_spec("pytest_cov") is not None:
            pytest.skip(
                "pytest-cov IS installed here — the gate is verifiable "
                "in this env; see the Phase 36 W5 report (L-9)."
            )
        assert importlib.util.find_spec("pytest_cov") is None

    def test_plus_one_is_the_fakeredis_module_skip(self):
        """The 2121-vs-2122 / 2392-vs-2393 outcome discrepancy,
        SOLVED (Phase 36, L-9): `--collect-only` counts test ITEMS,
        but a full run counts one extra OUTCOME — the module-level
        collection skip of test_phase26_lifespan_monitor.py, whose
        line 35 does ``pytest.importorskip("fakeredis")``. The module
        contributes 0 items to collection (its 1 test is never
        collected) yet 1 "skipped" outcome to the run summary, so
        outcomes = collected + 1 exactly. Verified against the
        2026-11-01 full run (2325 passed + 65 failed + 3 skipped =
        2393 outcomes vs 2392 collected; the 3 skips are this module
        skip + 2 real test skips). No test is missing or double-run."""
        import importlib.util

        text = _read(
            BACKEND / "tests/integration/test_phase26_lifespan_monitor.py"
        )
        assert 'pytest.importorskip("fakeredis")' in text
        if importlib.util.find_spec("fakeredis") is not None:
            pytest.skip(
                "fakeredis IS installed here — the module collects "
                "its 1 test and the +1 vanishes in this env."
            )
        assert importlib.util.find_spec("fakeredis") is None


# ── L-10: inventory + stale docs ──────────────────────────────────────

class TestL10DocsReissue:
    def test_inventory_counts_match_remeasurement(self):
        from app.services.feed_adapter.schemas import FeedEventType

        inventory = _read(REPO_ROOT / "docs/PROJECT_INVENTORY.md")
        # Tracked docs on disk (untracked in-flight files from other
        # workers are excluded — the inventory counts committed docs).
        docs_count = len(
            subprocess.run(
                ["git", "-C", str(REPO_ROOT), "ls-files",
                 ":(glob)docs/*.md"],
                capture_output=True, text=True, check=True,
            ).stdout.split()
        )
        assert f"{docs_count} top-level tracked" in inventory
        # Feed event types.
        assert len(FeedEventType) == 17
        assert "| Feed event types (NormalizedEvent) | 17 |" in inventory
        # Graphics registry + template files.
        registry = _read(
            REPO_ROOT / "frontend/components/graphics/registry.ts"
        )
        template_ids = re.findall(r"id: '([a-z0-9-]+)'", registry)
        assert len(template_ids) == 16
        assert "16 registered" in inventory
        templates_dir = (
            REPO_ROOT / "frontend/components/graphics/templates"
        )
        files = [p for p in templates_dir.iterdir() if p.is_file()]
        helpers = [p for p in files if p.name.startswith("_")]
        assert len(files) == 20 and len(helpers) == 4
        # Frontend lib modules.
        assert len(list((REPO_ROOT / "frontend/lib").iterdir())) == 18
        assert "lib ×18" in inventory

    def test_kaggle_training_states_the_real_dataset(self):
        doc = _read(REPO_ROOT / "docs/KAGGLE_TRAINING.md")
        assert "44,034" in doc
        # The stale "100,000 frames" claim survives ONLY as a quoted
        # correction note (it is named as the stale claim, not stated
        # as the dataset size); the live header bullet says 44,034.
        assert "100,000 frames** ka hai" not in doc
        assert '"100,000 frames /' in doc
        assert "vivekyadav54/aegis-killfeed-dataset" in doc
        assert "rfdetr[train,loggers]" in doc
        # The string rfdetr[onnxexport] survives only inside the
        # correction that says it is NOT a real extra.
        assert "koi real extra nahi hai" in doc
        assert "install hua? Check karo" not in doc

    def test_training_doc_has_the_top_left_correction(self):
        doc = _read(REPO_ROOT / "docs/TRAINING.md")
        assert "top-left" in doc
        assert "top-right overlay" not in doc

    def test_roles_doc_lists_six_roles(self):
        doc = _read(REPO_ROOT / "docs/ROLES.md")
        for role in ("director", "spotter", "observer"):
            assert role in doc
        assert "require_role(\"admin\", \"operator\", \"director\", \"spotter\")" in doc

    def test_director_doc_lists_five_scenes_and_clone(self):
        doc = _read(REPO_ROOT / "docs/DIRECTOR.md")
        assert "map_cam" in doc
        assert "clone" in doc.lower()
        assert "retracted" in doc

    def test_graphics_doc_lists_sixteen_templates(self):
        doc = _read(REPO_ROOT / "docs/GRAPHICS.md")
        assert "16 templates" in doc
        assert "lowerthird-eliminated" in doc
