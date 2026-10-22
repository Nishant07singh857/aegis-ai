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

Per-detector frame-skip intervals (Phase 21 CV wiring):
    AEGIS_DETECTOR_INTERVAL_<DETECTOR_NAME>  run this detector every Nth
                               pipeline frame instead of every frame, e.g.
                               AEGIS_DETECTOR_INTERVAL_minimap_reader=5.
                               Detector names match BaseDetector.name and are
                               lowercased. Unset = the built-in default for the
                               Phase 21 CV detectors, 1 (every frame) for the
                               four Phase 14 detectors. Any value < 1 is
                               clamped to 1.

Multi-feed cheap-reader monitoring (Phase 25, W2 — see
``app/vision/multi_feed_monitor.py`` for the full design):
    AEGIS_MONITOR_ENABLED      1/0 -- enable the parallel per-feed monitors
                               (default: 1; with no extra feeds registered
                               the monitor is an honest no-op)
    AEGIS_MONITOR_TICK_S       scheduler tick in seconds (default: 0.5).
                               Each tick samples at most
                               AEGIS_MONITOR_BATCH_SIZE feeds, round-robin.
    AEGIS_MONITOR_BATCH_SIZE   feeds sampled per tick (default: 4)
    AEGIS_MONITOR_STALE_AFTER_S seconds a feed may deliver unchanged frames
                               before it is reported "stale_frames"
                               (default: 6.0)
    AEGIS_MONITOR_NO_SIGNAL_AFTER_S seconds without any frame before a feed
                               is reported "no_signal" (default: 6.0)
    AEGIS_MONITOR_VOTE_WINDOW  killfeed voting window in sampled frames
                               (default: 3)
    AEGIS_MONITOR_VOTE_THRESHOLD sightings inside the window required
                               before a killfeed entry is emitted
                               (default: 2 — the "2 of 3" rule)
    AEGIS_MONITOR_DEDUP_TTL_S  cross-feed kill/knock dedup TTL in seconds
                               (default: 30.0, matching KillFeedDetector)

CPU BUDGET (explicit, Phase 25): the full 10-detector pipeline on the
primary feed sustains ~2.3 FPS on the target CPU class. A monitor sample
runs ONLY the three cheap UI readers and ONLY on their HUD crops
(killfeed band ~9.5% of frame pixels; alive/zone crops <1% each), and
the killfeed read reuses the detector's band-hash cache, so a sample of
an unchanged band costs ~10 ms measured on this VM class (alive ~4 ms +
zone ~4 ms + ~1 ms cache hit). A CHANGED band costs one full killfeed
parse (~34 ms clean band; pathological noise frames spike far higher —
see multi_feed_monitor.py's docstring for the measured worst case).
With the defaults (tick 0.5 s, batch 4) the scheduler performs at most
8 samples/s total, spread round-robin: 1 feed is sampled every 0.5 s,
4 feeds every 0.5 s, 8 feeds every 1.0 s, 16 feeds every 2.0 s. Sixteen
feeds therefore do NOT each get the primary feed's rate — they get one
cheap sample every ~2 s, which is inside the ~5 s on-screen lifetime of
a killfeed entry, so the 2-of-3 vote still completes before the entry
fades. The effective per-feed interval for the CURRENT feed count is
computed by ``CaptureConfig.monitor_effective_interval_s()`` and
surfaced verbatim in the monitor status — never rounded up into a
claim it is not.
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

    # detector name -> run every Nth pipeline frame. The Phase 14
    # detectors (kill feed, alive count, zone timer, team status) run
    # every frame (1); the heavier Phase 21 CV detectors are staggered
    # so the ~2.3 FPS CPU budget is not blown on every pass.
    detector_intervals: Dict[str, int] = field(
        default_factory=lambda: {
            "hp_bar_detector": 2,
            "utility_detector": 3,
            "healing_detector": 3,
            "minimap_reader": 5,
            "zone_detector": 5,
            "loot_detector": 5,
        }
    )

    # --- Phase 25 (W2): multi-feed cheap-reader monitor budget --------
    # See the module docstring's CPU BUDGET paragraph. These values gate
    # the round-robin scheduler in app/vision/multi_feed_monitor.py; the
    # primary VisionCaptureService pipeline does NOT read them.
    monitor_enabled: bool = True
    monitor_tick_s: float = 0.5
    monitor_batch_size: int = 4
    monitor_stale_after_s: float = 6.0
    monitor_no_signal_after_s: float = 6.0
    monitor_vote_window: int = 3
    monitor_vote_threshold: int = 2
    monitor_dedup_ttl_s: float = 30.0

    # --- Phase 25 (W2): monitor budget helpers -------------------------

    def monitor_effective_interval_s(self, num_feeds: int) -> float:
        """Seconds between two samples of the SAME feed under round-robin.

        Honest arithmetic, no rounding up: with ``batch`` feeds sampled
        per ``tick``, a fleet of N feeds cycles in ceil(N / batch)
        ticks. Zero feeds -> 0.0 (nothing is sampled at all).
        """
        if num_feeds <= 0:
            return 0.0
        batch = max(1, int(self.monitor_batch_size))
        ticks_per_cycle = -(-int(num_feeds) // batch)  # ceil division
        return round(ticks_per_cycle * float(self.monitor_tick_s), 3)

    def monitor_budget_summary(self, num_feeds: int) -> Dict[str, object]:
        """The monitor CPU budget as data (surfaced in runtime status)."""
        interval = self.monitor_effective_interval_s(num_feeds)
        return {
            "enabled": bool(self.monitor_enabled),
            "tick_s": float(self.monitor_tick_s),
            "batch_size": int(self.monitor_batch_size),
            "feeds": int(num_feeds),
            "max_samples_per_s": round(
                int(self.monitor_batch_size) / max(0.05, float(self.monitor_tick_s)), 2
            ),
            "effective_interval_s_per_feed": interval,
            "effective_samples_per_s_per_feed": (
                round(1.0 / interval, 3) if interval > 0 else 0.0
            ),
            "vote_window_frames": int(self.monitor_vote_window),
            "vote_threshold": int(self.monitor_vote_threshold),
            "assumption": (
                "cheap UI readers on HUD crops only; full 10-detector "
                "pipeline stays on the primary feed (~2.3 FPS CPU class)"
            ),
        }

    def get_roi(self, detector_name: str) -> Optional[Tuple[float, float, float, float]]:
        """ROI override for a detector, or None when unset."""
        return self.detector_rois.get(detector_name)

    def detector_interval(self, detector_name: str) -> int:
        """Frame-skip interval for a detector (every Nth frame)."""
        return max(1, int(self.detector_intervals.get(detector_name, 1) or 1))

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
    # Collect per-detector frame-skip interval overrides:
    # AEGIS_DETECTOR_INTERVAL_<DETECTOR_NAME> (over the built-in defaults)
    intervals: Dict[str, int] = dict(
        CaptureConfig.__dataclass_fields__["detector_intervals"].default_factory()
    )
    for key, value in os.environ.items():
        if key.startswith("AEGIS_ROI_"):
            detector_name = key[len("AEGIS_ROI_"):].strip().lower()
            if not detector_name:
                continue
            roi = parse_roi(value)
            if roi is not None:
                rois[detector_name] = roi
        elif key.startswith("AEGIS_DETECTOR_INTERVAL_"):
            detector_name = key[len("AEGIS_DETECTOR_INTERVAL_"):].strip().lower()
            if not detector_name:
                continue
            try:
                intervals[detector_name] = max(1, int(value))
            except (TypeError, ValueError):
                continue

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
        detector_intervals=intervals,
        # Phase 25 (W2): multi-feed monitor budget (see module docstring).
        monitor_enabled=_get_bool("AEGIS_MONITOR_ENABLED", True),
        monitor_tick_s=max(0.05, _get_float("AEGIS_MONITOR_TICK_S", 0.5)),
        monitor_batch_size=max(1, _get_int("AEGIS_MONITOR_BATCH_SIZE", 4)),
        monitor_stale_after_s=max(
            0.5, _get_float("AEGIS_MONITOR_STALE_AFTER_S", 6.0)
        ),
        monitor_no_signal_after_s=max(
            0.5, _get_float("AEGIS_MONITOR_NO_SIGNAL_AFTER_S", 6.0)
        ),
        monitor_vote_window=max(1, _get_int("AEGIS_MONITOR_VOTE_WINDOW", 3)),
        monitor_vote_threshold=max(
            1, _get_int("AEGIS_MONITOR_VOTE_THRESHOLD", 2)
        ),
        monitor_dedup_ttl_s=max(
            1.0, _get_float("AEGIS_MONITOR_DEDUP_TTL_S", 30.0)
        ),
    )
