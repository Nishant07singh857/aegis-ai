# backend/app/vision/capture/config.py
"""
AEGIS AI - Vision Capture Configuration

Env-based configuration for the Phase 14 vision capture pipeline.
Every value has a sane default; the pipeline runs with zero env vars
set (and honestly reports "no feed" until a device is reachable).

Stdlib only -- no dependency on pydantic or the app settings module,
so this module can be imported by lightweight tooling and tests.

Environment variables
---------------------
General capture:
    AEGIS_CAPTURE_BACKEND      adb_poll | scrcpy | mss | none   (default: adb_poll)
                               adb_poll: loop `adb exec-out screencap` (5-10 FPS)
                               scrcpy:   persistent scrcpy H.264 stream (higher FPS)
                               mss:      local PC screen capture via the mss package
                               none:     capture disabled (honest "no feed")

Android device:
    AEGIS_ADB_SERIAL           ADB serial or tcp address, e.g. "192.168.1.50:5555"
                               (default: "" -- pipeline waits honestly)
    AEGIS_ADB_PATH             adb binary (default: "adb")
    AEGIS_SCRCPY_PATH          scrcpy binary (default: "scrcpy")
    AEGIS_SCRCPY_MAX_SIZE      scrcpy --max-size in px (default: 960)
    AEGIS_SCRCPY_MAX_FPS       scrcpy --max-fps (default: 15)

Frame pacing / preprocessing:
    AEGIS_CAPTURE_FPS          target frames per second (default: 8)
    AEGIS_CAPTURE_WIDTH        preprocess width in px (default: 960)
    AEGIS_CAPTURE_HEIGHT       preprocess height in px (default: 540)
    AEGIS_CAPTURE_GRAYSCALE    1/0 -- convert frames to grayscale (default: 1)
    AEGIS_FRAME_QUEUE_SIZE     per-source latest-frame buffer depth (default: 2,
                               drop-oldest semantics -- detectors never see stale frames)

Per-detector region of interest:
    AEGIS_ROI_<DETECTOR_NAME>  "x,y,w,h" as relative 0..1 floats of the
                               preprocessed frame, e.g. AEGIS_ROI_kill_feed_ocr="0.0,0.0,1.0,0.25"
                               Detector names match BaseDetector.name
                               (e.g. kill_feed_ocr, minimap_reader, zone_detector).
                               Unset = no override; the detector's own
                               get_detection_regions() is used.

Resilience:
    AEGIS_RECONNECT_DELAY_S    initial reconnect backoff in seconds (default: 2.0)
    AEGIS_MAX_RECONNECT_ATTEMPTS attempts before the delay is capped (default: 5;
                               retries continue with capped backoff -- the server
                               never gives up and never crashes)
    AEGIS_FEED_TIMEOUT_S       seconds without a frame before state becomes
                               NO_FEED (default: 5.0)
"""

import os
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def parse_roi(value: str) -> Optional[Tuple[float, float, float, float]]:
    """
    Parse an "x,y,w,h" region-of-interest string into relative 0..1 floats.

    Returns None when the string is malformed (never raises).
    """
    try:
        parts = [float(p.strip()) for p in value.split(",")]
        if len(parts) != 4:
            return None
        x, y, w, h = parts
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            return None
        if not (0.0 < w <= 1.0 and 0.0 < h <= 1.0):
            return None
        if x + w > 1.0 + 1e-9 or y + h > 1.0 + 1e-9:
            return None
        return (x, y, w, h)
    except (ValueError, AttributeError):
        return None


@dataclass
class CaptureConfig:
    """Resolved capture configuration (see module docstring for env vars)."""

    backend: str = "adb_poll"
    adb_serial: str = ""
    adb_path: str = "adb"
    scrcpy_path: str = "scrcpy"
    scrcpy_max_size: int = 960
    scrcpy_max_fps: int = 15

    target_fps: float = 8.0
    preprocess_width: int = 960
    preprocess_height: int = 540
    grayscale: bool = True
    frame_queue_size: int = 2

    reconnect_delay_s: float = 2.0
    max_reconnect_attempts: int = 5
    feed_timeout_s: float = 5.0

    # detector name -> (x, y, w, h) relative ROI override
    detector_rois: Dict[str, Tuple[float, float, float, float]] = field(
        default_factory=dict
    )

    def get_roi(self, detector_name: str) -> Optional[Tuple[float, float, float, float]]:
        """ROI override for a detector, or None when unset."""
        return self.detector_rois.get(detector_name)

    def roi_to_pixels(
        self,
        detector_name: str,
        frame_width: int,
        frame_height: int,
    ) -> Optional[Tuple[int, int, int, int]]:
        """ROI override converted to pixel coordinates for a given frame size."""
        roi = self.get_roi(detector_name)
        if roi is None:
            return None
        x, y, w, h = roi
        px = int(x * frame_width)
        py = int(y * frame_height)
        pw = max(1, int(w * frame_width))
        ph = max(1, int(h * frame_height))
        # Clamp inside the frame.
        px = min(px, frame_width - 1)
        py = min(py, frame_height - 1)
        pw = min(pw, frame_width - px)
        ph = min(ph, frame_height - py)
        return (px, py, pw, ph)


def load_capture_config(env: Optional[Dict[str, str]] = None) -> CaptureConfig:
    """
    Build a CaptureConfig from the environment.

    ``env`` is an optional mapping used for tests; when None, os.environ
    is used.
    """
    if env is not None:
        original = dict(os.environ)
        os.environ.clear()
        os.environ.update(original)
        os.environ.update(env)
        try:
            return _load_from_os_environ()
        finally:
            os.environ.clear()
            os.environ.update(original)
    return _load_from_os_environ()


def _load_from_os_environ() -> CaptureConfig:
    backend = os.getenv("AEGIS_CAPTURE_BACKEND", "adb_poll").strip().lower()
    if backend not in ("adb_poll", "scrcpy", "mss", "none"):
        backend = "adb_poll"

    target_fps = _get_float("AEGIS_CAPTURE_FPS", 8.0)
    target_fps = max(0.5, min(60.0, target_fps))

    # Collect per-detector ROI overrides: AEGIS_ROI_<DETECTOR_NAME>
    rois: Dict[str, Tuple[float, float, float, float]] = {}
    for key, value in os.environ.items():
        if not key.startswith("AEGIS_ROI_"):
            continue
        detector_name = key[len("AEGIS_ROI_"):].strip().lower()
        if not detector_name:
            continue
        roi = parse_roi(value)
        if roi is not None:
            rois[detector_name] = roi

    return CaptureConfig(
        backend=backend,
        adb_serial=os.getenv("AEGIS_ADB_SERIAL", "").strip(),
        adb_path=os.getenv("AEGIS_ADB_PATH", "adb").strip() or "adb",
        scrcpy_path=os.getenv("AEGIS_SCRCPY_PATH", "scrcpy").strip() or "scrcpy",
        scrcpy_max_size=_get_int("AEGIS_SCRCPY_MAX_SIZE", 960),
        scrcpy_max_fps=_get_int("AEGIS_SCRCPY_MAX_FPS", 15),
        target_fps=target_fps,
        preprocess_width=_get_int("AEGIS_CAPTURE_WIDTH", 960),
        preprocess_height=_get_int("AEGIS_CAPTURE_HEIGHT", 540),
        grayscale=_get_bool("AEGIS_CAPTURE_GRAYSCALE", True),
        frame_queue_size=max(1, _get_int("AEGIS_FRAME_QUEUE_SIZE", 2)),
        reconnect_delay_s=max(0.1, _get_float("AEGIS_RECONNECT_DELAY_S", 2.0)),
        max_reconnect_attempts=max(1, _get_int("AEGIS_MAX_RECONNECT_ATTEMPTS", 5)),
        feed_timeout_s=max(1.0, _get_float("AEGIS_FEED_TIMEOUT_S", 5.0)),
        detector_rois=rois,
    )
