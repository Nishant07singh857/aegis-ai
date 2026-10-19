# backend/tests/unit/test_vision_integration.py
"""
Phase 14 W4 — integration tests: detectors -> NormalizedEvent ->
VisionFeedAdapter -> (stub) event bus, plus the VisionCaptureService
runtime loop.

Covers:
  * vision adapter registration in FeedService (GET /feed/status shows it)
  * ingest() publishes kill/knock/alive_count/team_status/zone_update to
    the bus with the right bus event types
  * runtime service: synthetic frames -> pipeline -> events (alive count
    change, team status change, zone timer reset edge)
  * NO-FAKE AUDIT: solid-color / noise / blank frames through the FULL
    pipeline -> zero events, zero crashes
  * no-feed honesty: provider yields None -> loop idles, emits nothing
  * operator lifecycle: start/stop idempotent; grayscale forced off
    (detectors need color)

No tesseract / adb device / network needed: the TemplateDigitEngine and
color-threshold readers run fully offline. Kill-feed OCR is exercised only
for its honest no-OCR degradation (returns [] when tesseract is missing).
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import cv2
import numpy as np
import pytest

from app.services.feed_adapter.base import BaseFeedAdapter
from app.services.feed_adapter.schemas import (
    BUS_EVENT_TYPE_MAP,
    FeedEventType,
    NormalizedEvent,
)
from app.services.feed_adapter.service import FeedService
from app.services.feed_adapter.vision_adapter import VisionFeedAdapter
from app.vision.capture.config import CaptureConfig
from app.vision.capture.frame_grabber import (
    CapturedFrame,
    FrameSource,
)
from app.vision.capture.pipeline import FramePipeline
from app.vision.detectors.hud import (
    AliveCountReader,
    ReadStatus,
    TeamStatusReader,
    TemplateDigitEngine,
    TesseractEngine,
    ZoneTimerReader,
)
from app.vision.detectors.killfeed import KillFeedDetector
from app.vision.runtime import VisionCaptureService

W, H = 1920, 1080


# ---------------------------------------------------------------------------
# Synthetic frame helpers (same render style as test_hud_readers.py)
# ---------------------------------------------------------------------------

def _draw_centered(frame, region, text, scale=2.0, thickness=5):
    x1, y1 = int(region.x * W), int(region.y * H)
    x2, y2 = int((region.x + region.w) * W), int((region.y + region.h) * H)
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    org = (x1 + (x2 - x1 - tw) // 2, y1 + (y2 - y1 + th) // 2)
    cv2.putText(frame, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale,
                (255, 255, 255), thickness, cv2.LINE_AA)
    return frame


def frame_with_alive(text):
    return _draw_centered(np.zeros((H, W, 3), np.uint8),
                          AliveCountReader.DEFAULT_REGION, text)


def frame_with_zone(text):
    return _draw_centered(np.zeros((H, W, 3), np.uint8),
                          ZoneTimerReader.DEFAULT_REGION, text,
                          scale=1.6, thickness=4)


def frame_with_team(slot_kinds):
    f = np.zeros((H, W, 3), np.uint8)
    r = TeamStatusReader.DEFAULT_REGION
    x1, y1 = int(r.x * W), int(r.y * H)
    x2, y2 = int((r.x + r.w) * W), int((r.y + r.h) * H)
    sw = (x2 - x1) / 4
    for i, kind in enumerate(slot_kinds):
        cx1, cx2 = int(x1 + i * sw) + 3, int(x1 + (i + 1) * sw) - 3
        if kind == "empty":
            continue
        if kind == "dead":
            cv2.rectangle(f, (cx1, y1 + 4), (cx2, y2 - 4), (95, 95, 95), -1)
            continue
        cv2.rectangle(f, (cx1, y1 + 4), (cx2, y2 - 4), (42, 42, 55), -1)
        cv2.rectangle(f, (cx1 + 8, y1 + 12), (cx2 - 8, y2 - 60), (70, 70, 90), -1)
        strip_bottom = y2 - 10
        if kind == "alive":
            cv2.rectangle(f, (cx1 + 6, strip_bottom - 12), (cx2 - 6, strip_bottom),
                          (60, 205, 60), -1)
        elif kind == "knocked":
            sy = int(y2 - (y2 - y1) * 0.30)
            bh = int((strip_bottom - sy) * 0.45)
            cv2.rectangle(f, (cx1 + 6, strip_bottom - bh), (cx2 - 6, strip_bottom),
                          (30, 70, 235), -1)
    return f


def captured(image, frame_id=0):
    return CapturedFrame(frame_id=frame_id, source_id="test",
                         source_type=FrameSource.ADB_DEVICE, image=image)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class StubBus:
    """Minimal event-bus double: records publish() calls."""

    def __init__(self):
        self.calls = []

    async def publish(self, event_type, data, priority=None, match_id=None,
                      source=None, **kwargs):
        self.calls.append({
            "event_type": event_type, "data": data, "priority": priority,
            "match_id": match_id, "source": source,
        })
        return f"stub-{len(self.calls)}"


class StubIngest:
    def __init__(self):
        self.events = []

    async def __call__(self, event):
        self.events.append(event)
        return f"stub-{len(self.events)}"


def _no_grabber_config(**kw):
    kw.setdefault("backend", "none")
    kw.setdefault("target_fps", 8.0)
    return CaptureConfig(**kw)


def _template_service(ingest, frame_provider=None, **kw):
    """VisionCaptureService with deterministic engines.

    Pins the template-digit OCR engine for the alive/zone readers and
    forces the killfeed detector onto its honest no-OCR path, so tests
    are deterministic and fast regardless of whether the tesseract
    binary is installed. The REAL tesseract paths are audited separately
    (W2 script; pipeline-level no-fake tests).
    """
    killfeed = KillFeedDetector()
    killfeed._tesseract_ok = False  # honest "OCR unavailable" degradation
    return VisionCaptureService(
        ingest=ingest,
        config=kw.pop("config", _no_grabber_config()),
        frame_provider=frame_provider,
        match_id=kw.pop("match_id", None),
        killfeed=killfeed,
        alive=AliveCountReader(ocr=TemplateDigitEngine()),
        zone=ZoneTimerReader(ocr=TemplateDigitEngine()),
        **kw,
    )


# ---------------------------------------------------------------------------
# Adapter registration / ingest wiring
# ---------------------------------------------------------------------------

@pytest.mark.vision
class TestVisionAdapter:
    def test_registered_in_feed_service(self):
        svc = FeedService()
        bus = StubBus()
        svc.register(VisionFeedAdapter(bus))
        assert svc.get_adapter("vision") is not None
        assert "vision" in svc.status()["registered_adapters"]

    def test_adapter_id_and_contract(self):
        adapter = VisionFeedAdapter(StubBus())
        assert adapter.adapter_id == "vision"
        assert isinstance(adapter, BaseFeedAdapter)
        st = adapter.status()
        assert st["connected"] is False
        assert st["operator_gated"] is True
        assert st["vision"]["running"] is False

    @pytest.mark.asyncio
    async def test_ingest_kill_reaches_bus(self):
        bus = StubBus()
        adapter = VisionFeedAdapter(bus)
        event = NormalizedEvent(event_type=FeedEventType.KILL,
                                player="A", victim="B", match_id="m1")
        event_id = await adapter.ingest(event)
        assert event_id == "stub-1"
        call = bus.calls[0]
        assert call["event_type"] == "kill_event"
        assert call["data"]["killer"] == "A"
        assert call["data"]["victim"] == "B"
        assert call["source"] == "feed_adapter:vision"

    @pytest.mark.asyncio
    async def test_ingest_new_hud_event_types(self):
        bus = StubBus()
        adapter = VisionFeedAdapter(bus)
        for fetype, bus_type in (
            (FeedEventType.ALIVE_COUNT, "alive_count_update"),
            (FeedEventType.TEAM_STATUS, "team_status_update"),
            (FeedEventType.ZONE_UPDATE, "zone_event"),
        ):
            await adapter.ingest(NormalizedEvent(event_type=fetype))
        got = [c["event_type"] for c in bus.calls]
        assert got == ["alive_count_update", "team_status_update", "zone_event"]

    @pytest.mark.asyncio
    async def test_ingest_without_bus_raises(self):
        adapter = VisionFeedAdapter(None)
        with pytest.raises(RuntimeError):
            await adapter.ingest(NormalizedEvent(event_type=FeedEventType.KILL))

    def test_bus_event_type_map_covers_new_types(self):
        assert BUS_EVENT_TYPE_MAP[FeedEventType.ALIVE_COUNT] == "alive_count_update"
        assert BUS_EVENT_TYPE_MAP[FeedEventType.TEAM_STATUS] == "team_status_update"


# ---------------------------------------------------------------------------
# Runtime service: synthetic frames -> events
# ---------------------------------------------------------------------------

@pytest.mark.vision
class TestVisionCaptureService:
    @pytest.mark.asyncio
    async def test_alive_count_change_emits_one_event(self):
        stub = StubIngest()
        svc = _template_service(
            stub,
            frame_provider=lambda: captured(frame_with_alive("87")),
        )
        # confirm_frames=2: two identical frames -> exactly one alive event.
        # (The team reader may also emit its one-time all-unknown baseline;
        # that is allowed and asserted in the no-fake audit.)
        for i in range(4):
            result = await svc._pipeline.process_frame(
                captured(frame_with_alive("87"), frame_id=i))
            for ev in svc._extract_events(result):
                await stub(ev)
        alive = [e for e in stub.events
                 if e.event_type == FeedEventType.ALIVE_COUNT]
        assert len(alive) == 1, [(e.event_type, e.payload) for e in stub.events]
        assert alive[0].payload["players_alive"] == 87
        assert all(e.event_type not in (FeedEventType.KILL, FeedEventType.KNOCK,
                                       FeedEventType.ZONE_UPDATE)
                   for e in stub.events)

    @pytest.mark.asyncio
    async def test_team_status_change_emits_event(self):
        stub = StubIngest()
        svc = VisionCaptureService(
            ingest=stub, config=_no_grabber_config(),
            frame_provider=lambda: captured(
                frame_with_team(["alive", "alive", "knocked", "dead"])),
        )
        result = await svc._pipeline.process_frame(
            captured(frame_with_team(["alive", "alive", "knocked", "dead"])))
        events = svc._extract_events(result)
        team_events = [e for e in events
                       if e.event_type == FeedEventType.TEAM_STATUS]
        assert len(team_events) == 1
        assert team_events[0].payload["slots"] == [
            "alive", "alive", "knocked", "dead"]

    @pytest.mark.asyncio
    async def test_zone_timer_reset_edge_emits_event(self):
        stub = StubIngest()
        svc = _template_service(stub)
        frames = ([captured(frame_with_zone("0:30"), frame_id=i)
                   for i in range(3)]
                  + [captured(frame_with_zone("2:00"), frame_id=3)])
        for frame in frames:
            result = await svc._pipeline.process_frame(frame)
            for ev in svc._extract_events(result):
                await stub(ev)
        zone_events = [e for e in stub.events
                       if e.event_type == FeedEventType.ZONE_UPDATE]
        assert len(zone_events) == 1
        assert zone_events[0].payload["phase_source"] == "inferred_from_timer_reset"
        assert zone_events[0].payload["zone_phase"] == 2

    @pytest.mark.asyncio
    async def test_match_id_attached(self):
        stub = StubIngest()
        svc = VisionCaptureService(
            ingest=stub, config=_no_grabber_config(), match_id="match-42",
            frame_provider=lambda: captured(
                frame_with_team(["alive", "alive", "knocked", "dead"])),
        )
        result = await svc._pipeline.process_frame(
            captured(frame_with_team(["alive", "alive", "knocked", "dead"])))
        events = svc._extract_events(result)
        assert events and all(e.match_id == "match-42" for e in events)

    @pytest.mark.asyncio
    async def test_killfeed_degrades_honestly_without_tesseract(self):
        # In this environment the tesseract binary may be absent: the
        # detector must return [] (never fake events), and the pipeline
        # must mark the outcome without failing the frame.
        det = KillFeedDetector()
        if det.ocr_available():
            pytest.skip("tesseract present; degradation path not exercised")
        assert det.parse_frame(frame_with_alive("87")) == []
        svc = VisionCaptureService(ingest=StubIngest(),
                                   config=_no_grabber_config())
        result = await svc._pipeline.process_frame(captured(frame_with_alive("87")))
        oc = result.get("killfeed")
        assert oc is not None and oc.status == "success"
        events = svc._extract_events(result)
        # No kill/knock events may come out of a frame with no kill feed.
        assert all(e.event_type not in (FeedEventType.KILL, FeedEventType.KNOCK)
                   for e in events)

    def test_grayscale_forced_off(self):
        svc = VisionCaptureService(
            ingest=StubIngest(),
            config=CaptureConfig(backend="none", grayscale=True))
        assert svc.config.grayscale is False

    @pytest.mark.asyncio
    async def test_tesseract_engine_reads_synthetic_hud(self):
        """Regression: TesseractEngine._variants used to blur AFTER the
        tight crop, collapsing Tesseract's confidence (\"87\" -> 0.02 and
        dropped by the min_confidence gate). With the blur removed the
        production tesseract path reads clean HUD text."""
        if not TesseractEngine().available:
            pytest.skip("tesseract binary not installed")
        reader = AliveCountReader()  # default engine selection
        assert reader.ocr.name == "tesseract"
        read = reader.read(frame_with_alive("87"))
        assert read.status == ReadStatus.OK, read.detail
        assert read.value == 87
        zreader = ZoneTimerReader()
        zread = zreader.read(frame_with_zone("1:23"))
        assert zread.status == ReadStatus.OK, zread.detail
        assert zread.value == 83

    @pytest.mark.asyncio
    async def test_lifecycle_start_stop(self):
        svc = VisionCaptureService(
            ingest=StubIngest(), config=_no_grabber_config(),
            frame_provider=lambda: None)
        assert svc.running is False
        await svc.start()
        assert svc.running is True
        await asyncio.sleep(0.6)
        st = svc.status()
        assert st["running"] is True
        assert st["frames_no_feed"] > 0
        assert st["events_emitted"] == 0
        await svc.start()  # idempotent
        await svc.stop()
        assert svc.running is False
        await svc.stop()  # idempotent

    @pytest.mark.asyncio
    async def test_full_loop_emits_no_fake_events_on_noise(self):
        """NO-FAKE AUDIT (service level): noise frames through the running
        loop -> zero ingested events after baselines are established."""
        rng = np.random.default_rng(7)
        frames = [rng.integers(0, 255, (540, 960, 3), dtype=np.uint8)
                  for _ in range(6)]
        it = iter(frames)
        stub = StubIngest()
        svc = _template_service(
            stub,
            config=CaptureConfig(backend="none", target_fps=30.0),
            frame_provider=lambda: captured(next(it, frames[-1])),
        )
        # Primer through the real pipeline: establish detector baselines
        # (the team reader's one-time all-unknown baseline is allowed).
        primer = await svc._pipeline.process_frame(captured(frames[0]))
        for ev in svc._extract_events(primer):
            if ev.event_type == FeedEventType.TEAM_STATUS:
                assert all(s == "unknown" for s in ev.payload["slots"])
            else:
                raise AssertionError(f"unexpected primer event: {ev.event_type}")
        await svc.start()
        # Wait for several frames to be processed (bounded wait -- the
        # loop must actually run, not just idle).
        for _ in range(150):
            if svc.status()["frames_processed"] >= 5:
                break
            await asyncio.sleep(0.2)
        await svc.stop()
        assert svc.status()["frames_processed"] >= 5
        assert stub.events == [], \
            [(e.event_type, e.payload) for e in stub.events]


# ---------------------------------------------------------------------------
# NO-FAKE AUDIT (pipeline level, all four detectors)
# ---------------------------------------------------------------------------

@pytest.mark.vision
class TestNoFakeDetections:
    @pytest.fixture
    def pipeline(self):
        cfg = CaptureConfig(backend="none", grayscale=False)
        pipe = FramePipeline(config=cfg)
        pipe.register_detector(KillFeedDetector())
        pipe.register_detector(AliveCountReader())
        pipe.register_detector(ZoneTimerReader())
        pipe.register_detector(TeamStatusReader())
        yield pipe
        pipe.shutdown()

    def _assert_no_game_facts(self, result, where):
        """No invented game facts in one pipeline pass.

        Allowed: the one-time all-UNKNOWN team baseline (established W3
        contract -- honestly reports "no team bar observed", invents
        nothing). Everything else must be silent.
        """
        for name, outcome in result.outcomes.items():
            assert outcome.status in ("success", "failed", "skipped"), where
            rd = outcome.result_dict or {}
            extra = rd.get("extra", {})
            assert extra.get("events", []) == [], (where, name)
            assert (extra.get("alive", {}).get("change")) is None, (where, name)
            assert (extra.get("zone", {}).get("event")) is None, (where, name)
            change = (extra.get("team", {}).get("change"))
            if change is not None:
                assert all(s == "unknown" for s in change["slots"]), (where, name)

    @pytest.mark.asyncio
    async def test_blank_frames_emit_nothing(self, pipeline):
        # Primer: establish baselines (team may emit its all-unknown
        # baseline once -- allowed, asserts inside).
        primer = await pipeline.process_frame(captured(np.zeros((H, W, 3), np.uint8)))
        assert primer.feed_ok is True
        self._assert_no_game_facts(primer, "primer")
        for color in (0, 255, 127):
            result = await pipeline.process_frame(
                captured(np.full((H, W, 3), color, np.uint8)))
            assert result.feed_ok is True
            # After the primer, even the team baseline must be silent.
            for name, outcome in result.outcomes.items():
                rd = outcome.result_dict or {}
                extra = rd.get("extra", {})
                assert extra.get("events", []) == [], (color, name)
                assert (extra.get("alive", {}).get("change")) is None, (color, name)
                assert (extra.get("zone", {}).get("event")) is None, (color, name)
                assert (extra.get("team", {}).get("change")) is None, (color, name)

    @pytest.mark.asyncio
    async def test_noise_frames_emit_nothing(self, pipeline):
        primer = await pipeline.process_frame(captured(np.zeros((H, W, 3), np.uint8)))
        self._assert_no_game_facts(primer, "primer")
        rng = np.random.default_rng(1234)
        for i in range(3):
            result = await pipeline.process_frame(
                captured(rng.integers(0, 255, (H, W, 3), dtype=np.uint8)))
            for name, outcome in result.outcomes.items():
                rd = outcome.result_dict or {}
                extra = rd.get("extra", {})
                assert extra.get("events", []) == [], (i, name)
                assert (extra.get("alive", {}).get("change")) is None, (i, name)
                assert (extra.get("zone", {}).get("event")) is None, (i, name)
                assert (extra.get("team", {}).get("change")) is None, (i, name)

    @pytest.mark.asyncio
    async def test_no_feed_frame_skips_detectors(self, pipeline):
        result = await pipeline.process_frame(None)
        assert result.feed_ok is False
        for outcome in result.outcomes.values():
            assert outcome.status == "skipped"

    def test_concurrent_first_schema_import_no_deadlock(self):
        """Regression: the detectors import feed_adapter.schemas lazily;
        two worker threads racing that first import used to deadlock
        CPython's module import lock ("deadlock detected by _ModuleLock").
        Runs in a fresh interpreter WITHOUT pre-importing schemas so the
        lazy import really races inside detector threads.
        """
        import subprocess
        lines = [
            "import sys, asyncio",
            "sys.path.insert(0, '.')",
            "import numpy as np",
            "from app.vision.capture.pipeline import FramePipeline",
            "from app.vision.capture.config import CaptureConfig",
            "from app.vision.capture.frame_grabber import CapturedFrame, FrameSource",
            "from app.vision.detectors.killfeed import KillFeedDetector",
            "from app.vision.detectors.hud import ZoneTimerReader",
            "async def main():",
            "    pipe = FramePipeline(config=CaptureConfig(backend='none', grayscale=False))",
            "    pipe.register_detector(KillFeedDetector())",
            "    pipe.register_detector(ZoneTimerReader())",
            "    f = np.zeros((540, 960, 3), np.uint8)",
            "    fr = CapturedFrame(frame_id=0, source_id='t',",
            "                     source_type=FrameSource.ADB_DEVICE, image=f)",
            "    rs = await asyncio.gather(*[pipe.process_frame(fr) for _ in range(4)])",
            "    bad = [o.error for r in rs for o in r.outcomes.values()",
            "           if o.status == 'failed' and 'deadlock' in o.error]",
            "    assert not bad, bad",
            "    print('no-deadlock-ok')",
            "asyncio.run(main())",
        ]
        proc = subprocess.run(
            [sys.executable, "-c", "\n".join(lines)],
            cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
            capture_output=True, text=True, timeout=120,
        )
        assert "no-deadlock-ok" in proc.stdout, proc.stderr[-2000:]

    @pytest.mark.asyncio
    async def test_detector_failure_isolated(self, pipeline):
        class Boom:
            name = "boom"
            async def detect(self, frame, **kwargs):
                raise RuntimeError("boom")
        pipeline.register_detector(Boom())
        result = await pipeline.process_frame(
            captured(np.zeros((H, W, 3), np.uint8)))
        assert result.get("boom").status == "failed"
        # every other detector still ran
        for name in ("killfeed", "alive_count_reader",
                     "zone_timer_reader", "team_status_reader"):
            assert result.get(name).status in ("success", "failed"), name
