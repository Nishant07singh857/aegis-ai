# backend/app/ml/fallback_chain.py
"""
AEGIS AI - Detector Fallback Chain (Phase 15, W3)

``ChainedDetector`` tries the trained model first, then a heuristic
detector (injected, duck-typed), then an explicit "unknown"/empty
result. The chain is NEVER worse than heuristics alone:

    - trained detector missing (registry returned None) → heuristic
    - trained detector raises (bad model, missing deps, bad frame) →
      logged, then heuristic
    - trained detector finds nothing → heuristic still gets a chance
      (model recall is not trusted blindly)
    - heuristic raises too → empty NOT_FOUND "unknown" result, logged

Both detectors speak the same duck-type interface
(``async detect(frame, **kwargs)`` like Phase-14's BaseDetector); the
chain itself does too, so it is a drop-in wherever a detector is used.

"unknown"/empty result: status NOT_FOUND, zero boxes, and
``extra_data`` explains exactly which links failed and why — no
silent empties.
"""

import inspect
import time
from typing import Any, Dict, List, Optional

import structlog

from .detection_result import DetectionBox, DetectionResult, DetectionStatus

logger = structlog.get_logger("aegis.ml.fallback_chain")


async def _run_detect(detector: Any, frame, **kwargs):
    """Call detect() whether it is async (Phase-14) or sync (a stub)."""
    result = detector.detect(frame, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result


class ChainedDetector:
    """
    Trained-model → heuristic → unknown fallback chain.

    Args:
        name: detector name used for stats/empty results.
        trained: trained detector (duck-typed) or None.
        heuristic: heuristic detector (duck-typed) or None.
        require_model_boxes: accept the trained result only when it
            contains at least one box; otherwise still try heuristic.
            Default True — model precision/recall is unverified, so a
            silent model must not suppress the heuristic.
    """

    def __init__(
        self,
        name: str = "chained_detector",
        trained: Any = None,
        heuristic: Any = None,
        require_model_boxes: bool = True,
    ):
        self.name = name
        self.trained = trained
        self.heuristic = heuristic
        self.require_model_boxes = require_model_boxes
        self._last_result: Optional[DetectionResult] = None
        self._last_source: str = "none"
        self._log = logger.bind(detector=name)

    # ── properties ───────────────────────────────────────────────

    @property
    def model_available(self) -> bool:
        return self.trained is not None

    @property
    def heuristic_available(self) -> bool:
        return self.heuristic is not None

    @property
    def last_source(self) -> str:
        """Which link produced the last result: trained|heuristic|none."""
        return self._last_source

    # ── duck-type detector interface ────────────────────────────

    async def detect(self, frame, **kwargs) -> DetectionResult:
        # 1. Trained model
        if self.trained is not None:
            try:
                result = await _run_detect(self.trained, frame, **kwargs)
                if result is not None and (
                    not self.require_model_boxes or getattr(result, "count", 0) > 0
                ):
                    self._last_source = "trained"
                    self._last_result = result
                    self._tag(result, "trained_model")
                    return result
                self._log.debug(
                    "model_found_nothing", action="trying_heuristic"
                )
            except Exception as exc:  # never worse than heuristics alone
                self._log.warning(
                    "model_failed", error=str(exc), action="falling_back_to_heuristic"
                )

        # 2. Heuristic detector
        if self.heuristic is not None:
            try:
                result = await _run_detect(self.heuristic, frame, **kwargs)
                if result is not None:
                    self._last_source = "heuristic"
                    self._last_result = result
                    self._tag(result, "heuristic")
                    return result
                self._log.warning("heuristic_returned_none")
            except Exception as exc:
                self._log.error("heuristic_failed", error=str(exc))

        # 3. Explicit unknown/empty result — never a silent empty.
        self._last_source = "none"
        result = self._unknown_result(frame, kwargs)
        self._last_result = result
        return result

    def get_detection_regions(
        self, frame_width: int, frame_height: int
    ) -> List[DetectionBox]:
        for link in (self.heuristic, self.trained):
            fn = getattr(link, "get_detection_regions", None)
            if link is not None and callable(fn):
                try:
                    return fn(frame_width, frame_height)
                except Exception as exc:
                    self._log.warning("regions_failed", error=str(exc))
        return [
            DetectionBox(
                x=0.0, y=0.0,
                width=float(frame_width), height=float(frame_height),
                confidence=1.0, label="full_frame",
            )
        ]

    def set_config(self, **kwargs: Any) -> None:
        for link in (self.trained, self.heuristic):
            fn = getattr(link, "set_config", None)
            if link is not None and callable(fn):
                fn(**kwargs)

    def get_config(self) -> Dict[str, Any]:
        cfg: Dict[str, Any] = {"chain": self.name, "require_model_boxes": self.require_model_boxes}
        for label, link in (("trained", self.trained), ("heuristic", self.heuristic)):
            fn = getattr(link, "get_config", None)
            if link is not None and callable(fn):
                try:
                    cfg[label] = fn()
                except Exception:
                    cfg[label] = "unavailable"
            else:
                cfg[label] = None
        return cfg

    def get_last_result(self) -> Optional[DetectionResult]:
        return self._last_result

    def get_stats(self) -> Dict[str, Any]:
        stats: Dict[str, Any] = {
            "detector": self.name,
            "backend": "fallback_chain",
            "model_available": self.model_available,
            "heuristic_available": self.heuristic_available,
            "last_source": self._last_source,
        }
        for label, link in (("trained", self.trained), ("heuristic", self.heuristic)):
            fn = getattr(link, "get_stats", None)
            if link is not None and callable(fn):
                try:
                    stats[label] = fn()
                except Exception as exc:
                    stats[label] = {"error": str(exc)}
            else:
                stats[label] = None
        return stats

    def reset(self) -> None:
        for link in (self.trained, self.heuristic):
            fn = getattr(link, "reset", None)
            if link is not None and callable(fn):
                try:
                    fn()
                except Exception:
                    pass
        self._last_result = None
        self._last_source = "none"

    # ── internals ────────────────────────────────────────────────

    @staticmethod
    def _tag(result: Any, source: str) -> None:
        """Record which chain link produced a result (best-effort)."""
        try:
            extra = getattr(result, "extra_data", None)
            if isinstance(extra, dict):
                extra.setdefault("chain_source", source)
        except Exception:
            pass

    def _unknown_result(self, frame, kwargs: Dict[str, Any]) -> DetectionResult:
        reasons = []
        if self.trained is None:
            reasons.append("no trained model configured")
        else:
            reasons.append("trained model failed or found nothing")
        if self.heuristic is None:
            reasons.append("no heuristic detector configured")
        else:
            reasons.append("heuristic detector failed or returned nothing")

        width, height = 1920, 1080
        try:
            height, width = frame.shape[0], frame.shape[1]
        except Exception:
            pass

        self._log.warning("chain_exhausted", reasons=reasons)
        return DetectionResult(
            detector_name=self.name,
            status=DetectionStatus.NOT_FOUND,
            timestamp=time.time(),
            boxes=[],
            labels=[],
            confidences=[],
            frame_width=width,
            frame_height=height,
            processing_time_ms=0.0,
            extra_data={
                "source": "none",
                "chain_source": "none",
                "unknown": True,
                "reasons": reasons,
                "kwargs": {k: str(v) for k, v in kwargs.items()},
            },
        )
