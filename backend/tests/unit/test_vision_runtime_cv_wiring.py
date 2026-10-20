# backend/tests/unit/test_vision_runtime_cv_wiring.py
"""
Phase 21 W1 — CV wiring tests: the Gen-1 detectors registered in the
live vision runtime (vision/runtime.py).

Covers:
  * all six wired detectors are registered by default, with the
    frame-skip intervals from CaptureConfig applied by the pipeline
  * pipeline interval gating: a gated detector is invoked only every
    Nth frame and reports "skipped" outcomes in between
  * per-detector routing: stub detector output -> the runtime invokes
    the detector and emits the right NormalizedEvent type (change /
    edge / confirmation semantics, no first-sighting spam)
  * REAL detectors end-to-end: a synthetic HP bar losing width emits
    hp_update; a synthetic minimap zone circle emits zone_update with
    phase_source="minimap_geometry"
  * the new FeedEventTypes are covered by BUS_EVENT_TYPE_MAP and reach
    the bus through VisionFeedAdapter.ingest with the right bus names
  * NO-FAKE AUDIT: blank + noise frames through the full default
    runtime (real CV detectors) emit none of the new event types
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import cv2
import numpy as np
import pytest

from app.services.feed_adapter.schemas import (
    BUS_EVENT_TYPE_MAP,
    FeedEventType,
    NormalizedEvent,
)
from app.services.feed_adapter.vision_adapter import VisionFeedAdapter
from app.vision.capture.config import CaptureConfig, load_capture_config
from app.vision.capture.frame_grabber import CapturedFrame, FrameSource
from app.vision.capture.pipeline import FramePipeline
from app.vision.detectors.base_detector import (
    DetectionResult,
    DetectionStatus,
)
from app.vision.detectors.hud import (
    AliveCountReader,
    TemplateDigitEngine,
    ZoneTimerReader,
)
from app.vision.detectors.killfeed import KillFeedDetector
from app.vision.runtime import VisionCaptureService

W, H = 960, 540


def captured(image, frame_id=0):
    return CapturedFrame(frame_id=frame_id, source_id="test",
                         source_type=FrameSource.ADB_DEVICE, image=image)


def black():
    return np.zeros((H, W, 3), np.uint8)


class StubIngest:
    def __init__(self):
        self.events = []

    async def __call__(self, event):
        self.events.append(event)
        return f"stub-{len(self.events)}"


class StubBus:
    def __init__(self):
        self.calls = []

    async def publish(self, event_type, data, priority=None, match_id=None,
                      source=None, **kwargs):
        self.calls.append({"event_type": event_type, "data": data})
        return f"stub-{len(self.calls)}"


class StubDetector:
    """BaseDetector-shaped double: counts calls, returns canned extra."""

    def __init__(self, name, extra=None, status=DetectionStatus.SUCCESS):
        self.name = name
        self.extra = extra or {}
        self.status = status
        self.calls = 0

    async def detect(self, frame, **kwargs):
        self.calls += 1
        return DetectionResult(
            detector_name=self.name,
            status=self.status,
            extra_data=self.extra,
        )


def _no_grabber_config(**kw):
    kw.setdefault("backend", "none")
    kw.setdefault("target_fps", 8.0)
    # Tests drive frames explicitly; disable frame-skip gating unless a
    # test opts into it, so every pass invokes every stub.
    kw.setdefault("detector_intervals", {})
    return CaptureConfig(**kw)


def _stub_service(ingest, **detectors):
    """VisionCaptureService with ALL detectors stubbed (silent extras).

    Any detector passed by kwarg (hp=..., loot=..., ...) replaces the
    silent stub of the same role. The four Phase 14 roles use silent
    stubs named exactly like the real detectors.
    """
    stubs = {
        "killfeed": StubDetector("killfeed"),
        "alive": StubDetector("alive_count_reader"),
        "zone": StubDetector("zone_timer_reader"),
        "team": StubDetector("team_status_reader"),
        "hp": StubDetector("hp_bar_detector"),
        "utility": StubDetector("utility_detector"),
        "minimap": StubDetector("minimap_reader"),
        "zone_geom": StubDetector("zone_detector"),
        "healing": StubDetector("healing_detector"),
        "loot": StubDetector("loot_detector"),
    }
    stubs.update(detectors)
    return VisionCaptureService(
        ingest=ingest, config=_no_grabber_config(), **stubs
    )


async def _run(svc, image, frame_id=0):
    result = await svc._pipeline.process_frame(captured(image, frame_id))
    return svc._extract_events(result)


# ---------------------------------------------------------------------------
# Registration + interval gating
# ---------------------------------------------------------------------------

@pytest.mark.vision
class TestRegistrationAndGating:
    def test_all_detectors_registered_by_default(self):
        svc = VisionCaptureService(ingest=StubIngest(),
                                   config=_no_grabber_config())
        names = svc.status()["detectors"]
        for expected in (
            "killfeed", "alive_count_reader", "zone_timer_reader",
            "team_status_reader", "hp_bar_detector", "utility_detector",
            "minimap_reader", "zone_detector", "healing_detector",
            "loot_detector",
        ):
            assert expected in names, names

    def test_default_intervals_applied(self):
        svc = VisionCaptureService(ingest=StubIngest(),
                                   config=CaptureConfig(backend="none"))
        intervals = svc.status()["pipeline"]["detector_intervals"]
        assert intervals["killfeed"] == 1
        assert intervals["hp_bar_detector"] == 2
        assert intervals["utility_detector"] == 3
        assert intervals["healing_detector"] == 3
        assert intervals["minimap_reader"] == 5
        assert intervals["zone_detector"] == 5
        assert intervals["loot_detector"] == 5

    def test_env_interval_override(self, monkeypatch):
        monkeypatch.setenv("AEGIS_DETECTOR_INTERVAL_minimap_reader", "9")
        cfg = load_capture_config()
        assert cfg.detector_interval("minimap_reader") == 9
        # Untouched detectors keep their built-in default.
        assert cfg.detector_interval("hp_bar_detector") == 2
        assert cfg.detector_interval("killfeed") == 1

    @pytest.mark.asyncio
    async def test_pipeline_gates_by_interval(self):
        cfg = CaptureConfig(backend="none", grayscale=False)
        pipe = FramePipeline(config=cfg)
        gated = StubDetector("minimap_reader")   # default interval 5
        every = StubDetector("killfeed")          # interval 1
        pipe.register_detector(gated)
        pipe.register_detector(every)
        try:
            skipped = 0
            for i in range(10):
                result = await pipe.process_frame(captured(black(), i))
                if result.get("minimap_reader").status == "skipped":
                    skipped += 1
            assert every.calls == 10
            assert 0 < gated.calls < 10
            assert skipped == 10 - gated.calls
        finally:
            pipe.shutdown()


# ---------------------------------------------------------------------------
# Per-detector routing: invoked -> event emitted
# ---------------------------------------------------------------------------

@pytest.mark.vision
class TestHPWiring:
    @pytest.mark.asyncio
    async def test_hp_change_emits_hp_update(self):
        hp = StubDetector("hp_bar_detector", extra={"players": [{
            "player_id": "player_1", "player_name": "Player_1", "hp": 80,
            "hp_pct": 80.0, "armor": 100, "armor_type": "level3",
            "boost": 0, "is_knocked": False, "confidence": 0.7,
        }]})
        svc = _stub_service(StubIngest(), hp=hp)
        # First sighting = baseline, no event.
        assert await _run(svc, black(), 0) == []
        assert hp.calls == 1
        # HP drop -> exactly one hp_update with the delta.
        hp.extra = {"players": [{
            "player_id": "player_1", "player_name": "Player_1", "hp": 55,
            "hp_pct": 55.0, "armor": 100, "armor_type": "level3",
            "boost": 0, "is_knocked": False, "confidence": 0.7,
        }]}
        events = await _run(svc, black(), 1)
        assert hp.calls == 2
        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == FeedEventType.HP_UPDATE
        assert ev.payload["hp_pct"] == 55.0
        assert ev.payload["previous_hp_pct"] == 80.0
        assert ev.payload["hp_delta_pct"] == -25.0
        assert ev.payload["detector"] == "hp_bar_detector"
        # Unchanged reading -> no repeat event.
        assert await _run(svc, black(), 2) == []

    @pytest.mark.asyncio
    async def test_real_hp_detector_end_to_end(self):
        # Real HPBarDetector (not a stub): a dark bar in the HP region
        # whose width shrinks between frames = an HP drop.
        killfeed = KillFeedDetector()
        killfeed._tesseract_ok = False
        svc = VisionCaptureService(
            ingest=StubIngest(), config=_no_grabber_config(),
            killfeed=killfeed,
            alive=AliveCountReader(ocr=TemplateDigitEngine()),
            zone=ZoneTimerReader(ocr=TemplateDigitEngine()),
        )
        wide = black()
        cv2.rectangle(wide, (25, 120), (25 + 60, 132), (0, 50, 50), -1)
        narrow = black()
        cv2.rectangle(narrow, (25, 120), (25 + 40, 132), (0, 50, 50), -1)
        # Baseline only: no HP_UPDATE yet (the team reader's one-time
        # all-unknown TEAM_STATUS baseline may legitimately appear).
        first = await _run(svc, wide, 0)
        assert [e for e in first
                if e.event_type == FeedEventType.HP_UPDATE] == []
        events = await _run(svc, narrow, 1)
        hp_events = [e for e in events
                     if e.event_type == FeedEventType.HP_UPDATE]
        assert len(hp_events) == 1, [(e.event_type, e.payload) for e in events]
        assert hp_events[0].payload["hp_delta_pct"] < 0


@pytest.mark.vision
class TestUtilityWiring:
    @pytest.mark.asyncio
    async def test_grenade_emits_immediately_and_dedupes(self):
        util = StubDetector("utility_detector", extra={"events": [{
            "type": "grenade", "position": (100.0, 100.0),
            "affected_players": [], "damage": 0, "confidence": 0.7,
        }]})
        svc = _stub_service(StubIngest(), utility=util)
        events = await _run(svc, black(), 0)
        assert util.calls == 1
        assert len(events) == 1
        assert events[0].event_type == FeedEventType.UTILITY
        assert events[0].payload["utility_type"] == "grenade"
        assert events[0].payload["position"] == [100.0, 100.0]
        # Same flash re-reported next pass -> deduped.
        assert await _run(svc, black(), 1) == []

    @pytest.mark.asyncio
    async def test_smoke_needs_two_sightings(self):
        util = StubDetector("utility_detector", extra={"events": [{
            "type": "smoke", "position": (300.0, 200.0),
            "affected_players": [], "damage": 0, "confidence": 0.65,
        }]})
        svc = _stub_service(StubIngest(), utility=util)
        assert await _run(svc, black(), 0) == []       # candidate only
        events = await _run(svc, black(), 1)           # confirmed
        assert len(events) == 1
        assert events[0].payload["utility_type"] == "smoke"
        assert await _run(svc, black(), 2) == []       # deduped


@pytest.mark.vision
class TestMinimapWiring:
    @pytest.mark.asyncio
    async def test_positions_emit_on_change_only(self):
        mm = StubDetector("minimap_reader", extra={
            "minimap": {"players": 0, "vehicles": 0, "air_drops": 0,
                        "zone": {"safe": None, "safe_radius": 0.0,
                                 "blue": None, "blue_radius": 0.0}},
            "player_positions": {},
            "confidence": 0.7,
        })
        svc = _stub_service(StubIngest(), minimap=mm)
        # Empty minimap (reader still reports SUCCESS) -> no event.
        assert await _run(svc, black(), 0) == []
        mm.extra = {
            "minimap": {"players": 2, "vehicles": 0, "air_drops": 0,
                        "zone": {"safe": None, "safe_radius": 0.0,
                                 "blue": None, "blue_radius": 0.0}},
            "player_positions": {"teammate_0": (10.0, 10.0),
                                 "enemy_0": (50.0, 40.0)},
            "confidence": 0.8,
        }
        events = await _run(svc, black(), 1)
        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == FeedEventType.POSITION_UPDATE
        assert ev.payload["marker_count"] == 2
        assert ev.payload["coordinate_space"] == "minimap_region_pixels"
        # Identical layout -> no repeat.
        assert await _run(svc, black(), 2) == []
        # A marker moved -> new event.
        mm.extra = {**mm.extra, "player_positions": {
            "teammate_0": (20.0, 10.0), "enemy_0": (50.0, 40.0)}}
        events = await _run(svc, black(), 3)
        assert len(events) == 1


@pytest.mark.vision
class TestZoneGeometryWiring:
    def _zone_extra(self, phase=2, radius=1200.0):
        return {"zone": {
            "phase": phase, "safe_center": (4000.0, 3000.0),
            "safe_radius": radius, "blue_center": (3900.0, 2900.0),
            "timer": 0.0, "is_shrinking": False, "damage": 2,
            "confidence": 0.7,
        }}

    @pytest.mark.asyncio
    async def test_zone_geometry_emits_zone_update_on_change(self):
        zg = StubDetector("zone_detector", extra=self._zone_extra())
        svc = _stub_service(StubIngest(), zone_geom=zg)
        events = await _run(svc, black(), 0)
        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == FeedEventType.ZONE_UPDATE
        assert ev.payload["phase_source"] == "minimap_geometry"
        assert ev.payload["zone_phase"] == 2
        assert ev.payload["safe_radius"] == 1200.0
        assert await _run(svc, black(), 1) == []  # unchanged

    @pytest.mark.asyncio
    async def test_zone_not_found_emits_nothing(self):
        zg = StubDetector(
            "zone_detector",
            extra={"zone": {"phase": 0, "safe_center": (0, 0),
                            "safe_radius": 0, "blue_center": (0, 0),
                            "timer": 0, "is_shrinking": False,
                            "damage": 0, "confidence": 0}},
            status=DetectionStatus.NOT_FOUND,
        )
        svc = _stub_service(StubIngest(), zone_geom=zg)
        assert await _run(svc, black(), 0) == []

    @pytest.mark.asyncio
    async def test_real_zone_detector_end_to_end(self):
        killfeed = KillFeedDetector()
        killfeed._tesseract_ok = False
        svc = VisionCaptureService(
            ingest=StubIngest(), config=_no_grabber_config(),
            killfeed=killfeed,
            alive=AliveCountReader(ocr=TemplateDigitEngine()),
            zone=ZoneTimerReader(ocr=TemplateDigitEngine()),
        )
        frame = black()
        # White circle inside the minimap region (bottom-right).
        cv2.circle(frame, (800, 450), 60, (255, 255, 255), 3)
        events = await _run(svc, frame, 0)
        geom = [e for e in events
                if e.event_type == FeedEventType.ZONE_UPDATE
                and e.payload.get("phase_source") == "minimap_geometry"]
        assert len(geom) == 1, [(e.event_type, e.payload) for e in events]
        assert geom[0].payload["safe_radius"] > 0


@pytest.mark.vision
class TestHealingWiring:
    @pytest.mark.asyncio
    async def test_heal_start_then_completion(self):
        heal = StubDetector("healing_detector", extra={"events": [{
            "type": "first_aid", "progress": 40.0, "completed": False,
            "cancelled": False, "confidence": 0.7,
        }]})
        svc = _stub_service(StubIngest(), healing=heal)
        events = await _run(svc, black(), 0)
        assert len(events) == 1
        assert events[0].event_type == FeedEventType.HEALING
        assert events[0].payload["completed"] is False
        heal.extra = {"events": [{
            "type": "first_aid", "progress": 100.0, "completed": True,
            "cancelled": False, "confidence": 0.7,
        }]}
        events = await _run(svc, black(), 1)
        assert len(events) == 1
        assert events[0].payload["completed"] is True

    @pytest.mark.asyncio
    async def test_lone_completion_without_active_is_not_a_heal(self):
        heal = StubDetector("healing_detector", extra={"events": [{
            "type": "first_aid", "progress": 100.0, "completed": True,
            "cancelled": False, "confidence": 0.7,
        }]})
        svc = _stub_service(StubIngest(), healing=heal)
        assert await _run(svc, black(), 0) == []


@pytest.mark.vision
class TestLootWiring:
    @pytest.mark.asyncio
    async def test_loot_needs_temporal_confirmation(self):
        loot = StubDetector("loot_detector", extra={"objects": [{
            "type": "death_box", "position": (200.0, 300.0),
            "is_looted": False, "lifetime": 0.0, "confidence": 0.65,
        }]})
        svc = _stub_service(StubIngest(), loot=loot)
        assert await _run(svc, black(), 0) == []       # candidate only
        events = await _run(svc, black(), 1)           # confirmed
        assert len(events) == 1
        assert events[0].event_type == FeedEventType.LOOT
        assert events[0].payload["loot_type"] == "death_box"
        assert await _run(svc, black(), 2) == []       # episode once only

    @pytest.mark.asyncio
    async def test_single_frame_loot_hit_emits_nothing(self):
        loot = StubDetector("loot_detector", extra={"objects": [{
            "type": "death_box", "position": (200.0, 300.0),
            "is_looted": False, "lifetime": 0.0, "confidence": 0.65,
        }]})
        svc = _stub_service(StubIngest(), loot=loot)
        assert await _run(svc, black(), 0) == []
        loot.extra = {"objects": []}
        assert await _run(svc, black(), 1) == []


# ---------------------------------------------------------------------------
# Bus contract for the new event types
# ---------------------------------------------------------------------------

@pytest.mark.vision
class TestBusContract:
    def test_bus_map_covers_every_event_type(self):
        for fetype in FeedEventType:
            assert fetype in BUS_EVENT_TYPE_MAP, fetype

    @pytest.mark.asyncio
    async def test_new_types_reach_bus_with_bus_names(self):
        bus = StubBus()
        adapter = VisionFeedAdapter(bus)
        for fetype in (
            FeedEventType.HP_UPDATE, FeedEventType.UTILITY,
            FeedEventType.POSITION_UPDATE, FeedEventType.HEALING,
            FeedEventType.LOOT,
        ):
            await adapter.ingest(NormalizedEvent(event_type=fetype))
        assert [c["event_type"] for c in bus.calls] == [
            "hp_update", "utility_event", "position_update",
            "healing_event", "loot_event",
        ]


# ---------------------------------------------------------------------------
# NO-FAKE AUDIT: real CV detectors on blank / noise frames
# ---------------------------------------------------------------------------

@pytest.mark.vision
class TestNoFakeCVEvents:
    @pytest.mark.asyncio
    async def test_blank_and_noise_emit_no_cv_events(self):
        killfeed = KillFeedDetector()
        killfeed._tesseract_ok = False
        svc = VisionCaptureService(
            ingest=StubIngest(), config=_no_grabber_config(),
            killfeed=killfeed,
            alive=AliveCountReader(ocr=TemplateDigitEngine()),
            zone=ZoneTimerReader(ocr=TemplateDigitEngine()),
        )
        new_types = {
            FeedEventType.HP_UPDATE, FeedEventType.UTILITY,
            FeedEventType.POSITION_UPDATE, FeedEventType.HEALING,
            FeedEventType.LOOT,
        }
        rng = np.random.default_rng(99)
        frames = [black(), black(),
                  rng.integers(0, 255, (H, W, 3), dtype=np.uint8),
                  rng.integers(0, 255, (H, W, 3), dtype=np.uint8)]
        for i, frame in enumerate(frames):
            events = await _run(svc, frame, i)
            bad = [e for e in events if e.event_type in new_types]
            assert bad == [], [(e.event_type, e.payload) for e in bad]
            geom = [e for e in events
                    if e.event_type == FeedEventType.ZONE_UPDATE
                    and e.payload.get("phase_source") == "minimap_geometry"]
            assert geom == []
