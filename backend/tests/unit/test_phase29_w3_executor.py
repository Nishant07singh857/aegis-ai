"""Phase 29 (W3) — POV tap executor + calibration + kill-switch proof tests.

No real device, no real ADB: FakeADB records every tap call, the
frame provider returns scripted numpy frames (shape only — the
executor needs (width, height) and passes frames to the reader), and
the spectated reader returns scripted indices. Every number asserted
below is derived from the fractions the test itself configures
(fraction x frame size), never copied from the executor's output.

Covered (brief W3 cases):
  * happy path with exact pixel coordinates — default profile,
    resolution profile, and per-serial override (priority order)
  * verify fail -> exactly ONE retry (2 taps total) -> failed_stay,
    a third tap is NEVER sent
  * reader returns None forever -> failed_stay, verified_index None
  * late-arriving correct index inside a verify window (polling) and
    in the RETRY window -> verified
  * kill-switch global OFF -> zero taps via execute AND execute_target;
    per-slot disable -> that slot zero taps, another slot still works
  * no_device / no_frame -> zero taps
  * calibration JSON: valid file override applies; corrupt file and
    missing file -> built-in defaults, no crash
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import List, Optional

import numpy as np
import pytest

from app.services.observer_pilot.config import (
    DEFAULT_TAP_POINTS,
    ExecutorConfig,
    TapCalibration,
    TapPoint,
    load_tap_calibration,
)
from app.services.observer_pilot.executor import (
    POVTapExecutor,
    PilotControl,
    TapResult,
    device_resolver_from_multi_device_manager,
    no_device_resolver,
)
from app.services.observer_pilot.rules import POVDecision

SLOT = 3
SOURCE = "aegis-observer-slot-03"
SERIAL = "SER123"
NOW = 1_700_000_000.0


# ── fakes / scripted collaborators ───────────────────────────────


class FakeADB:
    """Records (serial, x, y) per tap; never touches a real device."""

    def __init__(self) -> None:
        self.calls: List[tuple] = []

    async def tap(self, serial: str, x: int, y: int) -> bool:
        self.calls.append((serial, x, y))
        return True


class ScriptedReader:
    """Spectated-index reader with a queue; the last value repeats
    once the queue drains (a camera that stays where it is)."""

    def __init__(self, sequence: List[Optional[int]]) -> None:
        self._sequence = list(sequence)
        self._last: Optional[int] = None
        self.calls = 0

    def __call__(self, frame) -> Optional[int]:
        self.calls += 1
        if self._sequence:
            self._last = self._sequence.pop(0)
        return self._last


class StaticProvider:
    """frame_provider_for returning one fixed frame (or None)."""

    def __init__(self, frame) -> None:
        self.frame = frame
        self.calls = 0

    def __call__(self, slot_number: int, source_id: str):
        self.calls += 1
        return self.frame


def frame(width: int = 1920, height: int = 1080) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


def fast_config(calibration: Optional[TapCalibration] = None) -> ExecutorConfig:
    """Short verify windows so polling behaviour is exercised fast."""
    return ExecutorConfig(
        verify_timeout_s=0.15,
        verify_poll_s=0.01,
        calibration=calibration or TapCalibration(),
    )


def make_executor(
    adb: Optional[FakeADB] = None,
    reader=None,
    calibration: Optional[TapCalibration] = None,
    control: Optional[PilotControl] = None,
    resolver=None,
    provider=None,
    config=None,
) -> POVTapExecutor:
    return POVTapExecutor(
        adb=adb if adb is not None else FakeADB(),
        config=config if config is not None else fast_config(calibration),
        control=control if control is not None else PilotControl(),
        device_resolver=resolver
        if resolver is not None
        else (lambda slot, src: SERIAL),
        frame_provider_for=provider
        if provider is not None
        else StaticProvider(frame()),
        spectated_reader=reader if reader is not None else ScriptedReader([2]),
    )


def decision(to_index: int = 2, slot: int = SLOT, source: str = SOURCE) -> POVDecision:
    return POVDecision(
        slot_number=slot,
        source_id=source,
        from_index=0,
        to_index=to_index,
        rule="R1_SPECTATED_DEAD",
        reason="Spectated player is DEAD; moving to next alive teammate.",
        evidence={},
        decided_at=NOW,
    )


# ── happy path: exact pixels per calibration source ──────────────


async def test_happy_path_default_profile_exact_pixels():
    adb = FakeADB()
    ex = make_executor(adb=adb, reader=ScriptedReader([2]))
    result = await ex.execute(decision(to_index=2), now=NOW)

    assert isinstance(result, TapResult)
    assert result.status == "verified"
    assert result.taps_sent == 1
    assert result.verified_index == 2
    assert result.slot_number == SLOT and result.source_id == SOURCE
    assert result.target_index == 2 and result.at == NOW
    # Default profile is the UNCALIBRATED estimate: flag must say so.
    assert result.calibrated is False
    expected = DEFAULT_TAP_POINTS[2].to_pixels(1920, 1080)
    assert adb.calls == [(SERIAL, expected[0], expected[1])]
    # Independently: fraction x frame size, truncated.
    assert expected == (int(0.18375 * 1920), int(0.885 * 1080))


async def test_happy_path_resolution_profile_exact_pixels_and_calibrated():
    calibration = TapCalibration(
        profiles={
            "default": list(DEFAULT_TAP_POINTS),
            "1280x720": [
                TapPoint(0.50, 0.25),
                TapPoint(0.60, 0.25),
                TapPoint(0.70, 0.25),
                TapPoint(0.80, 0.25),
            ],
        },
        calibrated_profiles={"1280x720"},
    )
    adb = FakeADB()
    ex = make_executor(
        adb=adb,
        reader=ScriptedReader([1]),
        calibration=calibration,
        provider=StaticProvider(frame(1280, 720)),
    )
    result = await ex.execute_target(SLOT, SOURCE, 1, "manual check", NOW)

    assert result.status == "verified" and result.taps_sent == 1
    assert result.calibrated is True
    assert adb.calls == [(SERIAL, int(0.60 * 1280), int(0.25 * 720))]


async def test_per_serial_override_beats_resolution_profile():
    calibration = TapCalibration(
        profiles={
            "1920x1080": [TapPoint(0.50, 0.50)] * 4,
        },
        per_serial={
            SERIAL: [
                TapPoint(0.10, 0.20),
                TapPoint(0.11, 0.20),
                TapPoint(0.12, 0.20),
                TapPoint(0.13, 0.20),
            ]
        },
        calibrated_profiles={SERIAL},
    )
    adb = FakeADB()
    ex = make_executor(adb=adb, reader=ScriptedReader([0]), calibration=calibration)
    result = await ex.execute_target(SLOT, SOURCE, 0, "override check", NOW)

    assert result.status == "verified"
    assert result.calibrated is True  # the SERIAL is the calibrated entry
    assert adb.calls == [(SERIAL, int(0.10 * 1920), int(0.20 * 1080))]


def test_calibration_resolve_priority_and_calibrated_flags():
    calibration = TapCalibration(
        profiles={
            "default": [TapPoint(0.01, 0.01)] * 4,
            "800x600": [TapPoint(0.02, 0.02)] * 4,
        },
        per_serial={"S9": [TapPoint(0.03, 0.03)] * 4},
        calibrated_profiles={"800x600"},
    )
    points, calibrated = calibration.resolve("S9", 800, 600)
    assert points[0].fx == 0.03 and calibrated is False  # serial not ledgered
    points, calibrated = calibration.resolve("OTHER", 800, 600)
    assert points[0].fx == 0.02 and calibrated is True
    points, calibrated = calibration.resolve("OTHER", 640, 480)
    assert points[0].fx == 0.01 and calibrated is False  # default fallback


async def test_executor_accepts_bare_tap_calibration_as_config():
    adb = FakeADB()
    ex = POVTapExecutor(
        adb=adb,
        config=TapCalibration(),
        control=PilotControl(),
        device_resolver=lambda slot, src: SERIAL,
        frame_provider_for=StaticProvider(frame()),
        spectated_reader=ScriptedReader([2]),
    )
    # Default verify timings come from ExecutorConfig defaults.
    assert ex.config.verify_timeout_s == 2.0 and ex.config.verify_poll_s == 0.4
    result = await ex.execute(decision(), now=NOW)
    assert result.status == "verified" and len(adb.calls) == 1


# ── verification: retry exactly once, then stay ──────────────────


async def test_verify_fail_retries_exactly_once_then_failed_stay():
    adb = FakeADB()
    # Camera stuck on index 0 whatever we tap.
    ex = make_executor(adb=adb, reader=ScriptedReader([0]))
    result = await ex.execute(decision(to_index=2), now=NOW)

    assert result.status == "failed_stay"
    assert result.taps_sent == 2
    assert len(adb.calls) == 2  # the third tap NEVER happens
    assert adb.calls[0] == adb.calls[1]  # same point retried
    assert result.verified_index == 0  # the index actually read


async def test_reader_none_forever_failed_stay_verified_index_none():
    adb = FakeADB()
    ex = make_executor(adb=adb, reader=ScriptedReader([None]))
    result = await ex.execute(decision(to_index=1), now=NOW)

    assert result.status == "failed_stay"
    assert result.taps_sent == 2 and len(adb.calls) == 2
    assert result.verified_index is None


async def test_polling_late_correct_index_in_first_window_verified():
    adb = FakeADB()
    # Identity unreadable twice, then the target appears — polling
    # inside the window must catch it without a retry tap.
    ex = make_executor(adb=adb, reader=ScriptedReader([None, None, 2]))
    result = await ex.execute(decision(to_index=2), now=NOW)

    assert result.status == "verified"
    assert result.taps_sent == 1 and len(adb.calls) == 1
    assert result.verified_index == 2


async def test_correct_index_arrives_in_retry_window_verified_two_taps():
    adb = FakeADB()
    reads_after_retry = {"n": 0}

    def reader(frame_) -> Optional[int]:
        if len(adb.calls) < 2:
            return 0  # first window: camera still on the wrong player
        reads_after_retry["n"] += 1
        # Retry window: one unreadable frame, then the target.
        return None if reads_after_retry["n"] == 1 else 2

    ex = make_executor(adb=adb, reader=reader)
    result = await ex.execute(decision(to_index=2), now=NOW)

    assert result.status == "verified"
    assert result.taps_sent == 2 and len(adb.calls) == 2
    assert result.verified_index == 2


async def test_captured_frame_wrapper_uses_image_shape_and_reader_gets_image():
    adb = FakeADB()
    seen_shapes = []

    def reader(image) -> Optional[int]:
        seen_shapes.append(getattr(image, "shape", None))
        return 2

    wrapped = SimpleNamespace(image=frame(1280, 720))
    calibration = TapCalibration(
        profiles={"1280x720": [TapPoint(0.5, 0.5)] * 4},
    )
    ex = make_executor(
        adb=adb,
        reader=reader,
        calibration=calibration,
        provider=StaticProvider(wrapped),
    )
    result = await ex.execute_target(SLOT, SOURCE, 2, "wrapped frame", NOW)

    assert result.status == "verified"
    assert adb.calls == [(SERIAL, 640, 360)]
    assert seen_shapes and seen_shapes[0] == (720, 1280, 3)


# ── kill-switch (the choke point) ────────────────────────────────


async def test_kill_switch_off_zero_taps_on_both_entry_points():
    adb = FakeADB()
    control = PilotControl()
    control.set_enabled(False)
    ex = make_executor(adb=adb, control=control)

    r1 = await ex.execute(decision(), now=NOW)
    r2 = await ex.execute_target(SLOT, SOURCE, 1, "manual override", NOW)

    for result in (r1, r2):
        assert result.status == "blocked_kill_switch"
        assert result.taps_sent == 0
        assert result.verified_index is None
    assert adb.calls == []


async def test_kill_switch_default_off_state_is_enabled():
    control = PilotControl(enabled=False)
    adb = FakeADB()
    ex = make_executor(adb=adb, control=control)
    result = await ex.execute(decision(), now=NOW)
    assert result.status == "blocked_kill_switch" and adb.calls == []


async def test_per_slot_disable_blocks_only_that_slot():
    adb = FakeADB()
    control = PilotControl()
    control.disable_slot(SLOT)
    reader_calls = {"target": 2}

    def reader(image) -> Optional[int]:
        return reader_calls["target"]

    ex = make_executor(adb=adb, control=control, reader=reader)

    blocked = await ex.execute(decision(), now=NOW)
    assert blocked.status == "blocked_kill_switch" and blocked.taps_sent == 0
    assert adb.calls == []

    reader_calls["target"] = 1
    other = await ex.execute_target(4, "aegis-observer-slot-04", 1, "other", NOW)
    assert other.status == "verified" and len(adb.calls) == 1

    control.enable_slot(SLOT)
    reader_calls["target"] = 2
    freed = await ex.execute(decision(), now=NOW)
    assert freed.status == "verified"


def test_pilot_control_semantics():
    control = PilotControl()
    assert control.enabled is True and control.allows(1) is True
    control.disable_slot(1)
    assert control.allows(1) is False and control.allows(2) is True
    control.enable_slot(1)
    assert control.allows(1) is True
    assert control.set_enabled(False) is False
    assert control.allows(2) is False
    assert control.snapshot() == {"enabled": False, "disabled_slots": []}


# ── no device / no frame ─────────────────────────────────────────


async def test_no_device_zero_taps():
    adb = FakeADB()
    ex = make_executor(adb=adb, resolver=lambda slot, src: None)
    result = await ex.execute(decision(), now=NOW)
    assert result.status == "no_device"
    assert result.taps_sent == 0 and adb.calls == []
    assert result.calibrated is False


async def test_no_frame_zero_taps():
    adb = FakeADB()
    ex = make_executor(adb=adb, provider=StaticProvider(None))
    result = await ex.execute(decision(), now=NOW)
    assert result.status == "no_frame"
    assert result.taps_sent == 0 and adb.calls == []


async def test_target_outside_calibration_profile_no_tap():
    adb = FakeADB()
    ex = make_executor(adb=adb, reader=ScriptedReader([9]))
    result = await ex.execute_target(SLOT, SOURCE, 9, "bad target", NOW)
    assert result.status == "failed_stay"
    assert result.taps_sent == 0 and adb.calls == []


# ── device resolver audit helpers ────────────────────────────────


def test_no_device_resolver_binds_nothing():
    assert no_device_resolver(1, SOURCE) is None
    assert no_device_resolver(16, "aegis-observer-slot-16") is None


def test_resolver_from_multi_device_manager_reads_slot_serials():
    manager = SimpleNamespace(
        _slots={
            3: SimpleNamespace(serial="SER-3"),
            4: SimpleNamespace(serial=""),  # assigned slot, device gone
            5: {"serial": "SER-5"},
        }
    )
    resolver = device_resolver_from_multi_device_manager(manager)
    assert resolver(3, SOURCE) == "SER-3"
    assert resolver(4, "aegis-observer-slot-04") is None
    assert resolver(5, "aegis-observer-slot-05") == "SER-5"
    assert resolver(9, "aegis-observer-slot-09") is None


# ── calibration JSON loading ─────────────────────────────────────


def _write_calibration(tmp_path, payload) -> str:
    path = tmp_path / "pov_calibration.json"
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_calibration_json_valid_file_override_applies(tmp_path):
    path = _write_calibration(
        tmp_path,
        {
            "profiles": {
                "1920x1080": [[0.50, 0.50], [0.60, 0.50], [0.70, 0.50], [0.80, 0.50]],
            },
            "per_serial": {"SERX": [[0.11, 0.22], [0.12, 0.22], [0.13, 0.22], [0.14, 0.22]]},
            "calibrated_profiles": ["1920x1080", "SERX"],
        },
    )
    calibration = load_tap_calibration(path)

    points, calibrated = calibration.resolve("OTHER", 1920, 1080)
    assert [ (p.fx, p.fy) for p in points ] == [
        (0.50, 0.50), (0.60, 0.50), (0.70, 0.50), (0.80, 0.50)
    ]
    assert calibrated is True

    points, calibrated = calibration.resolve("SERX", 1920, 1080)
    assert points[0] == TapPoint(0.11, 0.22) and calibrated is True

    # A resolution the file does not name still falls back to default.
    points, calibrated = calibration.resolve("OTHER", 640, 480)
    assert points == list(DEFAULT_TAP_POINTS) and calibrated is False


def test_calibration_json_corrupt_file_defaults_no_crash(tmp_path):
    path = _write_calibration(tmp_path, "{ this is not json ]")
    calibration = load_tap_calibration(path)
    points, calibrated = calibration.resolve(SERIAL, 1920, 1080)
    assert points == list(DEFAULT_TAP_POINTS)
    assert calibrated is False


def test_calibration_json_missing_file_defaults_no_crash(tmp_path):
    calibration = load_tap_calibration(str(tmp_path / "does-not-exist.json"))
    points, calibrated = calibration.resolve(SERIAL, 1920, 1080)
    assert points == list(DEFAULT_TAP_POINTS) and calibrated is False


def test_calibration_env_var_path_is_used(tmp_path, monkeypatch):
    path = _write_calibration(
        tmp_path,
        {"profiles": {"default": [[0.21, 0.31], [0.22, 0.31], [0.23, 0.31], [0.24, 0.31]]}},
    )
    monkeypatch.setenv("AEGIS_POV_CALIBRATION_PATH", path)
    calibration = load_tap_calibration()
    points, _ = calibration.resolve("ANY", 320, 240)
    assert points[0] == TapPoint(0.21, 0.31)


async def test_calibration_from_file_drives_executor_pixels(tmp_path):
    path = _write_calibration(
        tmp_path,
        {
            "profiles": {
                "1920x1080": [
                    {"fx": 0.25, "fy": 0.75},
                    {"fx": 0.30, "fy": 0.75},
                    {"fx": 0.35, "fy": 0.75},
                    {"fx": 0.40, "fy": 0.75},
                ]
            },
            "calibrated_profiles": ["1920x1080"],
        },
    )
    adb = FakeADB()
    ex = make_executor(
        adb=adb,
        reader=ScriptedReader([3]),
        calibration=load_tap_calibration(path),
    )
    result = await ex.execute_target(SLOT, SOURCE, 3, "from file", NOW)
    assert result.status == "verified" and result.calibrated is True
    assert adb.calls == [(SERIAL, int(0.40 * 1920), int(0.75 * 1080))]
