# backend/app/vision/capture/frame_grabber.py
"""
AEGIS AI - Frame Grabber
Captures video frames from multiple sources:
ADB devices (adb exec-out screencap loop or scrcpy), PC screen (mss),
RTMP streams, video files, and IP cameras.

Phase 14 notes:
- The old ``_capture_adb`` returned a black placeholder frame -- that is
  gone. Every backend below returns REAL frames or None (honest "no feed").
- RTMP/file/screen sources now keep PERSISTENT handles (the old code
  reopened cv2.VideoCapture / mss on every frame, which never worked as
  a continuous stream).
- Capture loops pace with time-aware sleeps, never block the asyncio
  event loop (all blocking I/O runs via ``asyncio.to_thread``), and
  never crash the server: disconnects flip the source to NO_FEED /
  RECONNECTING and auto-retry with backoff.
"""

import asyncio
import functools
import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog
import numpy as np

# NOTE: cv2 at module top on purpose -- OpenCV's import-time initialization
# happens in the main thread rather than lazily inside worker threads.
import cv2

logger = structlog.get_logger("aegis.vision.capture")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FrameSource(str, Enum):
    """Types of frame sources"""
    RTMP_STREAM = "rtmp_stream"
    NDI_FEED = "ndi_feed"
    # Phase 22 (W3, M2): plug-ready only — see vision/ingest_plugins.py.
    # Honest no-feed until a real WebRTC producer is implemented.
    WEBRTC_FEED = "webrtc_feed"
    SCREEN_CAPTURE = "screen_capture"
    ADB_DEVICE = "adb_device"
    VIDEO_FILE = "video_file"
    WEBCAM = "webcam"
    IP_CAMERA = "ip_camera"

class FrameFormat(str, Enum):
    """Frame color formats"""
    BGR = "bgr"           # OpenCV default
    RGB = "rgb"
    GRAY = "gray"
    YUV = "yuv"
    NV12 = "nv12"

class SourceState(str, Enum):
    """Capture state of a source (graceful-degradation state machine)."""
    STOPPED = "stopped"          # never started / explicitly stopped
    CONNECTING = "connecting"    # opening the underlying handle
    LIVE = "live"                # frames flowing within the timeout
    NO_FEED = "no_feed"          # no frames for longer than feed_timeout_s
    RECONNECTING = "reconnecting"  # handle failed, backing off and retrying

@dataclass
class CapturedFrame:
    """A single captured video frame"""
    frame_id: int
    source_id: str
    source_type: FrameSource
    timestamp: float = field(default_factory=time.time)
    # Phase 22 (W3, M1/M2 latency sync): ``timestamp`` IS the server
    # capture (grab) time. ``device_capture_ts`` stays None until a
    # source can supply a genuine on-device capture timestamp — the
    # sync tracker labels offsets accordingly and never pretends
    # grab-time offsets are device-clock offsets.
    device_capture_ts: Optional[float] = None

    # Image data
    image: Optional[np.ndarray] = None
    width: int = 1920
    height: int = 1080
    channels: int = 3
    format: FrameFormat = FrameFormat.BGR

    # Metadata
    fps: float = 30.0
    is_keyframe: bool = False
    sequence_number: int = 0

    # Device info
    device_id: str = ""
    team_id: str = ""
    player_id: str = ""

    # Quality
    quality_score: float = 1.0
    has_artifact: bool = False

    def get_roi(self, x: int, y: int, w: int, h: int) -> Optional[np.ndarray]:
        """Extract region of interest"""
        if self.image is None:
            return None
        return self.image[y:y+h, x:x+w]

    def resize(self, width: int, height: int) -> None:
        """Resize frame in place"""
        if self.image is not None:
            self.image = cv2.resize(self.image, (width, height))
            self.width = width
            self.height = height

    def to_grayscale(self) -> None:
        """Convert to grayscale"""
        if self.image is not None and self.channels == 3:
            self.image = cv2.cvtColor(self.image, cv2.COLOR_BGR2GRAY)
            self.channels = 1
            self.format = FrameFormat.GRAY


@dataclass
class StreamConfig:
    """Configuration for a capture stream"""
    source_id: str
    source_type: FrameSource
    source_url: str = ""
    device_id: str = ""
    team_id: str = ""
    player_id: str = ""

    # Quality
    target_fps: int = 30
    target_width: int = 1920
    target_height: int = 1080
    bitrate: int = 8000000

    # Buffering
    buffer_size: int = 30
    reconnect_attempts: int = 5
    reconnect_delay: float = 2.0
    feed_timeout_s: float = 5.0   # no frame for this long -> NO_FEED

    # Android capture backend for ADB_DEVICE sources:
    #   "adb_poll": loop `adb exec-out screencap` (5-10 FPS, no extra deps)
    #   "scrcpy":   persistent scrcpy H.264 stream (higher FPS, needs scrcpy)
    capture_backend: str = "adb_poll"
    adb_path: str = "adb"
    scrcpy_path: str = "scrcpy"
    scrcpy_max_size: int = 960
    scrcpy_max_fps: int = 15

    # Processing
    enable_resize: bool = False
    enable_grayscale: bool = False
    roi: Optional[Tuple[int, int, int, int]] = None


class FrameGrabber:
    """
    Multi-source frame capture system.

    Supported backends:
    - ADB_DEVICE via "adb_poll" (adb exec-out screencap loop, 5-10 FPS)
    - ADB_DEVICE via "scrcpy" (persistent H.264 stream, higher FPS)
    - SCREEN_CAPTURE via mss (free, MIT-licensed)
    - RTMP_STREAM / VIDEO_FILE / IP_CAMERA / WEBCAM via OpenCV (persistent handles)
    - NDI_FEED: not supported in this build (honest "no feed", no fake frames)

    Graceful degradation: a disconnected source reports SourceState.NO_FEED
    (or RECONNECTING while backing off), retries forever with capped
    backoff, and never raises out of the capture loop.
    """

    def __init__(self):
        # Active captures
        self.captures: Dict[str, StreamConfig] = {}
        self.frame_buffers: Dict[str, deque] = {}
        self.capture_tasks: Dict[str, asyncio.Task] = {}

        # Persistent per-source handles (cv2.VideoCapture, mss, ScrcpyCapture, ...)
        self._handles: Dict[str, Any] = {}

        # State machine + diagnostics per source
        self._states: Dict[str, SourceState] = {}
        self._last_frame_time: Dict[str, float] = {}
        self._last_error: Dict[str, str] = {}
        self._consecutive_failures: Dict[str, int] = {}

        # Frame counter
        self._frame_counter: Dict[str, int] = {}

        # Statistics
        self._total_frames_captured: int = 0
        self._capture_errors: Dict[str, int] = {}

        self._ndi_warned = False
        self._webrtc_warned = False

        # Phase 22 (W3, M1/M2): feed-health scoring + latency-sync
        # tracking, wired into the capture loop below. Before Phase 22
        # FeedHealthMonitor.record_frame()/record_frame_drop() had ZERO
        # call sites, so the (real) health score was never exposed.
        from .feed_health_monitor import FeedHealthMonitor
        from .latency_sync import LatencySyncTracker

        self.health = FeedHealthMonitor()
        self.sync = LatencySyncTracker()

        self._running = False
        self._log = logger.bind(component="frame_grabber")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CAPTURE MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def add_source(self, config: StreamConfig) -> str:
        """Add a new capture source"""
        source_id = config.source_id

        self.captures[source_id] = config
        self.frame_buffers[source_id] = deque(maxlen=config.buffer_size)
        self._frame_counter[source_id] = 0
        self._capture_errors[source_id] = 0
        self._states[source_id] = SourceState.STOPPED
        self._last_frame_time[source_id] = 0.0
        self._last_error[source_id] = ""
        self._consecutive_failures[source_id] = 0
        self.health.register_feed(source_id, expected_fps=float(config.target_fps))
        self.sync.register_feed(source_id)

        self._log.info(
            f"Source added: {source_id}",
            type=config.source_type.value,
            resolution=f"{config.target_width}x{config.target_height}",
            fps=config.target_fps,
        )

        return source_id

    def remove_source(self, source_id: str) -> bool:
        """Remove a capture source"""
        if source_id in self.captures:
            # Cancel capture task
            task = self.capture_tasks.pop(source_id, None)
            if task:
                task.cancel()

            self._close_handle(source_id)

            del self.captures[source_id]
            del self.frame_buffers[source_id]
            del self._frame_counter[source_id]
            del self._capture_errors[source_id]
            self.health.unregister_feed(source_id)
            self.sync.unregister_feed(source_id)
            self._states.pop(source_id, None)
            self._last_frame_time.pop(source_id, None)
            self._last_error.pop(source_id, None)
            self._consecutive_failures.pop(source_id, None)

            self._log.info(f"Source removed: {source_id}")
            return True

        return False

    async def start_capture(self, source_id: str) -> bool:
        """Start capturing from a source"""
        config = self.captures.get(source_id)
        if not config:
            self._log.error(f"Source not found: {source_id}")
            return False

        if source_id in self.capture_tasks and not self.capture_tasks[source_id].done():
            self._log.warning(f"Capture already running: {source_id}")
            return True

        self._running = True
        self._states[source_id] = SourceState.CONNECTING

        # Create capture task
        task = asyncio.create_task(self._capture_loop(config))
        self.capture_tasks[source_id] = task

        self._log.info(f"Capture started: {source_id}")
        return True

    async def stop_capture(self, source_id: str) -> bool:
        """Stop capturing from a source"""
        task = self.capture_tasks.pop(source_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._close_handle(source_id)
        if source_id in self._states:
            self._states[source_id] = SourceState.STOPPED

        self._log.info(f"Capture stopped: {source_id}")
        return True

    async def stop_all(self) -> None:
        """Stop all captures"""
        self._running = False

        for source_id in list(self.capture_tasks.keys()):
            await self.stop_capture(source_id)

        self._log.info("All captures stopped")

    def get_source_state(self, source_id: str) -> Optional[SourceState]:
        """Current capture state (honest feed status) for a source."""
        return self._states.get(source_id)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PERSISTENT HANDLES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _close_handle(self, source_id: str) -> None:
        """Close and drop the persistent handle for a source."""
        handle = self._handles.pop(source_id, None)
        if handle is None:
            return
        try:
            release = getattr(handle, "release", None)
            if callable(release):
                release()
            stop = getattr(handle, "stop", None)
            if callable(stop):
                stop()
            close = getattr(handle, "close", None)
            if callable(close):
                close()
        except Exception as e:
            self._log.debug(f"Handle close error for {source_id}: {e}")

    def _open_handle(self, config: StreamConfig) -> Any:
        """
        Open (or reuse) the persistent capture handle for a source.

        Runs in a worker thread -- may block. Raises on failure.
        """
        source_id = config.source_id
        handle = self._handles.get(source_id)
        if handle is not None:
            return handle

        if config.source_type == FrameSource.ADB_DEVICE:
            handle = self._open_adb_handle(config)
        elif config.source_type == FrameSource.SCREEN_CAPTURE:
            handle = self._open_screen_handle(config)
        elif config.source_type in (
            FrameSource.RTMP_STREAM,
            FrameSource.VIDEO_FILE,
            FrameSource.IP_CAMERA,
            FrameSource.WEBCAM,
        ):
            handle = self._open_cv_handle(config)
        elif config.source_type == FrameSource.NDI_FEED:
            raise RuntimeError("NDI capture is not supported in this build")
        elif config.source_type == FrameSource.WEBRTC_FEED:
            raise RuntimeError(
                "WebRTC capture is plug-ready only in this build "
                "(see app/vision/ingest_plugins.py)"
            )
        else:
            raise RuntimeError(f"Unsupported source type: {config.source_type}")

        self._handles[source_id] = handle
        return handle

    def _open_adb_handle(self, config: StreamConfig) -> Any:
        """Open the Android capture handle for the configured backend."""
        if not config.device_id:
            raise RuntimeError(
                "ADB_DEVICE source has no device_id -- set AEGIS_ADB_SERIAL "
                "or pass device_id in StreamConfig"
            )

        backend = (config.capture_backend or "adb_poll").strip().lower()
        if backend == "scrcpy":
            from .scrcpy_capture import ScrcpyCapture
            cap = ScrcpyCapture(
                serial=config.device_id,
                max_size=config.scrcpy_max_size,
                max_fps=config.scrcpy_max_fps,
                scrcpy_path=config.scrcpy_path,
            )
            cap.start()  # raises RuntimeError with install help when unavailable
            return cap

        if backend != "adb_poll":
            self._log.warning(
                f"Unknown ADB capture backend '{backend}', falling back to adb_poll"
            )

        # adb_poll: lightweight controller used for exec-out screencap.
        from .adb_controller import ADBController
        return ADBController(adb_path=config.adb_path)

    def _open_screen_handle(self, config: StreamConfig) -> Any:
        """Open a persistent mss handle for PC screen capture."""
        try:
            from mss import mss
        except ImportError as e:
            raise RuntimeError(
                "mss is not installed. Install it with: pip install mss "
                "(free, MIT-licensed)."
            ) from e
        sct = mss()
        if not sct.monitors:
            sct.close()
            raise RuntimeError("mss found no monitors on this machine")
        return sct

    def _open_cv_handle(self, config: StreamConfig) -> Any:
        """Open a persistent OpenCV VideoCapture."""
        url: Any = config.source_url
        if config.source_type == FrameSource.WEBCAM and not url:
            url = 0  # default webcam
        if not url and config.source_type != FrameSource.WEBCAM:
            raise RuntimeError(
                f"{config.source_type.value} source has no source_url"
            )
        cap = cv2.VideoCapture(url)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"Could not open video source: {url}")
        return cap

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CAPTURE LOOPS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def _capture_loop(self, config: StreamConfig) -> None:
        """Main capture loop for a source. Never raises out."""
        source_id = config.source_id
        period = 1.0 / max(0.5, config.target_fps)
        backoff = config.reconnect_delay

        while self._running:
            loop_start = time.perf_counter()
            try:
                # (Re)open the handle when needed -- blocking, so thread it.
                try:
                    handle = await asyncio.to_thread(self._open_handle, config)
                except Exception as e:
                    await self._note_failure(config, f"open failed: {e}", backoff)
                    backoff = self._next_backoff(backoff)
                    continue

                frame = await self._capture_frame(config, handle)

                if frame is not None:
                    frame = self._process_frame(frame, config)
                    self.frame_buffers[source_id].append(frame)
                    self._frame_counter[source_id] += 1
                    self._total_frames_captured += 1
                    self._last_frame_time[source_id] = time.time()
                    self._consecutive_failures[source_id] = 0
                    self._last_error[source_id] = ""
                    self._states[source_id] = SourceState.LIVE
                    backoff = config.reconnect_delay  # reset backoff on success
                    # Phase 22 (W3): health + sync bookkeeping per frame.
                    self.health.record_frame(source_id, timestamp=frame.timestamp)
                    self.health.update_health(source_id)
                    self.sync.record_frame(
                        source_id,
                        capture_ts=frame.timestamp,
                        device_ts=frame.device_capture_ts,
                    )
                else:
                    # Phase 22 (W3): a missed capture IS a frame drop —
                    # log it in the health monitor (was never wired).
                    self.health.record_frame_drop(source_id)
                    # No frame this tick (silent device / stalled stream):
                    # not fatal, but after enough misses the source is
                    # honestly reported as RECONNECTING and the handle is
                    # dropped so the next tick attempts a fresh open.
                    misses = self._consecutive_failures[source_id] + 1
                    self._consecutive_failures[source_id] = misses
                    if misses >= config.reconnect_attempts:
                        if self._states.get(source_id) != SourceState.RECONNECTING:
                            self._states[source_id] = SourceState.RECONNECTING
                            self._capture_errors[source_id] += 1
                            self._log.warning(
                                f"No frames from {source_id} after {misses} "
                                "attempts -- RECONNECTING"
                            )
                        self._last_error[source_id] = (
                            f"no frames received ({misses} consecutive attempts)"
                        )
                        self._close_handle(source_id)
                        await asyncio.sleep(backoff)
                        backoff = self._next_backoff(backoff)
                    self._refresh_feed_state(config)

            except asyncio.CancelledError:
                break
            except Exception as e:
                # The loop itself must never die on a bad frame/handle.
                await self._note_failure(config, str(e)[:300], backoff)
                backoff = self._next_backoff(backoff)

            # Time-aware pacing: keep the effective FPS close to target.
            elapsed = time.perf_counter() - loop_start
            await asyncio.sleep(max(0.0, period - elapsed))

    def _next_backoff(self, backoff: float) -> float:
        """Exponential backoff capped at 60s."""
        return min(60.0, backoff * 1.5 if backoff >= 1.0 else backoff + 1.0)

    async def _note_failure(self, config: StreamConfig, error: str, backoff: float) -> None:
        """Record a failure, flip state, close the stale handle, back off."""
        source_id = config.source_id
        self._capture_errors[source_id] += 1
        # Phase 22 (W3): failures count as drops + health errors too.
        self.health.record_frame_drop(source_id)
        self.health.record_error(source_id, error)
        self._consecutive_failures[source_id] += 1
        self._last_error[source_id] = error
        self._close_handle(source_id)

        attempts = self._consecutive_failures[source_id]
        if attempts >= config.reconnect_attempts:
            self._states[source_id] = SourceState.RECONNECTING
        self._refresh_feed_state(config)

        self._log.warning(
            f"Capture failure for {source_id} (attempt {attempts}), "
            f"retrying in {backoff:.1f}s: {error}"
        )
        await asyncio.sleep(backoff)

    def _refresh_feed_state(self, config: StreamConfig) -> None:
        """Flip LIVE -> NO_FEED when frames stop arriving."""
        source_id = config.source_id
        if self._states.get(source_id) == SourceState.LIVE:
            age = time.time() - self._last_frame_time.get(source_id, 0.0)
            if age > config.feed_timeout_s:
                self._states[source_id] = SourceState.NO_FEED
                self._log.warning(
                    f"No frames from {source_id} for {age:.1f}s -- NO_FEED"
                )

    async def _capture_frame(
        self, config: StreamConfig, handle: Any
    ) -> Optional[CapturedFrame]:
        """Capture a single frame using the source's persistent handle."""

        if config.source_type == FrameSource.RTMP_STREAM:
            return await self._capture_cv(config, handle, FrameSource.RTMP_STREAM)
        elif config.source_type == FrameSource.VIDEO_FILE:
            return await self._capture_cv(config, handle, FrameSource.VIDEO_FILE)
        elif config.source_type == FrameSource.IP_CAMERA:
            return await self._capture_cv(config, handle, FrameSource.IP_CAMERA)
        elif config.source_type == FrameSource.WEBCAM:
            return await self._capture_cv(config, handle, FrameSource.WEBCAM)
        elif config.source_type == FrameSource.NDI_FEED:
            return await self._capture_ndi(config)
        elif config.source_type == FrameSource.WEBRTC_FEED:
            return await self._capture_webrtc(config)
        elif config.source_type == FrameSource.SCREEN_CAPTURE:
            return await self._capture_screen(config, handle)
        elif config.source_type == FrameSource.ADB_DEVICE:
            return await self._capture_adb(config, handle)
        else:
            return None

    async def _capture_cv(
        self, config: StreamConfig, cap: Any, source_type: FrameSource
    ) -> Optional[CapturedFrame]:
        """Read one frame from a persistent cv2.VideoCapture (threaded)."""
        read = functools.partial(cap.read)
        ok, frame = await asyncio.to_thread(read)
        if not ok or frame is None:
            # End of file or stream stall: drop the handle so the loop
            # reopens it (files loop from the start on reopen).
            self._close_handle(config.source_id)
            return None
        return CapturedFrame(
            frame_id=self._frame_counter.get(config.source_id, 0) + 1,
            source_id=config.source_id,
            source_type=source_type,
            image=frame,
            width=frame.shape[1],
            height=frame.shape[0],
            channels=frame.shape[2] if len(frame.shape) > 2 else 1,
            device_id=config.device_id,
            team_id=config.team_id,
        )

    async def _capture_ndi(self, config: StreamConfig) -> Optional[CapturedFrame]:
        """NDI is not bundled in this build -- honest no-feed, logged once."""
        if not self._ndi_warned:
            self._log.warning(
                "NDI capture is not supported in this build -- "
                f"source {config.source_id} will report NO_FEED. "
                "Use RTMP, ADB, or screen capture instead."
            )
            self._ndi_warned = True
        await asyncio.sleep(0)  # keep the signature async
        return None

    async def _capture_webrtc(self, config: StreamConfig) -> Optional[CapturedFrame]:
        """WebRTC is plug-ready only — honest no-feed via the plug point."""
        from ..ingest_plugins import get_ingest_plugin

        plugin = get_ingest_plugin("webrtc")
        if plugin is not None and plugin.available():
            image = plugin.grab_frame()
            if image is not None:  # pragma: no cover - future producer
                return CapturedFrame(
                    frame_id=self._frame_counter.get(config.source_id, 0) + 1,
                    source_id=config.source_id,
                    source_type=FrameSource.WEBRTC_FEED,
                    image=image,
                    width=image.shape[1],
                    height=image.shape[0],
                )
        if not self._webrtc_warned:
            self._log.warning(
                "WebRTC capture is plug-ready only in this build -- "
                f"source {config.source_id} will report NO_FEED."
            )
            self._webrtc_warned = True
        await asyncio.sleep(0)
        return None

    async def _capture_screen(
        self, config: StreamConfig, sct: Any
    ) -> Optional[CapturedFrame]:
        """Capture the primary monitor via a persistent mss handle."""
        def _grab():
            monitor = sct.monitors[0]  # primary monitor
            shot = sct.grab(monitor)
            frame = np.array(shot)
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

        try:
            frame = await asyncio.to_thread(_grab)
        except Exception as e:
            raise RuntimeError(f"mss screen grab failed: {e}") from e

        return CapturedFrame(
            frame_id=self._frame_counter.get(config.source_id, 0) + 1,
            source_id=config.source_id,
            source_type=FrameSource.SCREEN_CAPTURE,
            image=frame,
            width=frame.shape[1],
            height=frame.shape[0],
        )

    async def _capture_adb(
        self, config: StreamConfig, handle: Any
    ) -> Optional[CapturedFrame]:
        """
        Capture one frame from an Android device.

        - backend "adb_poll" (default): ``adb exec-out screencap -p`` in a
          thread, PNG decoded with OpenCV. ~5-10 FPS, no extra dependencies.
        - backend "scrcpy": latest frame from the persistent scrcpy H.264
          stream (higher FPS).
        """
        backend = (config.capture_backend or "adb_poll").strip().lower()

        if backend == "scrcpy":
            frame = await asyncio.to_thread(handle.read_frame)
            if frame is None:
                return None
        else:
            png_bytes = await asyncio.to_thread(handle.screencap_png, config.device_id)
            if not png_bytes:
                return None  # device unreachable -- honest no-feed

            def _decode():
                arr = np.frombuffer(png_bytes, dtype=np.uint8)
                return cv2.imdecode(arr, cv2.IMREAD_COLOR)

            frame = await asyncio.to_thread(_decode)
            if frame is None:
                raise RuntimeError("adb screencap returned undecodable PNG data")

        return CapturedFrame(
            frame_id=self._frame_counter.get(config.source_id, 0) + 1,
            source_id=config.source_id,
            source_type=FrameSource.ADB_DEVICE,
            image=frame,
            width=frame.shape[1],
            height=frame.shape[0],
            channels=frame.shape[2] if len(frame.shape) > 2 else 1,
            device_id=config.device_id,
            team_id=config.team_id,
        )

    def _process_frame(self, frame: CapturedFrame, config: StreamConfig) -> CapturedFrame:
        """Process captured frame (resize, convert, ROI)"""

        if frame.image is None:
            return frame

        # Extract ROI if specified
        if config.roi:
            roi_frame = frame.get_roi(*config.roi)
            if roi_frame is not None:
                frame.image = roi_frame
                frame.width = roi_frame.shape[1]
                frame.height = roi_frame.shape[0]

        # Resize if needed
        if config.enable_resize:
            frame.resize(config.target_width, config.target_height)

        # Convert to grayscale if needed
        if config.enable_grayscale:
            frame.to_grayscale()

        return frame

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FRAME RETRIEVAL
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def get_latest_frame(self, source_id: str) -> Optional[CapturedFrame]:
        """Get the most recent frame from a source"""
        buffer = self.frame_buffers.get(source_id)
        if buffer and len(buffer) > 0:
            return buffer[-1]
        return None

    def get_frame_buffer(self, source_id: str) -> List[CapturedFrame]:
        """Get all buffered frames for a source"""
        buffer = self.frame_buffers.get(source_id)
        if buffer:
            return list(buffer)
        return []

    def get_all_latest_frames(self) -> Dict[str, CapturedFrame]:
        """Get latest frame from all sources"""
        frames = {}
        for source_id in self.captures:
            frame = self.get_latest_frame(source_id)
            if frame is not None:
                frames[source_id] = frame
        return frames

    def clear_buffer(self, source_id: str) -> None:
        """Clear frame buffer for a source"""
        buffer = self.frame_buffers.get(source_id)
        if buffer:
            buffer.clear()

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATISTICS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def get_stats(self) -> Dict[str, Any]:
        """Get frame grabber statistics"""
        now = time.time()
        source_stats = {}

        for source_id, config in self.captures.items():
            buffer = self.frame_buffers.get(source_id, deque())
            state = self._states.get(source_id, SourceState.STOPPED)

            source_stats[source_id] = {
                "type": config.source_type.value,
                "state": state.value,
                "frames_captured": self._frame_counter.get(source_id, 0),
                "buffer_size": len(buffer),
                "buffer_max": config.buffer_size,
                "errors": self._capture_errors.get(source_id, 0),
                "fps_target": config.target_fps,
                "is_active": source_id in self.capture_tasks
                and not self.capture_tasks[source_id].done(),
                "last_frame_age_s": round(
                    now - self._last_frame_time.get(source_id, 0.0), 2
                ) if self._last_frame_time.get(source_id) else None,
                "last_error": self._last_error.get(source_id, ""),
                # Phase 22 (W3, M1): the health score, live at last —
                # plus this feed's latency-sync report (±500 ms frame).
                "health": self.health.get_feed_health(source_id),
                "sync": self.sync.feed_report(source_id),
            }

        return {
            "total_frames_captured": self._total_frames_captured,
            "active_sources": sum(
                1 for s in source_stats.values() if s["is_active"]
            ),
            "total_sources": len(self.captures),
            "sources": source_stats,
            # Phase 22 (W3): fleet health summary + alignment report.
            "feed_health": self.health.get_stats(),
            "latency_sync": self.sync.alignment_report(),
        }

    def reset(self) -> None:
        """Reset frame grabber"""
        for source_id in list(self._handles.keys()):
            self._close_handle(source_id)
        for buffer in self.frame_buffers.values():
            buffer.clear()
        self._frame_counter.clear()
        self._total_frames_captured = 0
        self.health.reset()
        self.sync.reset()
        for source_id in self.captures:
            cfg = self.captures[source_id]
            self.health.register_feed(source_id, expected_fps=float(cfg.target_fps))
            self.sync.register_feed(source_id)
        self._log.info("Frame grabber reset")
