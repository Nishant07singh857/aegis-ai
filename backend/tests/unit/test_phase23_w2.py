"""Phase 23 W2 — kill-feed weapon recognition, wired LIVE.

Covers:
  * IconMatcher: library loads; a template embedded in a bar canvas
    matches its own family at high confidence; blank/other crops stay
    below the emission gate; unknown_* glyphs compete but are NEVER
    emitted as a weapon; every preprocess mode runs; class ids are the
    spec ids ("ar"/"smg"/"sniper"/"dmr"/"frag_grenade"/"pistol") —
    never a gun model name.
  * Layout parse: synthetic entry strips built from the REAL template
    PNGs pasted into a rendered bar (names rendered as text) parse
    end-to-end through KillFeedDetector._parse_entry_strip: killer /
    victim split around the icon slot, weapon class, knock marker ->
    knock, headshot marker -> headshot=True, no marker -> kill.
  * Event flow: KillFeedEntry -> NormalizedEvent -> bus payload
    carries weapon + {weapon_confidence, weapon_family,
    weapon_source="icon_template"}; an unknown-glyph entry keeps
    weapon=None and notes icon_glyph="unknown".
  * Highlight triggers (services/replay): frag_grenade x2 fires
    grenade_multi_kill, sniper x3 fires sniper_triple, weapon-less
    kills keep every trigger inactive.
  * ClipDetector agent: live weapon triggers (frag fires at once,
    sniper counted per player, knocks don't count, no weapon never).
  * Real harvest material (skipped when absent): the matcher reads a
    W1 sample entry crop; parse_frame on a real 360p VOD frame runs
    without crashing (at 360p the OCR confidence gate honestly
    suppresses most names — no assertion on emitted events).

Run: python -m pytest tests/unit/test_phase23_w2.py --no-cov -o addopts=""
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import cv2  # noqa: E402

from app.vision.detectors.icon_matcher import (  # noqa: E402
    FAMILY_TO_CLASS,
    IconMatcher,
)
from app.vision.detectors.killfeed import (  # noqa: E402
    DEFAULT_KILL_FEED_REGION,
    KillFeedDetector,
    KillFeedEntry,
)

ICONS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "app", "vision", "icons"
)
HARVEST_FRAME = os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "data", "phase23_work", "entries_day1", "d1_e0003_t2670_frame.jpg",
)

SPEC_CLASS_IDS = {"ar", "smg", "sniper", "dmr", "frag_grenade", "pistol"}


def _template_bgr(name: str) -> np.ndarray:
    img = cv2.imread(os.path.join(ICONS_DIR, name))
    assert img is not None, f"template missing: {name}"
    return img


def _bar_canvas(template_name: str, scale: int = 1) -> np.ndarray:
    """Dark kill-feed bar (24px tall at native scale) with one real
    template glyph pasted in — the matcher's production input shape.
    The bar fill matches the template's own border median, as on a
    real bar (a foreign flat background would distort the matcher's
    adaptive-binarisation threshold — a harness artifact, not signal).
    """
    tpl = _template_bgr(template_name)
    gray = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
    border = np.concatenate(
        [gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]]
    )
    fill = int(np.median(border))
    if scale != 1:
        tpl = cv2.resize(
            tpl, (tpl.shape[1] * scale, tpl.shape[0] * scale),
            interpolation=cv2.INTER_LINEAR,
        )
    h = 24 * scale
    canvas = np.full((h, 140 * scale, 3), fill, dtype=np.uint8)
    y = (h - tpl.shape[0]) // 2
    x = (canvas.shape[1] - tpl.shape[1]) // 2
    canvas[y:y + tpl.shape[0], x:x + tpl.shape[1]] = tpl
    return canvas


# ----------------------------------------------------------------------
# IconMatcher
# ----------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.vision
class TestIconMatcher:
    @pytest.fixture(scope="class")
    def matcher(self):
        m = IconMatcher()  # production default preprocess ("none")
        assert m.available
        return m

    def test_library_loads_all_families(self, matcher):
        fams = matcher.families()
        for fam in ("ar_a", "ar_b", "smg_compact", "sniper_bolt", "dmr_long",
                    "frag_grenade", "pistol_t", "marker_knock",
                    "marker_headshot", "unknown_dome"):
            assert fam in fams

    def test_class_ids_are_spec_ids_never_models(self):
        assert set(FAMILY_TO_CLASS.values()) == SPEC_CLASS_IDS
        for value in FAMILY_TO_CLASS.values():
            assert value == value.lower()

    def test_template_vs_itself_high_confidence(self, matcher):
        for fname, fam, cls in (
            ("ar_a.png", "ar_a", "ar"),
            ("frag_grenade.png", "frag_grenade", "frag_grenade"),
            ("sniper_bolt.png", "sniper_bolt", "sniper"),
        ):
            m = matcher.match(_bar_canvas(fname))
            assert m.family == fam, (fname, m)
            assert m.score >= matcher.threshold, (fname, m.score)
            assert m.weapon == cls, (fname, m)
            assert m.weapon_family == fam

    def test_blank_crop_no_weapon(self, matcher):
        blank = np.full((24, 140, 3), 45, dtype=np.uint8)
        m = matcher.match(blank)
        assert m.weapon is None

    def test_unknown_glyph_competes_but_never_a_weapon(self, matcher):
        m = matcher.match(_bar_canvas("unknown_dome.png"))
        assert m.weapon is None
        # The dome may or may not win outright on a given build; when
        # it does, the family must be reported as the unknown glyph.
        if m.score >= matcher.threshold:
            assert m.family == "unknown_dome"

    def test_preprocess_modes_all_run(self):
        canvas = _bar_canvas("ar_a.png")
        for mode in ("bin", "clahe", "histeq", "none"):
            m = IconMatcher(preprocess=mode)
            assert m.available
            out = m.match(canvas)
            assert out.score >= 0.0  # runs; accuracy per-mode is W5's A/B
        # The two modes validated on harvest material must identify it.
        for mode in ("bin", "none"):
            out = IconMatcher(preprocess=mode).match(canvas)
            assert out.weapon == "ar", (mode, out)

    def test_marker_match_on_marker_crop(self, matcher):
        knock = matcher.match_markers(_bar_canvas("marker_knock.png"))
        assert knock.knock is True
        assert knock.headshot is False
        blank = matcher.match_markers(np.full((24, 60, 3), 45, dtype=np.uint8))
        assert blank.knock is False and blank.headshot is False


# ----------------------------------------------------------------------
# Layout parse on synthetic entry strips (real templates + real OCR)
# ----------------------------------------------------------------------

def _font(size: int):
    from PIL import ImageFont

    for path in (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _entry_strip(weapon_tpl: str, marker_tpl, scale: int = 3):
    """Render one kill-feed entry strip in the REAL layout:
    killer name [weapon icon] [marker(s)] victim name, on a dark bar.
    ``marker_tpl`` is a template filename, a tuple of filenames
    (co-occurring markers, pasted left-to-right), or None."""
    from PIL import Image, ImageDraw

    font = _font(11 * scale)
    killer, victim = "SoulMortal", "GodLJonathan"
    tmp = Image.new("RGB", (10, 10))
    d = ImageDraw.Draw(tmp)
    kw = int(d.textlength(killer, font=font))
    vw = int(d.textlength(victim, font=font))

    tpl = _template_bgr(weapon_tpl)
    tpl = cv2.resize(
        tpl, (tpl.shape[1] * scale, tpl.shape[0] * scale),
        interpolation=cv2.INTER_LINEAR,
    )
    markers = []
    if marker_tpl:
        names = marker_tpl if isinstance(marker_tpl, tuple) else (marker_tpl,)
        for name in names:
            mk = _template_bgr(name)
            markers.append(cv2.resize(
                mk, (mk.shape[1] * scale, mk.shape[0] * scale),
                interpolation=cv2.INTER_LINEAR,
            ))
    pad = 6 * scale
    gap = 5 * scale
    # Real layout (LAYOUT_REPORT §3): between the weapon icon's right
    # edge and the victim name there are ~30 native px holding the
    # marker slot (0-2 glyphs, ~10-14px each) + the victim team logo.
    zone = (30 + 16 * max(0, len(markers) - 1)) * scale
    height = 24 * scale
    width = pad + kw + gap + tpl.shape[1] + zone + vw + pad
    img = Image.new("RGB", (width, height), (38, 38, 38))
    draw = ImageDraw.Draw(img)
    y_text = (height - 11 * scale) // 2
    x = pad
    draw.text((x, y_text), killer, font=font, fill=(255, 255, 255))
    x += kw + gap
    strip = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    ty = (height - tpl.shape[0]) // 2
    strip[ty:ty + tpl.shape[0], x:x + tpl.shape[1]] = tpl
    mx = x + tpl.shape[1] + 3 * scale
    for mk in markers:
        my = (height - mk.shape[0]) // 2
        strip[my:my + mk.shape[0], mx:mx + mk.shape[1]] = mk
        mx += mk.shape[1] + 3 * scale
    x += tpl.shape[1] + zone
    img = Image.fromarray(cv2.cvtColor(strip, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(img)
    draw.text((x, y_text), victim, font=font, fill=(255, 255, 255))
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


@pytest.mark.unit
@pytest.mark.vision
class TestLayoutParse:
    @pytest.fixture
    def detector(self):
        det = KillFeedDetector()
        if not det.ocr_available():
            pytest.skip("tesseract unavailable")
        return det

    def test_ar_knock_entry(self, detector):
        strip = _entry_strip("ar_a.png", "marker_knock.png")
        entry = detector._parse_entry_strip(strip, 360)
        assert entry is not None and entry.is_valid
        assert entry.killer == "SoulMortal"
        assert entry.victim == "GodLJonathan"
        assert entry.event_type == "knock"
        assert entry.headshot is False
        assert entry.weapon == "ar"
        assert entry.weapon_family == "ar_a"
        assert entry.weapon_confidence >= 0.55

    def test_frag_kill_entry_no_marker(self, detector):
        strip = _entry_strip("frag_grenade.png", None)
        entry = detector._parse_entry_strip(strip, 360)
        assert entry is not None and entry.is_valid
        assert entry.event_type == "kill"
        assert entry.weapon == "frag_grenade"
        assert entry.weapon_family == "frag_grenade"

    def test_sniper_headshot_entry(self, detector):
        strip = _entry_strip("sniper_bolt.png", "marker_headshot.png")
        entry = detector._parse_entry_strip(strip, 360)
        assert entry is not None and entry.is_valid
        assert entry.event_type == "kill"  # headshot marker is not a knock
        assert entry.headshot is True
        assert entry.weapon == "sniper"

    def test_headshot_knock_cooccurrence(self, detector):
        # Real entries can carry BOTH markers (ring then figure):
        # event stays a knock AND headshot must be True — the marker
        # cross-talk guard must not merge two distinct glyphs.
        strip = _entry_strip(
            "ar_b.png", ("marker_headshot.png", "marker_knock.png")
        )
        entry = detector._parse_entry_strip(strip, 360)
        assert entry is not None and entry.is_valid
        assert entry.event_type == "knock"
        assert entry.headshot is True
        assert entry.weapon == "ar"

    def test_blank_strip_rejected(self, detector):
        blank = np.full((72, 400, 3), 40, dtype=np.uint8)
        assert detector._parse_entry_strip(blank, 360) is None


# ----------------------------------------------------------------------
# Event flow: entry -> NormalizedEvent -> bus payload
# ----------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.vision
class TestEventFlow:
    def test_weapon_event_payload(self):
        det = KillFeedDetector()
        entry = KillFeedEntry(
            killer="SoulMortal", victim="GodLJonathan", event_type="kill",
            weapon="frag_grenade", confidence=88.0, is_valid=True,
            weapon_family="frag_grenade", weapon_confidence=0.93,
            icon_family="frag_grenade",
        )
        ev = det._entry_to_event(entry, "m1", 1000.0)
        assert ev.weapon == "frag_grenade"
        assert ev.payload["weapon_source"] == "icon_template"
        assert ev.payload["weapon_family"] == "frag_grenade"
        assert ev.payload["weapon_confidence"] == 0.93
        bus = ev.to_bus_payload()
        assert bus["weapon"] == "frag_grenade"
        assert bus["killer"] == "SoulMortal"
        assert bus["isKnock"] is False

    def test_unknown_glyph_event_keeps_weapon_null(self):
        det = KillFeedDetector()
        entry = KillFeedEntry(
            killer="AOne", victim="BTwo", event_type="knock",
            weapon=None, confidence=80.0, is_valid=True,
            icon_family="unknown_dome",
        )
        ev = det._entry_to_event(entry, "m1", 1000.0)
        assert ev.weapon is None
        assert ev.payload["icon_glyph"] == "unknown"
        assert ev.payload["icon_family"] == "unknown_dome"
        assert ev.to_bus_payload()["weapon"] == "Unknown"

    def test_default_region_is_top_left_band(self):
        assert DEFAULT_KILL_FEED_REGION == (0.0, 0.24, 0.34, 0.52)


# ----------------------------------------------------------------------
# Highlight triggers — live on the spec class ids
# ----------------------------------------------------------------------

def _kill(eid, ts, killer, weapon=None, victim="V"):
    from app.services.replay.buffer import BufferedEvent

    payload = {
        "killer": killer, "killerTeam": "T1",
        "victim": victim, "victimTeam": "VT",
    }
    if weapon:
        payload["weapon"] = weapon
    return BufferedEvent(event_id=eid, ts=ts, type="kill_event", payload=payload)


@pytest.mark.unit
class TestHighlightTriggersLive:
    def test_frag_grenade_multi_kill_fires(self):
        from app.services.replay.highlight_triggers import trigger_registry

        events = [
            _kill("g1", 100.0, "Hero", weapon="frag_grenade"),
            _kill("g2", 105.0, "Hero", weapon="frag_grenade", victim="V2"),
        ]
        trig = next(
            t for t in trigger_registry(events) if t.name == "grenade_multi_kill"
        )
        assert trig.active is True
        moments = trig.run(events)
        assert len(moments) == 1 and moments[0].kind == "grenade_multi_kill"

    def test_sniper_triple_fires(self):
        from app.services.replay.highlight_triggers import trigger_registry

        events = [
            _kill(f"s{i}", 100.0 + i * 5, "Snip", weapon="sniper", victim=f"V{i}")
            for i in range(3)
        ]
        trig = next(
            t for t in trigger_registry(events) if t.name == "sniper_triple"
        )
        assert trig.active is True
        assert len(trig.run(events)) == 1

    def test_no_weapon_no_activation(self):
        from app.services.replay.highlight_triggers import trigger_registry

        events = [_kill("k1", 100.0, "Hero")]
        assert all(not t.active for t in trigger_registry(events))


@pytest.mark.unit
@pytest.mark.agents
class TestClipDetectorWeaponTriggers:
    @pytest.fixture
    def agent(self):
        from app.agents.base_agent import AgentConfig
        from app.agents.highlight_agents.clip_detector import ClipDetector
        from app.core.event_bus import EventBus

        return ClipDetector(AgentConfig(agent_id="test-w2"), EventBus())

    def test_frag_fires_immediately(self, agent):
        clip = agent.check_weapon_highlight(
            {"killer": "Hero", "victim": "V1", "weapon": "frag_grenade",
             "isKnock": False, "killerTeam": "T1"},
            {"match_id": "m1"}, now=1000.0,
        )
        assert clip is not None
        assert clip.category.value == "grenade_kill"

    def test_sniper_counted_per_player_knocks_excluded(self, agent):
        ev = {"killer": "Snip", "weapon": "sniper", "isKnock": False}
        assert agent.check_weapon_highlight(ev, {}, now=1000.0) is None
        assert agent.check_weapon_highlight(
            {**ev, "isKnock": True}, {}, now=1005.0) is None  # knock: no count
        assert agent.check_weapon_highlight(ev, {}, now=1010.0) is None
        clip = agent.check_weapon_highlight(ev, {}, now=1020.0)
        assert clip is not None
        assert "SNIPER TRIPLE" in clip.title

    def test_no_weapon_never_fires(self, agent):
        for weapon in (None, "", "Unknown"):
            assert agent.check_weapon_highlight(
                {"killer": "X", "victim": "Y", "weapon": weapon},
                {}, now=1000.0,
            ) is None


# ----------------------------------------------------------------------
# Real harvest material (smoke level; W5 owns accuracy measurement)
# ----------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.vision
class TestRealHarvestMaterial:
    def test_matcher_on_w1_sample_entry(self):
        # sample_ar_a_entry.png is the very entry ar_a was harvested
        # from, so this is a pipeline smoke test, NOT an accuracy
        # measurement (held-out accuracy is W5's job).
        path = os.path.join(ICONS_DIR, "sample_ar_a_entry.png")
        if not os.path.exists(path):
            pytest.skip("sample entry missing")
        img = cv2.imread(path)
        m = IconMatcher().match(img)
        assert m.family == "ar_a"
        assert m.weapon == "ar"

    def test_parse_frame_on_real_vod_frame_no_crash(self):
        if not os.path.exists(HARVEST_FRAME):
            pytest.skip("harvest frame missing")
        frame = cv2.imread(HARVEST_FRAME)
        assert frame is not None
        det = KillFeedDetector()
        events = det.parse_frame(frame, match_id="harvest-smoke")
        # 360p VOD text is below the OCR confidence gate for most
        # names; emitting nothing is the honest outcome. We assert
        # only that the pipeline runs and returns well-formed events.
        assert isinstance(events, list)
        for ev in events:
            assert ev.weapon in SPEC_CLASS_IDS or ev.weapon is None
