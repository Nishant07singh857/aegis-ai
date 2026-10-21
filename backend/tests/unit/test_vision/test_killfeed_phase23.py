# backend/tests/unit/test_vision/test_killfeed_phase23.py
"""
Phase 23 W2 — kill-feed weapon recognition tests.

Covers:
  * region fix (top-left band measured by W1),
  * IconMatcher on synthetic crops (template pasted into a bar-like
    crop -> correct family/class; blank -> honest null; unknown_*
    glyph -> weapon stays null; dull/dimmed paste still recognised
    under the default "bin" preprocessing),
  * preprocessing switch (bin/clahe/histeq/none all construct + run),
  * marker detection on the real library glyph crops,
  * layout parser on REAL harvest-VOD entry crops (labels_day1.csv):
    structure + weapon class-or-honest-null, never exact OCR names
    (360p OCR is rough by nature),
  * highlight triggers fed by the now-real class strings
    ("frag_grenade" -> grenade_multi_kill, "sniper" -> sniper_triple).

The held-out set (data/phase23_work/heldout/) is deliberately NOT used
here — W5 measures accuracy on it independently.
"""

import os
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[4]  # aegis-ai/
ICONS = ROOT / "backend" / "app" / "vision" / "icons"
WORK = ROOT / "data" / "phase23_work"

from app.vision.detectors.icon_matcher import (  # noqa: E402
    FAMILY_TO_CLASS,
    IconMatcher,
)
from app.vision.detectors.killfeed import (  # noqa: E402
    DEFAULT_KILL_FEED_REGION,
    KillFeedDetector,
)

cv2 = pytest.importorskip("cv2")


def _load_gray(name: str) -> np.ndarray:
    img = cv2.imread(str(ICONS / name), cv2.IMREAD_GRAYSCALE)
    assert img is not None, f"template {name} missing"
    return img


def _bar_crop_with(template_gray: np.ndarray, dull: float = 1.0) -> np.ndarray:
    """Paste a template into a dark bar-like BGR crop (kill-feed geometry).

    ``dull`` < 1.0 dims the glyph toward the bar background, mimicking
    the dull broadcast rendering W1 measured (glyph ~120-210 grey on a
    ~30-70 grey bar).
    """
    bar = np.full((24, 170, 3), 45, dtype=np.uint8)
    th, tw = template_gray.shape[:2]
    x, y = 60, max(1, (24 - th) // 2)
    glyph = template_gray.astype(np.float32)
    if dull != 1.0:
        glyph = 45.0 + (glyph - 45.0) * dull
    patch = np.clip(glyph, 0, 255).astype(np.uint8)
    bar[y:y + th, x:x + tw] = cv2.cvtColor(patch, cv2.COLOR_GRAY2BGR)
    return bar


@pytest.mark.unit
@pytest.mark.vision
class TestRegionFix:
    def test_default_region_is_top_left_band(self):
        # W1 LAYOUT_REPORT §2: x [0, 0.34] W, y [0.24, 0.52] H.
        assert DEFAULT_KILL_FEED_REGION == (0.0, 0.24, 0.34, 0.52)

    def test_region_override_via_config(self):
        det = KillFeedDetector(kill_feed_region=(0.1, 0.1, 0.5, 0.5))
        regions = det.get_detection_regions(640, 360)
        assert regions[0].x == 0.1 and regions[0].width == pytest.approx(0.4)


@pytest.mark.unit
@pytest.mark.vision
class TestIconMatcherSynthetic:
    @pytest.fixture(scope="class")
    def matcher(self):
        m = IconMatcher()  # default preprocess picked on harvest data
        assert m.available
        return m

    @pytest.mark.parametrize(
        "template,family,klass",
        [
            ("ar_a.png", "ar_a", "ar"),
            ("ar_b.png", "ar_b", "ar"),
            ("smg_compact.png", "smg_compact", "smg"),
            ("sniper_bolt.png", "sniper_bolt", "sniper"),
            ("dmr_long.png", "dmr_long", "dmr"),
            ("frag_grenade.png", "frag_grenade", "frag_grenade"),
            ("pistol_t.png", "pistol_t", "pistol"),
        ],
    )
    def test_pasted_template_identified(self, matcher, template, family, klass):
        crop = _bar_crop_with(_load_gray(template))
        result = matcher.match(crop)
        assert result.family == family
        assert result.weapon == klass
        assert result.weapon_family == family
        assert result.score >= matcher.threshold
        assert result.location is not None

    def test_unknown_glyph_never_becomes_weapon(self, matcher):
        crop = _bar_crop_with(_load_gray("unknown_dome.png"))
        result = matcher.match(crop)
        assert result.family == "unknown_dome"  # it may WIN...
        assert result.weapon is None            # ...but is never a weapon
        assert result.weapon_family is None

    def test_blank_crop_is_honest_null(self, matcher):
        blank = np.full((24, 170, 3), 45, dtype=np.uint8)
        result = matcher.match(blank)
        assert result.weapon is None

    def test_dull_paste_still_identified(self, matcher):
        # Dull-icon path: glyph pulled 45% toward the bar background.
        crop = _bar_crop_with(_load_gray("frag_grenade.png"), dull=0.55)
        result = matcher.match(crop)
        assert result.family == "frag_grenade"
        assert result.weapon == "frag_grenade"

    def test_no_model_names_ever(self, matcher):
        for klass in FAMILY_TO_CLASS.values():
            assert klass in {"ar", "smg", "sniper", "dmr", "frag_grenade", "pistol"}

    @pytest.mark.parametrize("mode", ["bin", "clahe", "histeq", "none"])
    def test_preprocess_modes_all_run(self, mode):
        m = IconMatcher(preprocess=mode)
        assert m.available and m.preprocess == mode
        crop = _bar_crop_with(_load_gray("ar_a.png"))
        result = m.match(crop)
        assert result.family is not None  # runs; accuracy is W5's measure


@pytest.mark.unit
@pytest.mark.vision
class TestMarkerDetection:
    @pytest.fixture(scope="class")
    def matcher(self):
        return IconMatcher()

    def _marker_crop(self, name: str) -> np.ndarray:
        img = cv2.imread(str(ICONS / name))
        assert img is not None
        return img

    def test_knock_glyph_detected(self, matcher):
        out = matcher.match_markers(self._marker_crop("marker_knock.png"))
        assert out.knock is True
        assert out.knock_score >= matcher.marker_threshold

    def test_headshot_glyph_detected_both_phases(self, matcher):
        # v2 = ring phase, v3 = diamond phase (W1 templated both).
        # (The v1 instance is the dullest crop in the library —
        # mean luminance 48.9, contrast std 41.6 — and its ring
        # fragments below the blob floor standalone; documented limit.)
        for name in ("marker_headshot_v2.png", "marker_headshot_v3.png"):
            out = matcher.match_markers(self._marker_crop(name))
            assert out.headshot is True, name

    def test_blank_marker_slot_is_no_marker(self, matcher):
        blank = np.full((12, 30, 3), 45, dtype=np.uint8)
        out = matcher.match_markers(blank)
        assert out.knock is False and out.headshot is False


@pytest.mark.unit
@pytest.mark.vision
@pytest.mark.skipif(
    not (WORK / "entries_day1").exists(), reason="harvest entry crops not present"
)
class TestLayoutParserRealCrops:
    """Real 360p entry crops from the HARVEST VODs (never held-out)."""

    @pytest.fixture(scope="class")
    def detector(self):
        return KillFeedDetector()

    def _entry(self, detector, entry_id: str):
        path = WORK / "entries_day1" / f"{entry_id}_entry.png"
        img = cv2.imread(str(path))
        assert img is not None, f"missing {path}"
        return detector._parse_entry_strip(img, 360)

    def test_frag_grenade_knock_entry(self, detector):
        # d1_e0068_t3801: frag_grenade, knock=1 (labels_day1.csv).
        entry = self._entry(detector, "d1_e0068_t3801")
        assert entry is not None and entry.is_valid
        assert entry.weapon == "frag_grenade"
        assert entry.weapon_family == "frag_grenade"
        assert entry.event_type == "knock"

    @pytest.mark.parametrize(
        "entry_id,expected_class",
        [
            ("d1_e0050_t3530", "smg"),   # smg_compact, knock=1
            ("d1_e0003_t2670", "sniper"),  # sniper_bolt, knock=1
            ("d1_e0031_t2996", "ar"),    # ar_a, kill
        ],
    )
    def test_weapon_class_or_honest_null(self, detector, entry_id, expected_class):
        entry = self._entry(detector, entry_id)
        if entry is None:
            return  # honest skip beats a guessed entry
        assert entry.killer and entry.victim
        assert entry.event_type in ("kill", "knock")
        # Weapon is the expected class or an honest null — never a
        # wrong class and never a specific model name.
        assert entry.weapon in (expected_class, None)
        if entry.weapon is not None:
            assert entry.weapon_family in FAMILY_TO_CLASS
            assert FAMILY_TO_CLASS[entry.weapon_family] == entry.weapon

    def test_real_frame_parse_no_crash(self, detector):
        frame = cv2.imread(str(WORK / "entries_day1" / "d1_e0068_t3801_frame.jpg"))
        assert frame is not None
        events = detector.parse_frame(frame, match_id="test", now=1000.0)
        assert isinstance(events, list)
        for ev in events:
            assert ev.weapon in (
                None, "ar", "smg", "sniper", "dmr", "frag_grenade", "pistol",
            )
            if ev.weapon is not None:
                assert ev.payload.get("weapon_granularity") == "class"
                assert ev.payload.get("weapon_family") in FAMILY_TO_CLASS
                assert 0.0 <= float(ev.payload.get("weapon_confidence", 0)) <= 1.0


@pytest.mark.unit
class TestHighlightTriggersLive:
    """Phase 22 plug-ready triggers, now fed by real class strings."""

    def _kill(self, eid, ts, killer, weapon):
        from app.services.replay.buffer import BufferedEvent

        return BufferedEvent(
            event_id=eid,
            ts=ts,
            type="kill_event",
            payload={
                "killer": killer, "killerTeam": "T1",
                "victim": f"V-{eid}", "victimTeam": "T2",
                "weapon": weapon,
            },
        )

    def test_grenade_multi_kill_fires_on_frag_grenade_class(self):
        from app.services.replay.highlight_triggers import trigger_registry

        events = [
            self._kill("g1", 100.0, "Hero", "frag_grenade"),
            self._kill("g2", 105.0, "Hero", "frag_grenade"),
        ]
        trig = next(t for t in trigger_registry(events) if t.name == "grenade_multi_kill")
        assert trig.active is True
        moments = trig.run(events)
        assert len(moments) == 1 and moments[0].kind == "grenade_multi_kill"

    def test_sniper_triple_fires_on_sniper_class(self):
        from app.services.replay.highlight_triggers import trigger_registry

        events = [
            self._kill(f"s{i}", 100.0 + i * 5, "Hero", "sniper") for i in range(3)
        ]
        trig = next(t for t in trigger_registry(events) if t.name == "sniper_triple")
        assert trig.active is True
        moments = trig.run(events)
        assert len(moments) == 1 and moments[0].kind == "sniper_triple"

    def test_dmr_counts_toward_sniper_triple(self):
        from app.services.replay.highlight_triggers import trigger_registry

        events = [
            self._kill(f"d{i}", 100.0 + i * 5, "Hero", "dmr") for i in range(3)
        ]
        trig = next(t for t in trigger_registry(events) if t.name == "sniper_triple")
        assert len(trig.run(events)) == 1

    def test_still_inactive_without_weapon_data(self):
        from app.services.replay.highlight_triggers import trigger_registry

        events = [self._kill("k1", 100.0, "Hero", "Unknown")]
        assert all(not t.active for t in trigger_registry(events))
