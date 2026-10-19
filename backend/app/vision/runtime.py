# backend/app/vision/runtime.py
"""
AEGIS AI - Vision Pipeline Runtime Service (Phase 14 W4 integration).

Runs the Phase 14 capture -> detect pipeline as a background service and
pushes detector output into the game-feed adapter as NormalizedEvents:

    FrameGrabber (adb_poll / scrcpy / mss)
        -> FramePipeline (preprocess + parallel detector dispatch,
           per-detector failure isolation, honest no-feed skipping)
        -> KillFeedDetector  -> NormalizedEvent kill/knock
        -> ZoneTimerReader    -> NormalizedEvent zone_update (timer-reset edge)
        -> AliveCountReader   -> NormalizedEvent alive_count (on change only)
        -> TeamStatusReader   -> NormalizedEvent team_status (on change only)
        -> ingest(event)      (the VisionFeedAdapter.ingest -> event bus)

Honest-degradation guarantees (inherited from W1/W2/W3, preserved here):
- No feed (no device, capture backend "none", dropped frames) -> the loop
  idles, emits NOTHING, and reports the no-feed state. Never fake frames,
  never fake events.
- OCR unclear / region unreadable -> the reader reports UNKNOWN and emits
  nothing. The kill-feed detector skips low-confidence lines.
- One detector raising never kills the loop: the FramePipeline isolates
  detector failures per frame, and each ingest is individually guarded.

Color note: the pipeline's CaptureConfig defaults to grayscale
preprocessing, but the Phase 14 detectors need COLOR frames (the team
reader samples HSV, the OCR readers expect BGR). This service therefore
forces grayscale=False on its pipeline config copy and logs that it did.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import threading
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

import structlog

from .capture.config import CaptureConfig, load_capture_config
from .capture.frame_grabber import (
    CapturedFrame,
    FrameGrabber,
    FrameSource,
    SourceState,
    StreamConfig,
)
from .capture.pipeline import FramePipeline, PipelineResult
from .detectors.hud import (
    AliveCountReader,
    TeamStatusReader,
    ZoneTimerReader,
)
from .detectors.killfeed import KillFeedDetector

log = structlog.get_logger("aegis.vision.runtime")

# NormalizedEvent / FeedEventType are imported lazily so this module stays
# importable in minimal environments (schemas.py only needs pydantic, but
# importing app.services at module top would drag SQLAlchemy via
# app.services.__init__ -> match_service).
_schemas = None
_schemas_lock = threading.Lock()


def _default_killfeed_detector():
    """
    Phase-15 wiring: when VISION_MODEL_PATH points at a valid kill-feed
    ONNX model, the detector consults the trained model FIRST for entry
    localisation (hybrid trained+OCR); otherwise it is the plain Phase-14
    OCR heuristic — identical behaviour to before. The import is lazy so
    the vision pipeline never pays for the ml stack at import time.
    """
    from app.ml.vision_wiring import build_killfeed_detector

    return build_killfeed_detector()


def _feed_schemas():
    global _schemas
    if _schemas is not None:
        return _schemas
    # Serialized: two detector worker threads racing the first import can
    # otherwise deadlock on the module import lock. (The service __init__
    # also warms this import eagerly, so the race should never happen here.)
    with _schemas_lock:
        if _schemas is not None:
            return _schemas
        try:
            from app.services.feed_adapter import schemas as mod
        except ImportError:  # pragma: no cover - repo-layout guard
            import importlib.util
            import pathlib
            import sys

            path = (
                pathlib.Path(__file__).resolve().parent.parent
                / "services"
                / "feed_adapter"
                / "schemas.py"
            )
            name = "aegis_feed_adapter_schemas_rt"
            spec = importlib.util.spec_from_file_location(name, str(path))
            if spec is None or spec.loader is None:  # pragma: no cover
                raise
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        _schemas = mod
    return _schemas


#: Async callable the service uses to publish one NormalizedEvent.
#: (The VisionFeedAdapter passes its own ingest().)
IngestFn = Callable[[Any], Awaitable[str]]


class VisionCaptureService:
    """
    Background capture -> detect -> ingest loop.

    Parameters
    ----------
    ingest:
        Async callable receiving one NormalizedEvent and returning the bus
        event id. Raising is allowed -- the loop logs and continues.
    config:
        CaptureConfig (env-loaded when omitted).
    match_id:
        Attached to every emitted event (env AEGIS_VISION_MATCH_ID default).
    frame_provider:
        Optional zero-arg callable returning the latest CapturedFrame (or
        None). Defaults to the owned FrameGrabber's latest frame. Tests
        inject synthetic frames here.
    killfeed / alive / zone / team:
        Optional pre-built detector instances (e.g. with a pinned OCR
        engine). Defaults are constructed here.
    """

    SOURCE_ID = "vision0"

    def __init__(
        self,
        ingest: IngestFn,
        config: Optional[CaptureConfig] = None,
        match_id: Optional[str] = None,
        frame_provider: Optional[Callable[[], Optional[CapturedFrame]]] = None,
        killfeed: Optional[KillFeedDetector] = None,
        alive: Optional[AliveCountReader] = None,
        zone: Optional[ZoneTimerReader] = None,
        team: Optional[TeamStatusReader] = None,
    ) -> None:
        self._ingest = ingest
        base_config = config or load_capture_config()
        # The Phase 14 detectors need COLOR frames; never let an env default
        # silently grayscale them (the team reader's HSV sampling and the
        # OCR readers' BGR expectations would break).
        self.config = dataclasses.replace(base_config, grayscale=False)
        if base_config.grayscale:
            log.info(
                "vision_pipeline_forced_color",
                reason="Phase 14 detectors require BGR frames; "
                "AEGIS_CAPTURE_GRAYSCALE is ignored by this service",
            )
        self._match_id = match_id or os.getenv("AEGIS_VISION_MATCH_ID", "").strip() or None

        # Warm the lazy feed-adapter schema imports NOW, in this thread.
        # The detectors import schemas lazily on first use; if two worker
        # threads race that first import, CPython's module import lock can
        # deadlock ("deadlock detected by _ModuleLock"). Warming here --
        # before any detector thread runs -- makes later lazy imports
        # plain sys.modules lookups.
        _feed_schemas()

        self._grabber = FrameGrabber()
        self._pipeline = FramePipeline(config=self.config)
        self._killfeed = killfeed or _default_killfeed_detector()
        self._alive = alive or AliveCountReader()
        self._zone = zone or ZoneTimerReader()
        self._team = team or TeamStatusReader()
        for det in (self._killfeed, self._alive, self._zone, self._team):
            self._pipeline.register_detector(det)

        self._frame_provider = frame_provider or (
            lambda: self._grabber.get_latest_frame(self.SOURCE_ID)
        )
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._source_added = False

        # Stats (honest counters, no sampling tricks).
        self._started_at: Optional[float] = None
        self._frames_seen = 0
        self._frames_no_feed = 0
        self._events_emitted = 0
        self._ingest_errors = 0
        self._last_error = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Add the capture source, start grabbing, spawn the loop."""
        if self._running:
            log.warning("vision_service_already_running")
            return
        if self._add_source_from_config():
            # start_capture spawns the grabber's resilient loop; a missing
            # device / bad handle becomes NO_FEED + backoff inside the
            # grabber, never an exception here.
            try:
                await self._grabber.start_capture(self.SOURCE_ID)
            except Exception as exc:  # pragma: no cover - defensive
                self._last_error = str(exc)[:300]
                log.error("vision_capture_start_failed", error=self._last_error)
        self._running = True
        self._started_at = time.time()
        self._task = asyncio.create_task(self._loop(), name="aegis-vision-loop")
        log.info(
            "vision_service_started",
            backend=self.config.backend,
            target_fps=self.config.target_fps,
            match_id=self._match_id,
        )

    async def stop(self) -> None:
        """Stop the loop and the capture source. Idempotent."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        try:
            await self._grabber.stop_all()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("vision_grabber_stop_failed", error=str(exc)[:200])
        try:
            self._pipeline.shutdown()
        except Exception:  # pragma: no cover - defensive
            pass
        log.info("vision_service_stopped")

    @property
    def running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Source wiring
    # ------------------------------------------------------------------

    def _add_source_from_config(self) -> bool:
        """Add the FrameGrabber source matching the capture backend.

        Returns True when a source was added (False for backend "none").
        """
        backend = (self.config.backend or "none").strip().lower()
        if backend == "none":
            # Honest "capture disabled": no source, the loop idles and the
            # status reports no-feed. Never synthesize frames.
            log.warning(
                "vision_capture_disabled",
                hint="set AEGIS_CAPTURE_BACKEND=adb_poll|scrcpy|mss",
            )
            return False
        if backend == "mss":
            source_type = FrameSource.SCREEN_CAPTURE
        else:  # adb_poll | scrcpy (anything else falls back to adb_poll)
            source_type = FrameSource.ADB_DEVICE
            if backend not in ("adb_poll", "scrcpy"):
                log.warning("vision_unknown_backend", backend=backend,
                            fallback="adb_poll")
                backend = "adb_poll"
        cfg = StreamConfig(
            source_id=self.SOURCE_ID,
            source_type=source_type,
            target_fps=max(1, int(self.config.target_fps)),
            capture_backend=backend,
            device_id=self.config.adb_serial,
            adb_path=self.config.adb_path,
            scrcpy_path=self.config.scrcpy_path,
            scrcpy_max_size=self.config.scrcpy_max_size,
            scrcpy_max_fps=self.config.scrcpy_max_fps,
            reconnect_attempts=self.config.max_reconnect_attempts,
            reconnect_delay=self.config.reconnect_delay_s,
            feed_timeout_s=self.config.feed_timeout_s,
        )
        self._grabber.add_source(cfg)
        self._source_added = True
        return True

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        period = 1.0 / max(0.5, float(self.config.target_fps))
        while self._running:
            loop_start = time.perf_counter()
            try:
                frame = self._frame_provider()
                if frame is None or getattr(frame, "image", None) is None:
                    # Honest no-feed: idle, emit nothing.
                    self._frames_no_feed += 1
                    await asyncio.sleep(0.25)
                    continue
                result = await self._pipeline.process_frame(frame)
                self._frames_seen += 1
                for event in self._extract_events(result):
                    try:
                        await self._ingest(event)
                        self._events_emitted += 1
                    except Exception as exc:
                        # Ingest must never silently vanish AND must never
                        # kill the loop: record loudly, keep going.
                        self._ingest_errors += 1
                        self._last_error = str(exc)[:300]
                        log.error(
                            "vision_ingest_failed",
                            error=self._last_error,
                            event_type=getattr(event, "event_type", "?"),
                        )
            except asyncio.CancelledError:
                break
            except Exception as exc:  # the loop itself never dies
                self._last_error = str(exc)[:300]
                log.error("vision_loop_error", error=self._last_error)
            elapsed = time.perf_counter() - loop_start
            await asyncio.sleep(max(0.0, period - elapsed))

    # ------------------------------------------------------------------
    # Event extraction: pipeline outcomes -> NormalizedEvent
    # ------------------------------------------------------------------

    def _extract_events(self, result: PipelineResult) -> List[Any]:
        """Pull NormalizedEvents out of one pipeline pass.

        Only SUCCESS outcomes with real detector output produce events;
        failed/skipped outcomes contribute nothing (no guessing).
        """
        schemas = _feed_schemas()
        events: List[Any] = []

        def _extra(detector_name: str) -> Dict[str, Any]:
            oc = result.get(detector_name)
            if oc is None or oc.status != "success" or not oc.result_dict:
                return {}
            return (oc.result_dict.get("extra") or {})

        # --- kill feed: NormalizedEvents already built by the detector ---
        for dumped in _extra(self._killfeed.name).get("events", []):
            try:
                event = schemas.NormalizedEvent(**dumped)
            except Exception as exc:
                log.warning("vision_bad_killfeed_event", error=str(exc)[:200])
                continue
            events.append(self._with_match_id(event))

        # --- zone timer: reset-edge event embedded by detect() ---
        zone_dump = _extra(self._zone.name).get("zone", {}).get("event")
        if zone_dump:
            try:
                events.append(
                    self._with_match_id(schemas.NormalizedEvent(**zone_dump))
                )
            except Exception as exc:
                log.warning("vision_bad_zone_event", error=str(exc)[:200])

        # --- alive count: change-only update -> ALIVE_COUNT event ---
        alive_change = _extra(self._alive.name).get("alive", {}).get("change")
        if alive_change:
            events.append(
                schemas.NormalizedEvent(
                    event_type=schemas.FeedEventType.ALIVE_COUNT,
                    match_id=self._match_id,
                    payload={
                        "players_alive": alive_change.get("players"),
                        "confidence": alive_change.get("confidence"),
                        "ocr_engine": alive_change.get("engine"),
                        "detector": self._alive.name,
                    },
                )
            )

        # --- team status: change-only update -> TEAM_STATUS event ---
        team_change = _extra(self._team.name).get("team", {}).get("change")
        if team_change:
            events.append(
                schemas.NormalizedEvent(
                    event_type=schemas.FeedEventType.TEAM_STATUS,
                    match_id=self._match_id,
                    payload={
                        "slots": team_change.get("slots"),
                        "alive_count": team_change.get("alive_count"),
                        "confidences": team_change.get("confidences"),
                        "detector": self._team.name,
                    },
                )
            )

        return events

    def _with_match_id(self, event: Any) -> Any:
        if self._match_id and not event.match_id:
            event.match_id = self._match_id
        return event

    # ------------------------------------------------------------------
    # Status / diagnostics
    # ------------------------------------------------------------------

    def _ocr_report(self) -> Dict[str, Any]:
        report: Dict[str, Any] = {}
        try:
            report["killfeed_tesseract"] = self._killfeed.ocr_available()
        except Exception:  # pragma: no cover - defensive
            report["killfeed_tesseract"] = False
        for name, reader in (
            ("alive", self._alive),
            ("zone", self._zone),
        ):
            ocr = getattr(reader, "ocr", None)
            report[f"{name}_ocr_engine"] = getattr(ocr, "name", "?")
            try:
                report[f"{name}_ocr_available"] = bool(ocr.available)
            except Exception:  # pragma: no cover - defensive
                report[f"{name}_ocr_available"] = False
        report["team_engine"] = "color_threshold"
        return report

    def status(self) -> Dict[str, Any]:
        """Honest runtime status for operators and GET /feed/vision/status."""
        uptime = (time.time() - self._started_at) if self._started_at else 0.0
        measured_fps = (self._frames_seen / uptime) if uptime > 1e-6 else 0.0
        source_state: Optional[str] = None
        if self._source_added:
            state = self._grabber.get_source_state(self.SOURCE_ID)
            source_state = state.value if isinstance(state, SourceState) else (
                str(state) if state is not None else None
            )
        else:
            source_state = "disabled" if self._running else SourceState.STOPPED.value
        return {
            "running": self._running,
            "backend": self.config.backend,
            "target_fps": self.config.target_fps,
            "measured_fps": round(measured_fps, 2),
            "match_id": self._match_id,
            "capture_source_state": source_state,
            "frames_processed": self._frames_seen,
            "frames_no_feed": self._frames_no_feed,
            "events_emitted": self._events_emitted,
            "ingest_errors": self._ingest_errors,
            "last_error": self._last_error,
            "detectors": self._pipeline.detector_names,
            "pipeline": self._pipeline.get_stats(),
            "ocr": self._ocr_report(),
            "uptime_s": round(uptime, 1),
        }
