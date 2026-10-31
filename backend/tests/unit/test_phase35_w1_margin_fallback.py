# backend/tests/unit/test_phase35_w1_margin_fallback.py
"""Phase 35 Worker 1 — margin-knob clamp (M-1) + monitor-fallback gating (M-2).

M-1: the Phase 32 margin gate had a per-instance knob with no floor:
``IconMatcher(margin_threshold=-1.0)`` handed out labels at a 0.01
margin (verification report §2). The effective threshold is now
clamped to [0.15, 1.0] at the single choke point
(``clamp_margin_threshold``, applied in ``gated_weapon`` AND on every
``IconMatcher.margin_threshold`` assignment), so no config/caller
value can loosen the gate below the measured 0.15 floor.

M-2: ``multi_feed_monitor._entry_to_event``'s fallback passed a
foreign detector's ``weapon`` verbatim. It now routes the label
through the same gated_weapon/abstention contract: margin < 0.15 (or
no margin evidence at all) -> UNKNOWN/abstain, never a confident
verbatim name. Mirroring a pre-set abstention flag is not enough —
the label itself is re-checked.

Float honesty (report §2 / L-5): the gate compares the COMPUTED
float difference, so ``0.70 - 0.55 == 0.1499999999999999`` abstains
while ``0.80 - 0.65 == 0.15000000000000002`` is confident. Both
behaviours are asserted as-is; no test claims exact decimal-boundary
inclusivity beyond what floats deliver, and no wrong label is ever
emitted either way.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from app.vision.capture.config import CaptureConfig
from app.vision.capture.frame_grabber import CapturedFrame, FrameSource
from app.vision.detectors.icon_matcher import (
    MARGIN_THRESHOLD,
    IconMatcher,
    clamp_margin_threshold,
    gated_weapon,
)
from app.vision.detectors.killfeed import KillFeedDetector
from app.vision.multi_feed_monitor import (
    FeedReaderBundle,
    FeedSourceSpec,
    MultiFeedMonitorService,
    _entry_to_event,
    _gate_foreign_weapon,
    read_killfeed_candidates,
)

pytestmark = pytest.mark.unit

MATCH = "match-1"
T0 = 1_000.0

_CROP = np.zeros((24, 170, 3), dtype=np.uint8)


# ══════════════════════════════════════════════════════════════════
# M-1 — clamp helper
# ══════════════════════════════════════════════════════════════════


class TestClampMarginThreshold:
    @pytest.mark.parametrize("value", [-1.0, 0.0, 0.149, -100.0, "-0.5", "0.05"])
    def test_below_floor_clamps_up_to_floor(self, value):
        assert clamp_margin_threshold(value) == 0.15

    @pytest.mark.parametrize("value", [0.15, 0.25, 0.5, 1.0])
    def test_in_range_values_accepted_unchanged(self, value):
        assert clamp_margin_threshold(value) == value

    def test_above_ceiling_clamps_down_to_one(self):
        assert clamp_margin_threshold(1.5) == 1.0
        assert clamp_margin_threshold(100.0) == 1.0

    def test_non_numeric_nan_and_infinities_fail_closed(self):
        assert clamp_margin_threshold("junk") == 0.15
        assert clamp_margin_threshold(None) == 0.15
        assert clamp_margin_threshold(float("nan")) == 0.15
        assert clamp_margin_threshold(float("-inf")) == 0.15
        assert clamp_margin_threshold(float("inf")) == 1.0

    def test_result_always_finite_and_in_range(self):
        for value in (-1e9, -1.0, 0.0, 0.149999, 0.15, 0.5, 1.0, 1.0001, 1e9):
            out = clamp_margin_threshold(value)
            assert math.isfinite(out)
            assert 0.15 <= out <= 1.0


class TestGatedWeaponClamp:
    def test_loosened_knob_cannot_hand_out_labels(self):
        # The verification probe: margin 0.01 with a -1.0 knob.
        for knob in (-1.0, 0.0, 0.149, None):
            assert gated_weapon("ar_a", 0.80, 0.79, margin_threshold=knob) is None

    def test_stricter_knob_is_preserved(self):
        # A caller may demand MORE than the floor, never less.
        assert gated_weapon("ar_a", 0.80, 0.50, margin_threshold=0.5) is None
        assert gated_weapon("ar_a", 0.80, 0.20, margin_threshold=0.5) == "ar"
        assert gated_weapon("ar_a", 0.80, 0.20, margin_threshold=1.0) is None
        assert gated_weapon("ar_a", 0.80, 0.20, margin_threshold=1.5) is None

    def test_float_boundary_behaviour_is_fail_closed(self):
        # Computed floats, not decimal intent (see module docstring).
        assert repr(0.70 - 0.55) == "0.1499999999999999"
        assert gated_weapon("ar_a", 0.70, 0.55) is None  # abstains, never wrong
        assert repr(0.80 - 0.65) == "0.15000000000000002"
        assert gated_weapon("ar_a", 0.80, 0.65) == "ar"


class TestIconMatcherKnobClamp:
    @pytest.mark.parametrize("value", [-1.0, 0.0, 0.149])
    def test_loosened_construction_clamps_to_floor(self, value):
        assert IconMatcher(margin_threshold=value).margin_threshold == 0.15

    @pytest.mark.parametrize("value", [0.15, 0.25, 0.5, 1.0])
    def test_in_range_construction_accepted(self, value):
        assert IconMatcher(margin_threshold=value).margin_threshold == value

    def test_above_ceiling_construction_clamps_down(self):
        assert IconMatcher(margin_threshold=2.0).margin_threshold == 1.0

    def test_post_construction_mutation_is_clamped_too(self):
        m = IconMatcher(margin_threshold=0.5)
        m.margin_threshold = -1.0
        assert m.margin_threshold == 0.15
        m.margin_threshold = 0.25
        assert m.margin_threshold == 0.25

    def test_killfeed_config_path_is_clamped(self):
        # killfeed.py itself is untouched (another worker's area);
        # its config value flows through IconMatcher, so the clamp
        # covers it without editing that file.
        assert KillFeedDetector(icon_margin_threshold=-1.0)._get_matcher().margin_threshold == 0.15
        assert KillFeedDetector(icon_margin_threshold=0.25)._get_matcher().margin_threshold == 0.25
        assert KillFeedDetector()._get_matcher().margin_threshold == 0.15


def _stub_matcher(per_family, margin_threshold):
    """IconMatcher with controlled scores, real gating code.

    Mirrors the Phase 32 stub: ``_slide_best`` is stubbed, the
    ranking / second-score / margin-gate code under test is the real
    ``match_candidates`` implementation. The knob is assigned through
    the property setter, exactly as construction assigns it.
    """
    m = IconMatcher.__new__(IconMatcher)
    m.icons_dir = ""
    m.preprocess = "none"
    m.threshold = 0.55
    m.marker_threshold = 0.50
    m.upscale = 4
    m.scales = (1.0,)
    m.margin_threshold = margin_threshold
    m._templates = {fam: [] for fam in per_family}
    m._raw_sizes = {}
    m._loaded = True
    m._available = True
    m._lock = None

    def _slide(query, families):
        return {fam: (score, (0, 0, 10, 10)) for fam, score in per_family.items()}

    m._slide_best = _slide  # type: ignore[method-assign]
    m._ensure_loaded = lambda: None  # type: ignore[method-assign]
    return m


class TestLoosenedMatcherAbstains:
    def test_would_be_loosened_instance_still_abstains(self):
        # margin 0.80 - 0.79 = 0.01: the old -1.0 knob labelled "ar".
        m = _stub_matcher({"ar_a": 0.80, "smg_compact": 0.79}, margin_threshold=-1.0)
        assert m.margin_threshold == 0.15
        top = m.match_candidates(_CROP, top_n=4)[0]
        assert top.weapon is None
        assert top.weapon_abstained is True
        assert top.weapon_family is None

    def test_loosened_instance_abstains_on_float_boundary_pair(self):
        m = _stub_matcher({"ar_a": 0.70, "smg_compact": 0.55}, margin_threshold=0.0)
        top = m.match_candidates(_CROP, top_n=4)[0]
        assert top.weapon is None
        assert top.weapon_abstained is True

    def test_stricter_instance_keeps_its_gate(self):
        m = _stub_matcher({"ar_a": 0.80, "smg_compact": 0.50}, margin_threshold=0.5)
        top = m.match_candidates(_CROP, top_n=4)[0]
        assert top.weapon is None  # margin 0.30 < stricter 0.50
        assert top.weapon_abstained is True
        m2 = _stub_matcher({"ar_a": 0.80, "smg_compact": 0.20}, margin_threshold=0.5)
        top2 = m2.match_candidates(_CROP, top_n=4)[0]
        assert top2.weapon == "ar"


# ══════════════════════════════════════════════════════════════════
# M-2 — monitor fallback gating
# ══════════════════════════════════════════════════════════════════


class ForeignDetector:
    """A foreign detector with NO _entry_to_event builder, so the
    monitor fallback in _entry_to_event is the path under test."""

    name = "foreign"


def _foreign_entry(**overrides):
    entry = SimpleNamespace(
        killer="Alpha",
        victim="Bravo",
        event_type="kill",
        weapon=None,
        raw_text="Alpha Bravo",
        confidence=92.0,
        headshot=False,
        weapon_abstained=False,
    )
    for key, value in overrides.items():
        setattr(entry, key, value)
    return entry


def _fallback_event(entry):
    return _entry_to_event(ForeignDetector(), entry, MATCH, T0)


def _assert_flag_label_agree(event, *, claimed_label: bool):
    """Flag + label agreement: a dropped label always carries the
    abstention flag; a shipped label never does; no label claimed
    and no flag set stays flag-free."""
    if event.weapon is not None:
        assert event.payload["weapon_abstained"] is False
    elif claimed_label:
        assert event.payload["weapon_abstained"] is True
    assert "weapon_family" not in event.payload or event.weapon is not None


class TestFallbackConfusableAbstains:
    def test_margin_below_floor_abstains(self):
        ev = _fallback_event(_foreign_entry(
            weapon="ar", weapon_family="ar_a",
            weapon_confidence=0.80, weapon_margin=0.10,
        ))
        assert ev.weapon is None
        assert ev.payload["weapon_abstained"] is True
        assert ev.payload["weapon_margin"] == pytest.approx(0.10)
        assert "weapon_family" not in ev.payload
        _assert_flag_label_agree(ev, claimed_label=True)

    def test_margin_from_second_score_pair_abstains(self):
        ev = _fallback_event(_foreign_entry(
            weapon="ar", weapon_family="ar_a",
            weapon_confidence=0.80, second_score=0.70,
        ))
        assert ev.weapon is None
        assert ev.payload["weapon_abstained"] is True
        assert ev.payload["weapon_margin"] == pytest.approx(0.10)

    def test_float_boundary_pair_abstains(self):
        # 0.70 - 0.55 computes below the floor: fail-closed, as in
        # the production matcher.
        ev = _fallback_event(_foreign_entry(
            weapon="ar", weapon_family="ar_a",
            weapon_confidence=0.70, second_score=0.55,
        ))
        assert ev.weapon is None
        assert ev.payload["weapon_abstained"] is True


class TestFallbackConfidentPasses:
    def test_confident_family_label_passes(self):
        ev = _fallback_event(_foreign_entry(
            weapon="ar", weapon_family="ar_a",
            weapon_confidence=0.85, weapon_margin=0.30,
        ))
        assert ev.weapon == "ar"
        assert ev.payload["weapon_abstained"] is False
        assert ev.payload["weapon_margin"] == pytest.approx(0.30)
        assert ev.payload["weapon_family"] == "ar_a"
        assert ev.payload["weapon_source"] == "icon_template"
        assert ev.payload["weapon_granularity"] == "class"
        assert ev.payload["weapon_confidence"] == pytest.approx(0.85)
        _assert_flag_label_agree(ev, claimed_label=True)

    def test_confident_class_only_label_passes_without_family_claims(self):
        ev = _fallback_event(_foreign_entry(
            weapon="sniper", weapon_confidence=0.90, weapon_margin=0.40,
        ))
        assert ev.weapon == "sniper"
        assert ev.payload["weapon_abstained"] is False
        assert "weapon_family" not in ev.payload
        assert "weapon_source" not in ev.payload

    def test_confident_float_boundary_pair_passes(self):
        ev = _fallback_event(_foreign_entry(
            weapon="ar", weapon_family="ar_a",
            weapon_confidence=0.80, second_score=0.65,
        ))
        assert ev.weapon == "ar"
        assert ev.payload["weapon_abstained"] is False


class TestFallbackNoEvidenceAbstains:
    def test_no_margin_evidence_abstains_despite_high_score(self):
        ev = _fallback_event(_foreign_entry(
            weapon="ar", weapon_family="ar_a", weapon_confidence=0.95,
        ))
        assert ev.weapon is None
        assert ev.payload["weapon_abstained"] is True
        _assert_flag_label_agree(ev, claimed_label=True)

    def test_bare_label_with_no_evidence_abstains(self):
        ev = _fallback_event(_foreign_entry(weapon="ar"))
        assert ev.weapon is None
        assert ev.payload["weapon_abstained"] is True

    def test_ocr_confidence_is_not_icon_score_evidence(self):
        # entry.confidence is OCR confidence (0-100 scale); it must
        # never satisfy the icon score gate on its own.
        ev = _fallback_event(_foreign_entry(
            weapon="ar", confidence=95.0, weapon_margin=0.30,
        ))
        assert ev.weapon is None
        assert ev.payload["weapon_abstained"] is True

    def test_nan_margin_abstains(self):
        ev = _fallback_event(_foreign_entry(
            weapon="ar", weapon_family="ar_a",
            weapon_confidence=0.90, weapon_margin=float("nan"),
        ))
        assert ev.weapon is None
        assert ev.payload["weapon_abstained"] is True

    def test_below_score_threshold_abstains_even_with_margin(self):
        ev = _fallback_event(_foreign_entry(
            weapon="ar", weapon_family="ar_a",
            weapon_confidence=0.54, weapon_margin=0.40,
        ))
        assert ev.weapon is None
        assert ev.payload["weapon_abstained"] is True
        ev2 = _fallback_event(_foreign_entry(
            weapon="ar", weapon_confidence=0.50, weapon_margin=0.40,
        ))
        assert ev2.weapon is None
        assert ev2.payload["weapon_abstained"] is True


class TestFallbackNeverShipsUngatedNames:
    def test_model_name_never_ships(self):
        ev = _fallback_event(_foreign_entry(
            weapon="m416", weapon_confidence=0.95, weapon_margin=0.40,
        ))
        assert ev.weapon is None
        assert ev.payload["weapon_abstained"] is True

    def test_family_id_as_weapon_never_ships(self):
        ev = _fallback_event(_foreign_entry(
            weapon="ar_a", weapon_family="ar_a",
            weapon_confidence=0.85, weapon_margin=0.30,
        ))
        assert ev.weapon is None
        assert ev.payload["weapon_abstained"] is True

    def test_inconsistent_family_and_claim_abstains(self):
        ev = _fallback_event(_foreign_entry(
            weapon="sniper", weapon_family="ar_a",
            weapon_confidence=0.85, weapon_margin=0.30,
        ))
        assert ev.weapon is None
        assert ev.payload["weapon_abstained"] is True

    def test_dict_weapon_is_never_unwrapped(self):
        ev = _fallback_event(_foreign_entry(
            weapon={"weapon_family": "ar_a"},
            weapon_confidence=0.85, weapon_margin=0.30,
        ))
        assert ev.weapon is None
        assert ev.payload["weapon_abstained"] is True

    def test_unknown_family_never_ships(self):
        ev = _fallback_event(_foreign_entry(
            weapon="ar", weapon_family="unknown_dome",
            weapon_confidence=0.95, weapon_margin=0.80,
        ))
        assert ev.weapon is None
        assert ev.payload["weapon_abstained"] is True


class TestFallbackAbstentionFlagAgreement:
    def test_foreign_abstention_flag_is_respected_not_overridden(self):
        # Mirroring the flag is necessary but NOT sufficient: the
        # label is re-checked, and a flag set by the foreign detector
        # is never flipped to confident by this fallback.
        ev = _fallback_event(_foreign_entry(
            weapon="ar", weapon_family="ar_a", weapon_abstained=True,
            weapon_confidence=0.85, weapon_margin=0.30,
        ))
        assert ev.weapon is None
        assert ev.payload["weapon_abstained"] is True

    def test_already_abstained_no_label_flag_preserved(self):
        ev = _fallback_event(_foreign_entry(
            weapon=None, weapon_abstained=True, weapon_margin=0.10,
        ))
        assert ev.weapon is None
        assert ev.payload["weapon_abstained"] is True
        assert ev.payload["weapon_margin"] == pytest.approx(0.10)

    def test_no_label_no_flag_stays_flag_free(self):
        # The pre-existing FakeKillfeedReader shape: no weapon, no
        # abstention claim. The fallback must not invent a flag.
        ev = _fallback_event(_foreign_entry())
        assert ev.weapon is None
        assert ev.payload["weapon_abstained"] is False
        assert ev.payload["weapon_margin"] == 0.0

    @pytest.mark.parametrize("kwargs,passes", [
        ({"weapon": "ar", "weapon_family": "ar_a", "weapon_confidence": 0.80,
          "weapon_margin": 0.10}, False),
        ({"weapon": "ar", "weapon_family": "ar_a", "weapon_confidence": 0.85,
          "weapon_margin": 0.30}, True),
        ({"weapon": "ar", "weapon_confidence": 0.90, "weapon_margin": 0.40}, True),
        ({"weapon": "ar"}, False),
        ({"weapon": "m416", "weapon_confidence": 0.95, "weapon_margin": 0.40}, False),
        ({"weapon": None}, False),
    ])
    def test_flag_and_label_agree_on_every_path(self, kwargs, passes):
        ev = _fallback_event(_foreign_entry(**kwargs))
        if passes:
            assert ev.weapon is not None
            assert ev.payload["weapon_abstained"] is False
        elif kwargs.get("weapon"):
            assert ev.weapon is None
            assert ev.payload["weapon_abstained"] is True
        else:
            assert ev.weapon is None
            assert ev.payload["weapon_abstained"] is False


class TestGateHelperDirect:
    def test_gate_returns_tuple_contract(self):
        weapon, abstained, margin, family, score = _gate_foreign_weapon(
            _foreign_entry(
                weapon="ar", weapon_family="ar_a",
                weapon_confidence=0.85, weapon_margin=0.30,
            )
        )
        assert (weapon, abstained, margin, family, score) == (
            "ar", False, pytest.approx(0.30), "ar_a", pytest.approx(0.85),
        )

    def test_gate_abstains_without_margin_evidence(self):
        weapon, abstained, margin, _family, _score = _gate_foreign_weapon(
            _foreign_entry(weapon="ar", weapon_confidence=0.95)
        )
        assert weapon is None and abstained is True and margin is None


# ══════════════════════════════════════════════════════════════════
# M-2 — gate on the live candidate path (not just the helper)
# ══════════════════════════════════════════════════════════════════


class ForeignReadEntriesDetector:
    """read_entries seam with a confusable foreign weapon."""

    name = "foreign"

    def __init__(self, entry):
        self._entry = entry

    def read_entries(self, image):
        return [self._entry]


class TestFallbackOnLiveCandidatePath:
    def test_confusable_foreign_entry_abstains_via_candidates(self):
        entry = _foreign_entry(
            weapon="ar", weapon_family="ar_a",
            weapon_confidence=0.80, weapon_margin=0.10,
        )
        detector = ForeignReadEntriesDetector(entry)
        candidates = read_killfeed_candidates(detector, _CROP, MATCH, T0)
        assert len(candidates) == 1
        ev = candidates[0].make_event(T0)
        assert ev.weapon is None
        assert ev.payload["weapon_abstained"] is True

    def test_confident_foreign_entry_passes_via_candidates(self):
        entry = _foreign_entry(
            weapon="ar", weapon_family="ar_a",
            weapon_confidence=0.85, weapon_margin=0.30,
        )
        detector = ForeignReadEntriesDetector(entry)
        candidates = read_killfeed_candidates(detector, _CROP, MATCH, T0)
        ev = candidates[0].make_event(T0)
        assert ev.weapon == "ar"
        assert ev.payload["weapon_abstained"] is False

    async def test_confusable_foreign_entry_abstains_via_service(self):
        """Full monitor path: vote 2-of-3 confirms the entry, the
        emitted event still carries no ungated weapon."""
        entry = _foreign_entry(
            weapon="ar", weapon_family="ar_a",
            weapon_confidence=0.80, weapon_margin=0.10,
        )
        config = CaptureConfig(
            backend="none", monitor_tick_s=0.05, monitor_batch_size=4,
            monitor_stale_after_s=6.0, monitor_no_signal_after_s=6.0,
            monitor_vote_window=3, monitor_vote_threshold=2,
            monitor_dedup_ttl_s=30.0,
        )
        ingested = []

        async def ingest(event):
            ingested.append(event)
            return "stub"

        def factory(slot, source_id):
            return FeedReaderBundle(
                killfeed=ForeignReadEntriesDetector(entry))

        svc = MultiFeedMonitorService(
            ingest=ingest, config=config, match_id=MATCH,
            detector_factory=factory,
        )
        frame = CapturedFrame(
            frame_id=0, source_id="aegis-observer-slot-03",
            source_type=FrameSource.ADB_DEVICE, image=_CROP,
            width=170, height=24,
        )
        holder = SimpleNamespace(frame=frame, calls=0)

        def provider():
            holder.calls += 1
            return holder.frame

        svc.add_feed(FeedSourceSpec(3, "aegis-observer-slot-03", provider))
        assert await svc.sample_feed_once("aegis-observer-slot-03") == []
        # Second sighting on CHANGED pixels: the Phase 28 band-change
        # pre-filter may skip an unchanged band, and this probe is
        # about the weapon gate, not the pre-filter.
        holder.frame = CapturedFrame(
            frame_id=1, source_id="aegis-observer-slot-03",
            source_type=FrameSource.ADB_DEVICE,
            image=np.full((24, 170, 3), 7, dtype=np.uint8),
            width=170, height=24,
        )
        emitted = await svc.sample_feed_once("aegis-observer-slot-03")
        assert len(emitted) == 1
        assert emitted[0].weapon is None
        assert emitted[0].payload["weapon_abstained"] is True
        assert emitted[0].payload["observer_slot"] == 3
        assert len(ingested) == 1
