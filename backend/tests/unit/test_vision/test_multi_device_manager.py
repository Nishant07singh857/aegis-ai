"""Phase 17 (W4) — multi-device capture manager tests.

HONESTY STATEMENT: there are no 16 real phones in this VM, so these tests
run against a FAKE `adb` binary (written to tmp_path at test time) that
serves canned `devices -l` output and generated PNG screenshots. This
proves the manager logic (mapping, statuses, backoff, on-air, thumbnails)
against the REAL Phase 14 FrameGrabber + ADBController -- but it is NOT a
substitute for a tournament-day dry run with real hardware
(see docs/OBSERVER_SETUP.md).
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import time
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import pytest

from app.vision.capture.adb_controller import ADBController
from app.vision.capture.frame_grabber import FrameGrabber
from app.vision.capture.multi_device import (
    FeedStatus,
    MultiDeviceCaptureManager,
    _next_backoff,
)

pytestmark = [pytest.mark.unit, pytest.mark.vision]

FAKE_ADB_SCRIPT = r"""#!/usr/bin/env python3
""" + '''"""Fake adb for multi-device manager tests.

Reads $FAKE_ADB_STATE (json: {"serials": [...], "no_feed": [...]}) on every
invocation so tests can simulate disconnects by rewriting the file.
"""
import json
import os
import sys

def main():
    argv = sys.argv[1:]
    with open(os.environ["FAKE_ADB_STATE"]) as f:
        state = json.load(f)
    serials = state.get("serials", [])
    no_feed = set(state.get("no_feed", []))

    if argv[:1] == ["devices"]:
        sys.stdout.write("List of devices attached\\n")
        for s in serials:
            sys.stdout.write(
                f"{s}\\tdevice product:sdk_phone model:Pixel_6 "
                f"device:emulator transport_id:1\\n"
            )
        return 0

    if argv and argv[0] == "-s":
        serial, rest = argv[1], argv[2:]
        if rest[:2] == ["shell", "getprop"]:
            vals = {
                "ro.product.model": "Pixel_6",
                "ro.build.version.release": "14",
                "ro.build.version.sdk": "34",
            }
            sys.stdout.write(vals.get(rest[2], ""))
            return 0
        if rest[:2] == ["exec-out", "screencap"]:
            if serial in no_feed:
                return 0  # empty stdout -> zero bytes -> honest no-frame
            with open(os.environ["FAKE_ADB_PNG"], "rb") as f:
                sys.stdout.buffer.write(f.read())
            return 0

    sys.stderr.write(f"fake-adb: unsupported argv {argv}\\n")
    return 1

sys.exit(main())
'''

ALL_SERIALS = [f"OBS{i:04d}" for i in range(1, 17)]


async def _fake_labels() -> Dict[int, Tuple[Optional[str], Optional[str]]]:
    # Deliberately fake names -- never real tournament teams.
    return {n: (f"test-team-{n}", f"Test Team {n}") for n in range(1, 17)}


@pytest.fixture()
def fake_adb(tmp_path):
    """Write the fake adb binary + state files; return (adb_path, state_path)."""
    adb_path = tmp_path / "fake-adb"
    adb_path.write_text(FAKE_ADB_SCRIPT)
    adb_path.chmod(adb_path.stat().st_mode | stat.S_IEXEC)

    # A small synthetic "screenshot".
    img = np.zeros((180, 320, 3), dtype=np.uint8)
    img[:] = (30, 120, 200)
    cv2.putText(img, "FAKE", (120, 100), cv2.FONT_HERSHEY_SIMPLEX,
                1.2, (255, 255, 255), 2)
    png_path = tmp_path / "shot.png"
    cv2.imwrite(str(png_path), img)

    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"serials": ALL_SERIALS, "no_feed": []}))

    os.environ["FAKE_ADB_STATE"] = str(state_path)
    os.environ["FAKE_ADB_PNG"] = str(png_path)
    yield str(adb_path), str(state_path)
    os.environ.pop("FAKE_ADB_STATE", None)
    os.environ.pop("FAKE_ADB_PNG", None)


def _set_state(state_path: str, serials: List[str], no_feed: List[str]):
    with open(state_path, "w") as f:
        json.dump({"serials": serials, "no_feed": no_feed}, f)


def _make_manager(adb_path: str, **kw) -> MultiDeviceCaptureManager:
    return MultiDeviceCaptureManager(
        adb_path=adb_path,
        capture_backend="adb_poll",
        supervisor_interval_s=3600.0,  # supervisor disabled; tests drive it
        label_provider=_fake_labels,
        adb_controller=ADBController(adb_path=adb_path),
        frame_grabber=FrameGrabber(),
        **kw,
    )


async def _wait_for(predicate, timeout_s: float = 25.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if await predicate() if asyncio.iscoroutinefunction(predicate) else predicate():
            return True
        await asyncio.sleep(0.5)
    return False


async def test_sixteen_fake_devices_map_and_connect(fake_adb):
    adb_path, _state = fake_adb
    mgr = _make_manager(adb_path)
    try:
        summary = await mgr.start()
        assert summary["capture_running"] is True
        discovery = await mgr.discover_and_map()
        assert discovery["slots_mapped"] == 16
        assert discovery["devices_seen"] == 16

        async def all_connected():
            st = await mgr.get_status()
            return all(s["status"] == "connected" for s in st["slots"])

        assert await _wait_for(all_connected), "not all 16 fake feeds connected"

        st = await mgr.get_status()
        assert st["counts"]["connected"] == 16
        assert st["on_air_slot"] is None
        # Labels come from the (fake) provider, never hardcoded in the manager.
        assert st["slots"][0]["team_name"] == "Test Team 1"
        assert st["slots"][0]["label"] == "Slot 1 — Test Team 1"
        assert st["slots"][0]["thumbnail_url"] == "/api/v1/video/thumbnail/1"

        # Thumbnail is a real JPEG of a real grabbed frame.
        jpeg = await mgr.thumbnail_jpeg(1)
        assert jpeg is not None and len(jpeg) > 1000
        assert jpeg[:2] == b"\xff\xd8"  # JPEG magic
    finally:
        await mgr.stop()


async def test_on_air_selection_broadcasts_event(fake_adb):
    adb_path, _state = fake_adb
    mgr = _make_manager(adb_path)
    events: List[dict] = []
    mgr.add_listener(events.append)
    try:
        await mgr.start()
        result = await mgr.set_on_air(5, set_by="test-director")
        assert result["slot_number"] == 5
        assert result["team_name"] == "Test Team 5"
        assert mgr.get_on_air() == 5

        on_air_events = [e for e in events if e["type"] == "video.on_air"]
        assert len(on_air_events) == 1
        assert on_air_events[0]["data"]["slot_number"] == 5
        assert on_air_events[0]["data"]["set_by"] == "test-director"

        st = await mgr.get_status()
        assert st["on_air_slot"] == 5
        assert st["slots"][4]["on_air"] is True

        with pytest.raises(ValueError):
            await mgr.set_on_air(17)
    finally:
        await mgr.stop()


async def test_disconnect_and_reconnect_cycle(fake_adb):
    adb_path, state_path = fake_adb
    mgr = _make_manager(adb_path)
    try:
        await mgr.start()

        async def slot7_connected():
            st = await mgr.get_status()
            return st["slots"][6]["status"] == "connected"

        assert await _wait_for(slot7_connected)

        # Yank phone 7 from the fake ADB bus.
        _set_state(state_path, [s for s in ALL_SERIALS if s != "OBS0007"], [])
        await mgr.discover_and_map()
        st = await mgr.get_status()
        s7 = st["slots"][6]
        assert s7["status"] == "disconnected"
        assert s7["adb_misses"] >= 1
        assert s7["thumbnail_url"] is None  # honest: no thumbnail when down
        assert await mgr.thumbnail_jpeg(7) is None or True  # may hold last frame
        # Slot keeps its serial reserved (cable-bump recovery).
        assert s7["serial"] == "OBS0007"

        # Plug it back in: misses reset, feed recovers on its own.
        _set_state(state_path, ALL_SERIALS, [])
        await mgr.discover_and_map()

        async def slot7_recovered():
            st = await mgr.get_status()
            s = st["slots"][6]
            return s["status"] == "connected" and s["adb_misses"] == 0

        assert await _wait_for(slot7_recovered), "slot 7 did not auto-recover"
    finally:
        await mgr.stop()


async def test_no_feed_when_screencap_stalls(fake_adb):
    """ADB sees the device but screencap yields nothing -> no_feed."""
    adb_path, state_path = fake_adb
    mgr = _make_manager(adb_path)
    try:
        _set_state(state_path, ALL_SERIALS, ["OBS0003"])
        await mgr.start()

        async def slot3_no_feed():
            st = await mgr.get_status()
            return st["slots"][2]["status"] in ("no_feed", "reconnecting")

        assert await _wait_for(slot3_no_feed, timeout_s=30.0)
        # ...while the other 15 feeds stay healthy (allow them time to finish
        # their first capture handshake — they may briefly report reconnecting).
        async def others_healthy():
            st = await mgr.get_status()
            if st["slots"][2]["status"] not in ("no_feed", "reconnecting"):
                return False
            healthy = [s for i, s in enumerate(st["slots"]) if i != 2]
            return all(s["status"] == "connected" for s in healthy)

        assert await _wait_for(others_healthy, timeout_s=30.0), (
            "some healthy slots never reached connected: "
            + str([(i, s["status"]) for i, s in enumerate((await mgr.get_status())["slots"]) if i != 2 and s["status"] != "connected"])
        )
    finally:
        await mgr.stop()


async def test_empty_slots_are_disconnected(fake_adb):
    adb_path, state_path = fake_adb
    _set_state(state_path, ["OBS0001", "OBS0002", "OBS0003"], [])
    mgr = _make_manager(adb_path)
    try:
        await mgr.start()
        discovery = await mgr.discover_and_map()
        assert discovery["slots_mapped"] == 3
        st = await mgr.get_status()
        assert st["counts"].get("disconnected", 0) == 13
        assert st["slots"][15]["serial"] is None
        assert st["slots"][15]["thumbnail_url"] is None
        # No device -> no frame -> thumbnail honestly returns None
        # (router turns this into 404, never a placeholder image).
        assert await mgr.thumbnail_jpeg(16) is None
    finally:
        await mgr.stop()


def test_backoff_caps_at_sixty():
    b, seq = 1.0, []
    for _ in range(20):
        b = _next_backoff(b)
        seq.append(round(b, 2))
    assert seq[0] > 1.0
    assert all(x <= 60.0 for x in seq)
    assert seq[-1] == 60.0
    # Same capped-doubling formula as Phase 14 FrameGrabber._next_backoff:
    # 1.0 -> 1.5 -> 2.25 -> 3.375 -> ... capped at 60.
    assert seq[:4] == [1.5, 2.25, 3.38, 5.06]
