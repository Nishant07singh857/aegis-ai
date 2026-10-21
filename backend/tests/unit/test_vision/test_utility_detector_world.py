# backend/tests/unit/test_vision/test_utility_detector_world.py
"""
Phase 23 (W3) — world-side utility detector tests.

Synthetic deterministic frames (numpy) proving each detector fires /
does not fire appropriately:

* gray soft blob (persisting)            -> smoke (after 2 passes)
* HUD-like gray row / sky band           -> nothing (the Phase 23 audit
  found the OLD detector firing on exactly these)
* orange flickering region (persisting)  -> molotov (flicker confirm)
* small one-frame orange flash           -> nothing (muzzle flash)
* static orange region (no flicker)      -> nothing (signage)
* plain gameplay-like frame              -> nothing
* white flash                            -> nothing by default
  (stun gated OFF); with the gate ON: in-scene flash -> stun,
  flash across a hard cut -> nothing (broadcast transition guard)
* bright orange explosion blob           -> no "grenade" by default
  (explosion path gated OFF — inseparable from molotov/muzzle on
  real footage, see utility_detector module docstring)
"""

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from app.vision.detectors.utility_detector import UtilityDetector

H, W = 360, 640


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt=0.4):
        self.t += dt


def gameplay_frame(seed=7):
    """Dark textured 'world' frame: green/brown noise, no utility."""
    rng = np.random.default_rng(seed)
    frame = rng.integers(30, 90, size=(H, W, 3), dtype=np.uint8)
    frame[:, :, 1] = np.clip(frame[:, :, 1].astype(int) + 25, 0, 255).astype(np.uint8)
    return frame


def add_smoke(frame, cx=320, cy=250, radius=75, value=185):
    out = frame.copy()
    cv2.circle(out, (cx, cy), radius, (value, value, value), -1)
    out = cv2.GaussianBlur(out, (51, 51), 20)
    return out


def add_fire(frame, cx=320, cy=260, radius=40):
    """Flame-like blob: orange body + near-white/yellow core (the
    structure the detector's flame-core test requires; a flat orange
    disc is a painted object, not a flame)."""
    out = frame.copy()
    cv2.circle(out, (cx, cy), radius, (0, 140, 255), -1)       # orange
    cv2.circle(out, (cx, cy), max(3, radius // 2), (0, 215, 255), -1)  # core
    return out


def add_orange_car(frame, cx=320, cy=260, radius=40):
    """Flat saturated orange, NO flame core — the measured real
    false positive (orange vehicle under a panning camera)."""
    out = frame.copy()
    cv2.circle(out, (cx, cy), radius, (0, 110, 230), -1)
    return out


def hud_like_frame():
    """The measured real-footage false positive: a row of light-gray
    HUD rectangles at y=119 (h=37) over a darker scene + bright sky."""
    frame = gameplay_frame(seed=3)
    frame[0:70, :] = (200, 205, 210)  # bright sky band
    for x in range(20, 620, 62):
        cv2.rectangle(frame, (x, 119), (x + 50, 156), (178, 178, 178), -1)
    return frame


def events_of(result):
    return result.extra_data.get("events", [])


def types_of(result):
    return [e["type"] for e in events_of(result)]


@pytest.mark.unit
@pytest.mark.vision
class TestSmokeWorld:
    @pytest.mark.asyncio
    async def test_smoke_needs_temporal_confirmation_then_fires_once(self):
        clock = FakeClock()
        det = UtilityDetector(clock=clock)
        frame = add_smoke(gameplay_frame())

        r1 = await det.detect(frame)
        assert "smoke" not in types_of(r1)  # first sighting = candidate only

        clock.advance()
        r2 = await det.detect(frame)
        smoke = [e for e in events_of(r2) if e["type"] == "smoke"]
        assert len(smoke) == 1
        assert smoke[0]["source"] == "world_detector"
        assert 0.5 <= smoke[0]["confidence"] <= 0.85

        clock.advance()
        r3 = await det.detect(frame)  # same episode: no spam
        assert "smoke" not in types_of(r3)

    @pytest.mark.asyncio
    async def test_hud_row_and_sky_never_become_smoke(self):
        clock = FakeClock()
        det = UtilityDetector(clock=clock)
        frame = hud_like_frame()
        for _ in range(4):
            result = await det.detect(frame)
            assert events_of(result) == []
            clock.advance()

    @pytest.mark.asyncio
    async def test_small_gray_blob_is_not_smoke(self):
        clock = FakeClock()
        det = UtilityDetector(clock=clock)
        frame = add_smoke(gameplay_frame(), radius=18)  # far below min area
        for _ in range(3):
            result = await det.detect(frame)
            assert "smoke" not in types_of(result)
            clock.advance()

    @pytest.mark.asyncio
    async def test_plain_gameplay_frame_no_events(self):
        det = UtilityDetector(clock=FakeClock())
        result = await det.detect(gameplay_frame())
        assert events_of(result) == []


@pytest.mark.unit
@pytest.mark.vision
class TestMolotovWorld:
    @pytest.mark.asyncio
    async def test_flickering_fire_confirms_as_molotov(self):
        clock = FakeClock()
        det = UtilityDetector(clock=clock)
        base = gameplay_frame()
        saw_molotov = False
        for radius in (30, 48, 27, 50, 33):
            result = await det.detect(add_fire(base, radius=radius))
            if "molotov" in types_of(result):
                ev = [e for e in events_of(result) if e["type"] == "molotov"][0]
                assert ev["source"] == "world_detector"
                assert ev["confidence"] >= 0.6
                saw_molotov = True
                break
            clock.advance()
        assert saw_molotov, "flickering persistent fire must confirm"

    @pytest.mark.asyncio
    async def test_muzzle_flash_single_small_frame_no_event(self):
        clock = FakeClock()
        det = UtilityDetector(clock=clock)
        base = gameplay_frame()
        result = await det.detect(add_fire(base, radius=9))  # tiny, 1 frame
        assert events_of(result) == []
        result = await det.detect(base)
        assert events_of(result) == []

    @pytest.mark.asyncio
    async def test_static_orange_region_no_flicker_no_event(self):
        clock = FakeClock()
        det = UtilityDetector(clock=clock)
        frame = add_fire(gameplay_frame(), radius=26)  # ~2100px, static
        for _ in range(5):
            result = await det.detect(frame)
            assert "molotov" not in types_of(result)
            clock.advance()

    @pytest.mark.asyncio
    async def test_orange_vehicle_no_core_never_confirms(self):
        # Regression: an orange car confirmed as molotov on real
        # footage (mask area flickered under camera pan, core 0.08%).
        clock = FakeClock()
        det = UtilityDetector(clock=clock)
        base = gameplay_frame()
        for radius in (30, 48, 27, 50, 33):
            result = await det.detect(add_orange_car(base, radius=radius))
            assert "molotov" not in types_of(result)
            clock.advance()

    @pytest.mark.asyncio
    async def test_desaturated_broadcast_frame_no_smoke(self):
        # Regression: a B&W player-cam segment (median saturation 0)
        # confirmed as smoke on real footage.
        clock = FakeClock()
        det = UtilityDetector(clock=clock)
        gray_scene = np.full((H, W, 3), 60, dtype=np.uint8)
        frame = add_smoke(gray_scene, value=200)
        # add_smoke blurs into a gray frame: saturation stays ~0.
        for _ in range(3):
            result = await det.detect(frame)
            assert events_of(result) == []
            clock.advance()

    @pytest.mark.asyncio
    async def test_thin_ui_strip_is_not_fire(self):
        # The measured real-footage confuser: a 52x12 orange strip.
        clock = FakeClock()
        det = UtilityDetector(clock=clock)
        frame = gameplay_frame()
        cv2.rectangle(frame, (458, 150), (510, 162), (0, 140, 255), -1)
        assert det._detect_fire(frame) == []


@pytest.mark.unit
@pytest.mark.vision
class TestGatedDetectors:
    @pytest.mark.asyncio
    async def test_grenade_explosion_gated_off_by_default(self):
        det = UtilityDetector(clock=FakeClock())
        assert det.enable_explosion_detection is False
        frame = gameplay_frame()
        cv2.circle(frame, (320, 250), 45, (0, 200, 255), -1)
        result = await det.detect(frame)
        assert "grenade" not in types_of(result)

    @pytest.mark.asyncio
    async def test_stun_gated_off_by_default(self):
        det = UtilityDetector(clock=FakeClock())
        assert det.enable_stun_detection is False
        scene = gameplay_frame()
        white = np.full((H, W, 3), 255, dtype=np.uint8)
        await det.detect(scene)
        await det.detect(white)
        result = await det.detect(scene)
        assert "stun" not in types_of(result)

    @pytest.mark.asyncio
    async def test_stun_fires_when_enabled_and_flash_is_in_scene(self):
        clock = FakeClock()
        det = UtilityDetector(enable_stun_detection=True, clock=clock)
        scene = gameplay_frame()
        white = np.full((H, W, 3), 255, dtype=np.uint8)
        await det.detect(scene)
        clock.advance(0.1)
        await det.detect(white)          # spike onset
        clock.advance(0.1)
        result = await det.detect(scene)  # decay, same scene -> stun
        assert "stun" in types_of(result)

    @pytest.mark.asyncio
    async def test_stun_does_not_fire_across_hard_cut(self):
        clock = FakeClock()
        det = UtilityDetector(enable_stun_detection=True, clock=clock)
        scene_a = gameplay_frame(seed=7)
        # A very different scene (bright desert tones, different hist).
        scene_b = np.full((H, W, 3), (90, 170, 200), dtype=np.uint8)
        white = np.full((H, W, 3), 255, dtype=np.uint8)
        await det.detect(scene_a)
        clock.advance(0.1)
        await det.detect(white)
        clock.advance(0.1)
        result = await det.detect(scene_b)  # transition, not a flash
        assert "stun" not in types_of(result)


@pytest.mark.unit
@pytest.mark.vision
class TestStatsWiringContract:
    def test_stun_is_a_known_utility_type_for_the_tracker(self):
        # vision_intelligence._on_utility maps utility_type ->
        # UtilityType(...); "stun" must resolve or the event is
        # dropped from utility stats.
        from app.agents.team_agents.utility_tracker import UtilityType

        assert UtilityType("stun") is UtilityType.STUN
        for t in ("smoke", "molotov", "grenade"):
            assert UtilityType(t).value == t

    def test_detector_event_payload_shape(self):
        # The runtime builds NormalizedEvent payloads from these dicts;
        # the contract keys must be present on confirmed events.
        det = UtilityDetector(clock=FakeClock())
        cand = det._detect_smoke(add_smoke(gameplay_frame()))
        assert cand and cand[0].utility_type == "smoke"
        assert cand[0].confidence > 0
