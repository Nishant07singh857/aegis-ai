# backend/tests/unit/test_vision_capture.py
"""
Phase 14 (W1) unit tests: vision capture infrastructure.

No real Android device is required -- everything here runs on synthetic
images and on deliberately-broken configs to prove graceful degradation.
"""

import asyncio
import os
import time

import numpy as np
import pytest

from app.vision.capture.config import (
    CaptureConfig,
    load_capture_config,
    parse_roi,
)
from app.vision.capture.frame_grabber import (
    CapturedFrame,
    FrameGrabber,
    FrameSource,
    SourceState,
    StreamConfig,
)
from app.vision.capture.pipeline import DetectorOutcome, FramePipeline


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def make_synthetic_image(width: int = 640, height: int = 360) -> np.ndarray:
    """A deterministic fake "game frame": gradient + shapes, BGR uint8."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    # vertical gradient (blue channel)
    img[:, :, 0] = np.linspace(0, 255, height, dtype=np.uint8)[:, None]
    # a bright rectangle = "kill feed area"
    img[10:60, 10:200] = (255, 255, 255)
    # a red circle = "zone"
    yy, xx = np.mgrid[0:height, 0:width]
    img[((xx - width // 2) ** 2 + (yy - height // 2) ** 2) < 60 ** 2] = (0, 0, 255)
    return img


def make_frame(image: np.ndarray, source_id: str = "test_src") -> CapturedFrame:
    return CapturedFrame(
        frame_id=1,
        source_id=source_id,
        source_type=FrameSource.VIDEO_FILE,
        image=image,
        width=image.shape[1],
        height=image.shape[0],
        channels=3,
    )


class StubDetector:
    """Minimal detector implementing the BaseDetector interface."""

    def __init__(self, name: str = "stub_detector", fail: bool = False):
        self.name = name
        self._fail = fail
        self.calls = 0

    async def detect(self, frame: np.ndarray, **kwargs):
        self.calls += 1
        # Simulate blocking CPU work -- must NOT block the event loop.
        time.sleep(0.05)
        if self._fail:
            raise RuntimeError("boom")
        return {"mean": float(frame.mean()), "shape": frame.shape}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Config tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestCaptureConfig:
    def test_defaults(self):
        cfg = load_capture_config(env={})
        assert cfg.backend == "adb_poll"
        assert cfg.target_fps == 8.0
        assert cfg.grayscale is True
        assert cfg.detector_rois == {}

    def test_env_overrides(self):
        cfg = load_capture_config(env={
            "AEGIS_CAPTURE_BACKEND": "scrcpy",
            "AEGIS_ADB_SERIAL": "192.168.1.50:5555",
            "AEGIS_CAPTURE_FPS": "12",
            "AEGIS_CAPTURE_GRAYSCALE": "0",
            "AEGIS_ROI_kill_feed_ocr": "0.0,0.0,1.0,0.25",
        })
        assert cfg.backend == "scrcpy"
        assert cfg.adb_serial == "192.168.1.50:5555"
        assert cfg.target_fps == 12.0
        assert cfg.grayscale is False
        assert cfg.detector_rois["kill_feed_ocr"] == (0.0, 0.0, 1.0, 0.25)

    def test_bad_roi_ignored(self):
        cfg = load_capture_config(env={
            "AEGIS_ROI_bad": "not,a,roi",
            "AEGIS_ROI_out_of_range": "0.0,0.0,2.0,0.5",
        })
        assert cfg.detector_rois == {}
        assert parse_roi("0.1,0.2,0.3,0.4") == (0.1, 0.2, 0.3, 0.4)
        assert parse_roi("garbage") is None

    def test_roi_to_pixels(self):
        cfg = CaptureConfig(detector_rois={"d": (0.0, 0.0, 0.5, 0.5)})
        assert cfg.roi_to_pixels("d", 1000, 500) == (0, 0, 500, 250)
        assert cfg.roi_to_pixels("missing", 1000, 500) is None

    def test_env_not_leaked(self):
        load_capture_config(env={"AEGIS_ADB_SERIAL": "1.2.3.4:5555"})
        assert os.environ.get("AEGIS_ADB_SERIAL") != "1.2.3.4:5555"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pipeline tests (synthetic image, no device)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestFramePipeline:
    async def test_preprocess_resize_and_grayscale(self):
        cfg = CaptureConfig(
            preprocess_width=320, preprocess_height=180, grayscale=True
        )
        pipe = FramePipeline(config=cfg)
        result = await pipe.process_frame(make_frame(make_synthetic_image()))
        assert result.feed_ok is True
        assert result.preprocessed_shape == (180, 320)  # grayscale: no channel dim

    async def test_preprocess_keeps_color_when_disabled(self):
        cfg = CaptureConfig(
            preprocess_width=320, preprocess_height=180, grayscale=False
        )
        pipe = FramePipeline(config=cfg)
        result = await pipe.process_frame(make_frame(make_synthetic_image()))
        assert result.preprocessed_shape == (180, 320, 3)

    async def test_detector_dispatch_and_isolation(self):
        pipe = FramePipeline(config=CaptureConfig(grayscale=False))
        good = StubDetector("good_one")
        bad = StubDetector("bad_one", fail=True)
        pipe.register_detector(good)
        pipe.register_detector(bad)

        result = await pipe.process_frame(make_frame(make_synthetic_image()))

        assert result.feed_ok is True
        assert result.outcomes["good_one"].status == "success"
        assert result.outcomes["bad_one"].status == "failed"
        assert "boom" in result.outcomes["bad_one"].error
        assert good.calls == 1 and bad.calls == 1

    async def test_event_loop_not_blocked(self):
        """Detectors sleep 0.2s each; two in parallel must finish well under 0.4s."""
        pipe = FramePipeline(config=CaptureConfig(grayscale=False))

        class SlowDetector(StubDetector):
            async def detect(self, frame, **kwargs):
                self.calls += 1
                time.sleep(0.2)
                return {}

        pipe.register_detector(SlowDetector("slow_a"))
        pipe.register_detector(SlowDetector("slow_b"))

        ticked = []

        async def ticker():
            for _ in range(20):
                ticked.append(time.time())
                await asyncio.sleep(0.01)

        ticker_task = asyncio.create_task(ticker())
        await pipe.process_frame(make_frame(make_synthetic_image()))
        ticker_task.cancel()

        # The ticker kept firing while detectors ran => loop was not blocked.
        assert len(ticked) >= 10

    async def test_no_feed_is_honest(self):
        pipe = FramePipeline()
        pipe.register_detector(StubDetector("d1"))
        result = await pipe.process_frame(None)
        assert result.feed_ok is False
        assert result.outcomes["d1"].status == "skipped"
        assert result.outcomes["d1"].result is None

    async def test_detector_roi_override(self):
        cfg = CaptureConfig(
            preprocess_width=400,
            preprocess_height=200,
            grayscale=False,
            detector_rois={"roi_det": (0.0, 0.0, 0.5, 0.5)},
        )
        pipe = FramePipeline(config=cfg)
        det = StubDetector("roi_det")
        pipe.register_detector(det)
        result = await pipe.process_frame(make_frame(make_synthetic_image()))
        outcome = result.outcomes["roi_det"]
        assert outcome.status == "success"
        assert outcome.roi_applied == [0, 0, 200, 100]

    async def test_unregister(self):
        pipe = FramePipeline()
        det = StubDetector("temp")
        pipe.register_detector(det)
        assert pipe.unregister_detector("temp") is True
        assert pipe.detector_names == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Grabber degradation tests (no device present)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestGrabberDegradation:
    async def test_adb_disconnect_reports_no_feed_never_crashes(self):
        """adb binary missing -> honest NO_FEED/RECONNECTING, loop survives."""
        grabber = FrameGrabber()
        grabber.add_source(StreamConfig(
            source_id="dead_phone",
            source_type=FrameSource.ADB_DEVICE,
            device_id="192.0.2.1:5555",  # TEST-NET-1, unroutable
            target_fps=5,
            adb_path="/nonexistent/adb",
            reconnect_attempts=2,
            reconnect_delay=0.2,
            feed_timeout_s=1.0,
        ))
        assert await grabber.start_capture("dead_phone") is True
        await asyncio.sleep(2.5)  # let the loop fail + back off a few times

        state = grabber.get_source_state("dead_phone")
        assert state in (SourceState.NO_FEED, SourceState.RECONNECTING)
        stats = grabber.get_stats()["sources"]["dead_phone"]
        assert stats["errors"] >= 1
        assert "no frames" in stats["last_error"]
        assert stats["frames_captured"] == 0
        assert grabber.get_latest_frame("dead_phone") is None  # no fake frames

        await grabber.stop_capture("dead_phone")
        assert grabber.get_source_state("dead_phone") == SourceState.STOPPED

    async def test_missing_device_id_is_honest(self):
        grabber = FrameGrabber()
        grabber.add_source(StreamConfig(
            source_id="no_serial",
            source_type=FrameSource.ADB_DEVICE,
            device_id="",
            target_fps=5,
            reconnect_attempts=2,
            reconnect_delay=0.2,
        ))
        await grabber.start_capture("no_serial")
        await asyncio.sleep(1.5)
        stats = grabber.get_stats()["sources"]["no_serial"]
        assert "device_id" in stats["last_error"].lower()
        assert stats["frames_captured"] == 0
        await grabber.stop_all()

    async def test_ndi_is_honest_no_feed(self):
        grabber = FrameGrabber()
        grabber.add_source(StreamConfig(
            source_id="ndi_src",
            source_type=FrameSource.NDI_FEED,
            target_fps=5,
            reconnect_attempts=2,
            reconnect_delay=0.2,
        ))
        await grabber.start_capture("ndi_src")
        await asyncio.sleep(1.0)
        stats = grabber.get_stats()["sources"]["ndi_src"]
        assert stats["frames_captured"] == 0
        assert grabber.get_latest_frame("ndi_src") is None
        await grabber.stop_all()

    async def test_video_file_roundtrip(self):
        """A real (synthetic) video file captured through the persistent handle."""
        pytest.importorskip("cv2")
        import cv2

        path = "/tmp/aegis_test_src.mp4"
        writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
        for _ in range(10):
            writer.write(make_synthetic_image(160, 120))
        writer.release()

        grabber = FrameGrabber()
        grabber.add_source(StreamConfig(
            source_id="file_src",
            source_type=FrameSource.VIDEO_FILE,
            source_url=path,
            target_fps=10,
            buffer_size=5,
        ))
        await grabber.start_capture("file_src")
        await asyncio.sleep(1.5)

        stats = grabber.get_stats()["sources"]["file_src"]
        assert grabber.get_source_state("file_src") == SourceState.LIVE
        assert stats["frames_captured"] >= 3
        frame = grabber.get_latest_frame("file_src")
        assert frame is not None and frame.image.shape == (120, 160, 3)

        await grabber.stop_all()


class TestADBController:
    async def test_screencap_missing_binary_returns_none(self):
        from app.vision.capture.adb_controller import ADBController
        ctrl = ADBController(adb_path="/nonexistent/adb")
        # screencap_png is blocking by design -- run it in a thread.
        result = await asyncio.to_thread(ctrl.screencap_png, "whatever:5555")
        assert result is None

    async def test_discover_no_binary_returns_empty(self):
        from app.vision.capture.adb_controller import ADBController
        ctrl = ADBController(adb_path="/nonexistent/adb")
        assert await ctrl.discover_devices() == []

    def test_scrcpy_missing_binary_raises_honestly(self):
        from app.vision.capture.scrcpy_capture import ScrcpyCapture
        cap = ScrcpyCapture(serial="x", scrcpy_path="/nonexistent/scrcpy")
        with pytest.raises(RuntimeError, match="scrcpy binary not found"):
            cap.start()

    async def test_adb_poll_end_to_end_with_fake_adb(self, tmp_path):
        """
        Full adb_poll path with a fake `adb` executable that answers
        `exec-out screencap -p` with a static PNG: capture -> decode ->
        LIVE, then binary removed -> honest RECONNECTING, then restored
        -> LIVE again. No real device needed.
        """
        cv2 = pytest.importorskip("cv2")

        png_path = tmp_path / "frame.png"
        img = make_synthetic_image(320, 240)
        assert cv2.imwrite(str(png_path), img)

        adb_path = tmp_path / "adb"
        adb_path.write_text(
            "#!/bin/bash\n"
            'if [[ "$*" == *"exec-out screencap -p"* ]]; then\n'
            f"  cat {png_path}\n"
            "  exit 0\n"
            "fi\n"
            'echo "unsupported" >&2\n'
            "exit 1\n"
        )
        adb_path.chmod(0o755)

        grabber = FrameGrabber()
        grabber.add_source(StreamConfig(
            source_id="fake_phone",
            source_type=FrameSource.ADB_DEVICE,
            device_id="test:5555",
            target_fps=5,
            adb_path=str(adb_path),
            capture_backend="adb_poll",
            reconnect_attempts=3,
            reconnect_delay=0.2,
            feed_timeout_s=2.0,
        ))
        await grabber.start_capture("fake_phone")
        try:
            await asyncio.sleep(2.0)
            assert grabber.get_source_state("fake_phone") == SourceState.LIVE
            frame = grabber.get_latest_frame("fake_phone")
            assert frame is not None
            assert frame.image.shape == (240, 320, 3)

            # Simulate disconnect: hide the adb binary.
            hidden = tmp_path / "adb.hidden"
            adb_path.rename(hidden)
            await asyncio.sleep(3.0)
            assert grabber.get_source_state("fake_phone") in (
                SourceState.NO_FEED, SourceState.RECONNECTING,
            )
            assert grabber.get_stats()["sources"]["fake_phone"]["errors"] >= 1

            # Simulate reconnect: restore the binary.
            hidden.rename(adb_path)
            await asyncio.sleep(2.5)
            assert grabber.get_source_state("fake_phone") == SourceState.LIVE
        finally:
            await grabber.stop_all()

    async def test_scrcpy_reader_decodes_mkv_fifo(self, tmp_path):
        """
        Validate the ScrcpyCapture decode path without the scrcpy binary:
        ffmpeg writes a synthetic H.264 MKV stream into a FIFO and the
        reader thread must hand out real frames. Skipped when ffmpeg
        is unavailable.
        """
        import shutil
        import subprocess
        import threading

        if shutil.which("ffmpeg") is None:
            pytest.skip("ffmpeg not available")

        from app.vision.capture.scrcpy_capture import ScrcpyCapture

        fifo = str(tmp_path / "video.mkv")
        os.mkfifo(fifo)

        import structlog

        cap = ScrcpyCapture.__new__(ScrcpyCapture)
        cap._fifo_path = fifo
        cap._stop_event = threading.Event()
        cap._latest_frame = None
        cap._latest_lock = threading.Lock()
        cap._frames_read = 0
        cap._log = structlog.get_logger("test.scrcpy")
        reader = threading.Thread(target=cap._reader_loop, daemon=True)
        reader.start()

        writer = subprocess.Popen(
            [
                # -y is REQUIRED: ffmpeg prompts "overwrite?" for the
                # pre-created FIFO and exits without it, leaving the
                # reader blocked with no writer.
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=3",
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-f", "matroska", fifo,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.time() + 10
            frame = None
            while time.time() < deadline:
                frame = cap.read_frame()
                if frame is not None:
                    break
                await asyncio.sleep(0.1)
            assert frame is not None, "reader thread produced no frames"
            assert frame.shape == (240, 320, 3)
            assert cap.frames_read >= 1
        finally:
            cap._stop_event.set()
            reader.join(timeout=5)
            writer.wait(timeout=10)
