# backend/app/vision/capture/pipeline.py
"""
AEGIS AI - Vision Frame Pipeline

Phase 14 capture -> detect pipeline.

    capture -> preprocess (grayscale, resize) -> dispatch to detectors

Contract:
- The asyncio event loop is NEVER blocked by this module. All
  CPU-bound work (OpenCV preprocessing, detector inference) runs in a
  thread pool via ``asyncio.to_thread``.
- A failing detector is isolated: its result is marked "failed" and
  every other detector still runs. The pipeline never raises because
  of a detector.
- No fake data: when ``frame`` is None (no feed), every detector result
  is marked "skipped" and ``feed_ok`` is False.

Detector interface: ``BaseDetector`` from
``app.vision.detectors.base_detector`` (async ``detect(frame)``,
``name`` attribute). Any object with the same interface also works.
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import structlog

# NOTE: cv2 at module top on purpose -- OpenCV's import-time initialization
# happens in the main thread rather than lazily inside worker threads.
import cv2

from .config import CaptureConfig, load_capture_config

logger = structlog.get_logger("aegis.vision.pipeline")


@dataclass
class DetectorOutcome:
    """Outcome of one detector on one frame."""
    detector_name: str
    status: str = "success"          # success | failed | skipped
    result: Optional[Any] = None     # detector's own result object
    result_dict: Optional[Dict[str, Any]] = None
    processing_time_ms: float = 0.0
    error: str = ""
    roi_applied: Optional[List[int]] = None  # [x, y, w, h] pixels, if configured


@dataclass
class PipelineResult:
    """Result of one pass of the pipeline over a single frame."""
    frame_id: int
    source_id: str
    timestamp: float = field(default_factory=time.time)
    feed_ok: bool = True
    preprocessed_shape: Optional[tuple] = None
    outcomes: Dict[str, DetectorOutcome] = field(default_factory=dict)
    total_time_ms: float = 0.0

    def get(self, detector_name: str) -> Optional[DetectorOutcome]:
        return self.outcomes.get(detector_name)


class FramePipeline:
    """
    Preprocess frames and dispatch them to registered detectors.

    ``max_workers`` bounds the thread pool; detector dispatch is
    parallel inside that pool.
    """

    def __init__(
        self,
        config: Optional[CaptureConfig] = None,
        max_workers: int = 8,
    ):
        self.config = config or load_capture_config()
        self._detectors: Dict[str, Any] = {}
        # Phase 21: per-detector frame-skip state (see CaptureConfig
        # detector_intervals). Counter counts frames since the detector
        # last ran; a detector is due when the counter reaches its
        # interval. Counters are seeded at registration so different
        # detectors with the same interval do NOT all land on the same
        # frame (stagger by registration index).
        self._since_run: Dict[str, int] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="aegis-pipeline",
        )
        self._frames_processed = 0
        self._frames_skipped_no_feed = 0
        self._log = logger.bind(component="frame_pipeline")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # REGISTRATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def register_detector(self, detector: Any) -> None:
        """Register a detector (must expose ``.name`` and async ``detect``)."""
        name = getattr(detector, "name", None) or detector.__class__.__name__
        interval = self.config.detector_interval(name)
        registration_index = len(self._detectors)
        # Stagger: first run happens after 1 + (index % interval) frames,
        # so e.g. the three interval-5 detectors run on different frames.
        self._since_run[name] = (
            interval - 1 - (registration_index % interval) if interval > 1 else 0
        )
        self._detectors[name] = detector
        self._log.info("Detector registered", detector=name)

    def unregister_detector(self, name: str) -> bool:
        self._since_run.pop(name, None)
        return self._detectors.pop(name, None) is not None

    @property
    def detector_names(self) -> List[str]:
        return list(self._detectors.keys())

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PIPELINE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def process_frame(self, frame: Any) -> PipelineResult:
        """
        Run one frame through preprocess + all detectors.

        ``frame`` is a ``CapturedFrame`` (or any object with ``frame_id``,
        ``source_id`` and ``image`` attributes) or None for "no feed".
        """
        started = time.perf_counter()
        frame_id = getattr(frame, "frame_id", -1)
        source_id = getattr(frame, "source_id", "unknown")

        result = PipelineResult(frame_id=frame_id, source_id=source_id)

        image = getattr(frame, "image", None) if frame is not None else None
        if image is None:
            # Honest no-feed state: skip every detector, never fake one.
            self._frames_skipped_no_feed += 1
            result.feed_ok = False
            for name in self._detectors:
                result.outcomes[name] = DetectorOutcome(
                    detector_name=name, status="skipped"
                )
            result.total_time_ms = (time.perf_counter() - started) * 1000
            return result

        # --- preprocess in the thread pool (never blocks the loop) ---
        try:
            processed = await asyncio.to_thread(
                self._preprocess, image, self.config
            )
        except Exception as exc:
            self._log.error("Preprocess failed", error=str(exc))
            result.feed_ok = False
            for name in self._detectors:
                result.outcomes[name] = DetectorOutcome(
                    detector_name=name, status="failed", error=f"preprocess: {exc}"
                )
            result.total_time_ms = (time.perf_counter() - started) * 1000
            return result

        result.preprocessed_shape = getattr(processed, "shape", None)
        h, w = processed.shape[0], processed.shape[1]

        # --- dispatch to detectors in parallel, each isolated ---
        async def _run_one(name: str, detector: Any) -> DetectorOutcome:
            det_started = time.perf_counter()
            outcome = DetectorOutcome(detector_name=name)
            try:
                roi_px = self.config.roi_to_pixels(name, w, h)
                det_image = processed
                if roi_px is not None:
                    x, y, rw, rh = roi_px
                    det_image = processed[y:y + rh, x:x + rw]
                    outcome.roi_applied = [x, y, rw, rh]

                det_result = await asyncio.to_thread(
                    self._run_detector_sync, detector, det_image
                )
                outcome.result = det_result
                to_dict = getattr(det_result, "to_dict", None)
                outcome.result_dict = to_dict() if callable(to_dict) else None
                outcome.status = "success"
            except Exception as exc:  # isolate: one bad detector never kills the run
                outcome.status = "failed"
                outcome.error = str(exc)[:500]
                self._log.warning(
                    "Detector failed", detector=name, error=str(exc)[:200]
                )
            outcome.processing_time_ms = (time.perf_counter() - det_started) * 1000
            return outcome

        # --- frame-skip gating: not every detector runs every frame ---
        due: Dict[str, Any] = {}
        for name, det in self._detectors.items():
            self._since_run[name] = self._since_run.get(name, 0) + 1
            if self._since_run[name] >= self.config.detector_interval(name):
                self._since_run[name] = 0
                due[name] = det
            else:
                # Honest "not run this frame": outcome is "skipped" with
                # no result, so consumers emit nothing for it (same
                # contract as the no-feed path).
                result.outcomes[name] = DetectorOutcome(
                    detector_name=name, status="skipped"
                )

        outcomes = await asyncio.gather(
            *(_run_one(name, det) for name, det in due.items())
        )
        for outcome in outcomes:
            result.outcomes[outcome.detector_name] = outcome

        self._frames_processed += 1
        result.total_time_ms = (time.perf_counter() - started) * 1000
        return result

    @staticmethod
    def _preprocess(image, config: CaptureConfig):
        """Resize + grayscale. Runs in a worker thread."""
        out = image
        target = (config.preprocess_width, config.preprocess_height)
        if out.shape[1] != target[0] or out.shape[0] != target[1]:
            out = cv2.resize(out, target, interpolation=cv2.INTER_AREA)
        if config.grayscale and len(out.shape) == 3 and out.shape[2] == 3:
            out = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
        return out

    @staticmethod
    def _run_detector_sync(detector: Any, image):
        """
        Run a detector's (possibly async) ``detect`` inside a worker thread.

        Detector ``detect`` methods are declared ``async`` in
        BaseDetector; ``asyncio.run`` gives them a fresh event loop in
        this thread so the main loop is never blocked.
        """
        coro = detector.detect(image)
        if asyncio.iscoroutine(coro):
            return asyncio.run(coro)
        return coro

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATS / LIFECYCLE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def get_stats(self) -> Dict[str, Any]:
        return {
            "detectors": self.detector_names,
            "detector_intervals": {
                name: self.config.detector_interval(name)
                for name in self._detectors
            },
            "frames_processed": self._frames_processed,
            "frames_skipped_no_feed": self._frames_skipped_no_feed,
            "preprocess": {
                "width": self.config.preprocess_width,
                "height": self.config.preprocess_height,
                "grayscale": self.config.grayscale,
            },
        }

    def shutdown(self) -> None:
        """Shut down the worker thread pool."""
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._log.info("Frame pipeline shut down")
