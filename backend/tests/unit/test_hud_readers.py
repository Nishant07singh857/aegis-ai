# backend/tests/unit/test_hud_readers.py
"""
Synthetic tests for the Phase 14 W3 HUD readers (app/vision/detectors/hud.py).

Renders fake HUD frames (alive count, zone timer, team bar) with OpenCV,
measures read accuracy, and asserts honest UNKNOWN behavior on
blank/solid-color frames (no crash, no false events).

Tesseract is not installed in this VM, so these tests run the
TemplateDigitEngine fallback explicitly. The TesseractEngine code path is
import-safe but its OCR accuracy cannot be measured here (reported, not
assumed).
"""

import cv2
import numpy as np
import pytest

from app.vision.detectors.hud import (
    AliveCountReader,
    HUDPipeline,
    HUDRegion,
    ReadStatus,
    TeamSlotStatus,
    TeamStatusReader,
    TemplateDigitEngine,
    ZoneTimerReader,
)

W, H = 1920, 1080


def _blank():
    return np.zeros((H, W, 3), dtype=np.uint8)


def _draw_centered_text(frame, region: HUDRegion, text: str,
                        scale: float = 2.0, thickness: int = 5):
    x1, y1 = int(region.x * W), int(region.y * H)
    x2, y2 = int((region.x + region.w) * W), int((region.y + region.h) * H)
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX,
                                  scale, thickness)
    org = (x1 + (x2 - x1 - tw) // 2, y1 + (y2 - y1 + th) // 2)
    cv2.putText(frame, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale,
                (255, 255, 255), thickness, cv2.LINE_AA)
    return frame


def frame_with_alive(text: str):
    f = _blank()
    return _draw_centered_text(f, AliveCountReader.DEFAULT_REGION, text)


def frame_with_zone(text: str):
    f = _blank()
    return _draw_centered_text(f, ZoneTimerReader.DEFAULT_REGION, text,
                               scale=1.6, thickness=4)


def frame_with_team(slot_kinds):
    """slot_kinds: list of 'alive' | 'knocked' | 'dead' | 'empty' (len 4)."""
    f = _blank()
    r = TeamStatusReader.DEFAULT_REGION
    x1, y1 = int(r.x * W), int(r.y * H)
    x2, y2 = int((r.x + r.w) * W), int((r.y + r.h) * H)
    sw = (x2 - x1) / 4
    for i, kind in enumerate(slot_kinds):
        cx1, cx2 = int(x1 + i * sw) + 3, int(x1 + (i + 1) * sw) - 3
        if kind == "empty":
            continue  # leave black -> slot not present
        if kind == "dead":
            cv2.rectangle(f, (cx1, y1 + 4), (cx2, y2 - 4), (95, 95, 95), -1)
            continue
        cv2.rectangle(f, (cx1, y1 + 4), (cx2, y2 - 4), (42, 42, 55), -1)
        # portrait block
        cv2.rectangle(f, (cx1 + 8, y1 + 12), (cx2 - 8, y2 - 60), (70, 70, 90), -1)
        # HP strip (bottom 32% of card); bars are thin realistic bands,
        # bottom-anchored like real HUD bars
        sy = int(y2 - (y2 - y1) * 0.30)
        strip_bottom = y2 - 10
        if kind == "alive":
            cv2.rectangle(f, (cx1 + 6, strip_bottom - 12), (cx2 - 6, strip_bottom),
                          (60, 205, 60), -1)  # BGR green thin HP bar
        elif kind == "knocked":
            # red-orange knock banner: a band in the lower strip
            strip_h = strip_bottom - sy
            bh = int(strip_h * 0.45)
            by1 = strip_bottom - bh
            cv2.rectangle(f, (cx1 + 6, by1), (cx2 - 6, strip_bottom),
                          (30, 70, 235), -1)  # BGR red-orange banner
    return f


def frame_full_hud():
    f = frame_with_team(["alive", "alive", "knocked", "dead"])
    _draw_centered_text(f, AliveCountReader.DEFAULT_REGION, "87")
    _draw_centered_text(f, ZoneTimerReader.DEFAULT_REGION, "1:23",
                        scale=1.6, thickness=4)
    return f


def _ocr():
    return TemplateDigitEngine()


# ---------------------------------------------------------------------------
# Alive count reader
# ---------------------------------------------------------------------------

@pytest.mark.vision
class TestAliveCountReader:
    @pytest.mark.parametrize("text,expected", [("87", 87), ("100", 100),
                                               ("5", 5), ("0", 0)])
    def test_reads_synthetic_counts(self, text, expected):
        reader = AliveCountReader(ocr=_ocr())
        read = reader.read(frame_with_alive(text))
        assert read.status == ReadStatus.OK, read.detail
        assert read.value == expected

    def test_emits_only_on_change(self):
        reader = AliveCountReader(ocr=_ocr(), confirm_frames=2)
        f = frame_with_alive("87")
        assert reader.poll(f) is None          # 1st sighting: confirming
        upd = reader.poll(f)                   # 2nd identical: emit
        assert upd is not None and upd.players == 87
        assert reader.poll(f) is None          # unchanged: silent
        f2 = frame_with_alive("86")
        assert reader.poll(f2) is None         # new value: confirming
        upd2 = reader.poll(f2)
        assert upd2 is not None and upd2.players == 86

    @pytest.mark.parametrize("text", ["999", "abc", "--"])
    def test_invalid_is_unknown(self, text):
        reader = AliveCountReader(ocr=_ocr(), max_players=100)
        read = reader.read(frame_with_alive(text))
        # "999" out of range -> unknown; "abc"/"--" unreadable -> unknown
        assert read.status == ReadStatus.UNKNOWN
        assert reader.poll(frame_with_alive(text)) is None

    def test_blank_frame_is_unknown_no_crash(self):
        reader = AliveCountReader(ocr=_ocr())
        read = reader.read(_blank())
        assert read.status == ReadStatus.UNKNOWN
        assert reader.poll(_blank()) is None

    def test_solid_frames_are_unknown(self):
        reader = AliveCountReader(ocr=_ocr())
        for color in ((255, 255, 255), (0, 0, 255), (30, 30, 30)):
            f = np.full((H, W, 3), color, dtype=np.uint8)
            assert reader.read(f).status == ReadStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Zone timer reader
# ---------------------------------------------------------------------------

@pytest.mark.vision
class TestZoneTimerReader:
    @pytest.mark.parametrize("text,expected", [("1:23", 83), ("0:45", 45),
                                               ("10:00", 600), ("0:07", 7)])
    def test_reads_synthetic_timers(self, text, expected):
        reader = ZoneTimerReader(ocr=_ocr())
        read = reader.read(frame_with_zone(text))
        assert read.status == ReadStatus.OK, read.detail
        assert read.value == expected

    def test_malformed_timer_is_unknown(self):
        reader = ZoneTimerReader(ocr=_ocr())
        assert reader.read(frame_with_zone("9:99")).status == ReadStatus.UNKNOWN

    def test_blank_frame_is_unknown_no_event(self):
        reader = ZoneTimerReader(ocr=_ocr())
        assert reader.read(_blank()).status == ReadStatus.UNKNOWN
        assert reader.poll(_blank()) is None

    def test_zone_update_event_on_phase_change(self):
        from app.vision.detectors.hud import _feed_schemas
        FeedEventType = _feed_schemas().FeedEventType
        reader = ZoneTimerReader(ocr=_ocr())
        assert reader.poll(frame_with_zone("1:23"),
                           match_id="m1") is None     # baseline -> phase 1
        assert reader.poll(frame_with_zone("0:05"),
                           match_id="m1") is None     # countdown, no event
        ev = reader.poll(frame_with_zone("2:30"), match_id="m1")
        assert ev is not None                          # reset edge -> phase 2
        assert ev.event_type == FeedEventType.ZONE_UPDATE
        assert ev.payload["zone_phase"] == 2
        assert ev.payload["timer_seconds"] == 150
        assert ev.match_id == "m1"
        # Same phase continues -> no repeat event.
        assert reader.poll(frame_with_zone("2:29"), match_id="m1") is None

    def test_no_event_on_unknown(self):
        reader = ZoneTimerReader(ocr=_ocr())
        reader.poll(frame_with_zone("1:23"))
        assert reader.poll(_blank()) is None  # unknown never triggers a phase


# ---------------------------------------------------------------------------
# Team status reader (color thresholding)
# ---------------------------------------------------------------------------

@pytest.mark.vision
class TestTeamStatusReader:
    def test_classifies_mixed_bar(self):
        reader = TeamStatusReader()
        status = reader.read(
            frame_with_team(["alive", "alive", "knocked", "dead"]))
        assert [s for s in status.slots] == [
            TeamSlotStatus.ALIVE, TeamSlotStatus.ALIVE,
            TeamSlotStatus.KNOCKED, TeamSlotStatus.DEAD,
        ], status.to_dict()

    def test_empty_slots_are_unknown(self):
        reader = TeamStatusReader()
        status = reader.read(
            frame_with_team(["alive", "empty", "empty", "empty"]))
        assert status.slots[0] == TeamSlotStatus.ALIVE
        assert all(s == TeamSlotStatus.UNKNOWN for s in status.slots[1:])

    def test_blank_frame_all_unknown_no_crash(self):
        reader = TeamStatusReader()
        status = reader.read(_blank())
        assert all(s == TeamSlotStatus.UNKNOWN for s in status.slots)
        assert reader.poll(_blank()) is not None  # first poll emits baseline
        assert reader.poll(_blank()) is None      # then silent

    def test_poll_emits_only_on_change(self):
        reader = TeamStatusReader()
        f1 = frame_with_team(["alive", "alive", "alive", "alive"])
        f2 = frame_with_team(["alive", "knocked", "alive", "alive"])
        upd1 = reader.poll(f1)
        assert upd1 is not None and upd1["alive_count"] == 4
        assert reader.poll(f1) is None
        upd2 = reader.poll(f2)
        assert upd2 is not None
        assert upd2["slots"][1] == "knocked"

    def test_solid_frames_are_unknown(self):
        reader = TeamStatusReader()
        for color in ((255, 255, 255), (0, 0, 0), (200, 30, 30)):
            status = reader.read(
                np.full((H, W, 3), color, dtype=np.uint8))
            assert all(s == TeamSlotStatus.UNKNOWN for s in status.slots), color


# ---------------------------------------------------------------------------
# Pipeline facade
# ---------------------------------------------------------------------------

@pytest.mark.vision
class TestHUDPipeline:
    def test_process_full_hud(self):
        pipe = HUDPipeline(alive=AliveCountReader(ocr=_ocr()),
                           zone=ZoneTimerReader(ocr=_ocr()),
                           team=TeamStatusReader())
        f = frame_full_hud()
        pipe.process(f, match_id="m1")          # baseline: team emits, alive confirming
        out = pipe.process(f, match_id="m1")    # alive confirmed -> emits
        kinds = [u["type"] for u in out.updates]
        assert "alive_count" in kinds
        assert out.reads["alive"] == 87
        assert out.reads["zone_timer_s"] == 83
        assert out.reads["team"] == ["alive", "alive", "knocked", "dead"]
        # Third identical frame: no change -> no updates, no events.
        out3 = pipe.process(f, match_id="m1")
        assert out3.updates == [] and out3.events == []

    def test_process_blank_frame_no_updates_no_events(self):
        pipe = HUDPipeline(alive=AliveCountReader(ocr=_ocr()),
                           zone=ZoneTimerReader(ocr=_ocr()),
                           team=TeamStatusReader())
        out = pipe.process(_blank())
        # Only the team baseline poll may emit once (state change from
        # nothing -> all-unknown); never any zone events on blank frames.
        assert out.events == []
        assert out.reads["alive"] is None
        assert out.reads["zone_timer_s"] is None
