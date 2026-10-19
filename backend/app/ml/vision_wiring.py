# backend/app/ml/vision_wiring.py
"""
AEGIS AI - Vision Wiring (Phase 15, W1)

Connects ``VISION_MODEL_PATH`` (see ``app.ml.registry``) to the live
vision pipeline (``app.vision``):

    trained model present  →  consulted FIRST
    trained model absent   →  pure Phase-14 heuristic (today's behavior,
                              zero change)
    everything fails       →  explicit "unknown"/empty result, never a
                              silent empty and never a guessed detection

Two mechanisms, for two different detector kinds:

1. ``build_chained_detector(name, heuristic)`` — generic
   trained → heuristic → unknown chain (``ChainedDetector``) for
   detectors whose trained and heuristic versions speak the SAME
   language (e.g. knock_detector: both emit boxes). Drop-in wherever a
   detector is used.

2. ``build_killfeed_detector()`` — the kill-feed special case. The
   Phase-15 kill-feed model is a REGION LOCALISER
   (``killfeed_panel`` / ``killfeed_entry`` boxes); the Phase-14
   ``KillFeedDetector`` is a NAME READER (Tesseract OCR). A pure chain
   would be wrong here: a trained box carries no killer/victim names,
   so "trained wins" would SUPPRESS the OCR events the broadcast
   needs. Instead the hybrid consults the trained model FIRST for
   entry localisation, then reads each entry with the SAME OCR
   machinery as the heuristic:

       trained model finds entry boxes
         → OCR each entry crop → named events (boxes + events kept)
       trained model absent / raises / finds nothing / crops unreadable
         → the plain heuristic whole-region OCR parse (unchanged)

   Either way the returned object is a ``KillFeedDetector`` (same
   ``name``, same ``detect()`` contract, same ``extra_data["events"]``
   NormalizedEvents), so ``vision/runtime.py`` needs no contract
   changes.

No top-level ``app.vision`` imports anywhere in this module — the
vision package is imported lazily inside the builders, so importing
``app.ml.vision_wiring`` never pulls in the vision stack.

Honest limits:
    - Until a real killfeed.onnx exists AND its entry recall is
      measured high on real footage, the whole-region heuristic
      fallback stays active. The hybrid never trusts the model
      blindly: entries the model misses are still found by the
      fallback parse whenever the per-entry OCR yields nothing.
    - No accuracy claims. Whether the trained link helps is decided
      by ``evaluate.py`` numbers, not by this wiring.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import structlog

from .registry import get_registry
from .trained_detector import KILLFEED_DEFAULT_CLASSES

logger = structlog.get_logger("aegis.ml.vision_wiring")

#: Class the kill-feed model emits for one kill row; the OCR crop target.
KILLFEED_ENTRY_CLASS = KILLFEED_DEFAULT_CLASSES[1]  # "killfeed_entry"

_UNSET = object()  # sentinel: "caller did not pass trained="


# ---------------------------------------------------------------------------
# 1. Generic trained → heuristic → unknown chain
# ---------------------------------------------------------------------------

def build_chained_detector(name: str, heuristic: Any, **overrides: Any) -> Any:
    """
    Build a ``ChainedDetector`` for ``name`` when ``VISION_MODEL_PATH``
    points at a usable model; otherwise return ``heuristic`` unchanged.

    Args:
        name: registry detector name (see ``MODEL_DETECTOR_MAP``).
        heuristic: the Phase-14 heuristic detector (duck-typed).
        overrides: passed to ``registry.get_detector`` (e.g. conf_threshold).

    Returns:
        ChainedDetector(trained → heuristic → unknown) if a trained
        detector could be built, else the heuristic as-is.
    """
    from .fallback_chain import ChainedDetector

    trained = get_registry().get_detector(name, **overrides)
    if trained is None:
        logger.info(
            "chained_detector_heuristic_only",
            name=name,
            reason="no trained model (VISION_MODEL_PATH unset/missing) "
                   "or unknown detector name",
        )
        return heuristic
    logger.info("chained_detector_trained_first", name=name)
    return ChainedDetector(name=name, trained=trained, heuristic=heuristic)


# ---------------------------------------------------------------------------
# 2. Kill-feed hybrid: trained model localises, OCR reads
# ---------------------------------------------------------------------------

def build_killfeed_detector(
    name: str = "killfeed",
    trained: Any = _UNSET,
    **config: Any,
) -> Any:
    """
    Build the kill-feed detector the vision pipeline should use.

    - ``trained`` left unset (normal call): consult the registry —
      a valid ``VISION_MODEL_PATH`` yields the hybrid
      (trained-first), otherwise the plain heuristic ``KillFeedDetector``.
    - ``trained=None``: force the plain heuristic (tests, ablations).
    - ``trained=<detector>``: inject a trained detector directly (tests).

    Returns a ``KillFeedDetector`` (or subclass) in all cases — the
    ``vision/runtime.py`` contract (``.name``, ``async detect()``,
    ``extra_data["events"]``) is unchanged.
    """
    from app.vision.detectors.killfeed import KillFeedDetector  # lazy: no import cycle

    if trained is _UNSET:
        trained = get_registry().get_detector("killfeed")
    if trained is None:
        logger.info(
            "killfeed_detector_heuristic_only",
            reason="no trained killfeed model — pure OCR heuristic",
        )
        return KillFeedDetector(name=name, **config)
    logger.info("killfeed_detector_hybrid", reason="trained model consulted first")
    return _make_hybrid_detector(KillFeedDetector, name, trained, config)


def _make_hybrid_detector(base_cls, name: str, trained: Any,
                          config: Dict[str, Any]):
    """Define (and instantiate) the trained-first kill-feed hybrid."""

    class HybridKillFeedDetector(base_cls):
        """
        KillFeedDetector that consults the trained YOLOv8 kill-feed
        model FIRST for entry localisation, then OCR-reads each entry.

        Behaviour:
            1. trained.detect(frame) → ``killfeed_entry`` boxes.
            2. each entry box (padded, clamped) is OCR'd with the
               inherited ``_ocr_region_to_events`` — the same parsing,
               confidence gating and dedup as the heuristic.
            3. ≥1 named event from entry crops → result with entry
               boxes + NormalizedEvents (``chain_source`` recorded).
            4. model missing / raised / found nothing / crops
               unreadable → the plain heuristic whole-region parse.
        """

        #: padding added around each entry box before OCR (fraction of
        #: the box's own width/height).
        entry_pad_fraction: float = 0.15
        #: crops smaller than this are not worth OCR'ing.
        entry_min_px: int = 16

        def __init__(self) -> None:
            super().__init__(name=name, **config)
            self._trained = trained
            self._trained_failures = 0

        # -- duck-type surface ------------------------------------------------
        @property
        def trained_available(self) -> bool:
            return self._trained is not None

        def get_stats(self) -> Dict[str, Any]:
            stats = super().get_stats()
            stats["chain"] = "hybrid_trained_first"
            stats["trained_available"] = self.trained_available
            stats["trained_failures"] = self._trained_failures
            try:
                stats["trained"] = (
                    self._trained.get_stats() if self._trained is not None else None
                )
            except Exception:
                stats["trained"] = "unavailable"
            return stats

        # -- detection ---------------------------------------------------------
        async def detect(self, frame, **kwargs):
            started = time.time()
            match_id = kwargs.get("match_id")
            now = kwargs.get("now") or time.time()

            if self._trained is not None and frame is not None:
                try:
                    mresult = await self._trained.detect(frame, **kwargs)
                except Exception as exc:
                    self._trained_failures += 1
                    logger.warning(
                        "hybrid_trained_failed",
                        error=str(exc)[:200],
                        action="heuristic_fallback",
                    )
                    mresult = None
                if mresult is not None:
                    entries = [
                        b for b in (mresult.boxes or [])
                        if getattr(b, "label", "") == KILLFEED_ENTRY_CLASS
                    ]
                    per_entry = self._read_entry_crops(frame, entries, match_id, now)
                    if per_entry:
                        return self._entry_events_result(
                            frame, per_entry, started, mresult
                        )
                    logger.debug(
                        "hybrid_no_entry_events",
                        entries=len(entries),
                        action="heuristic_fallback",
                    )

            # Heuristic fallback: model absent / failed / found nothing /
            # entry crops yielded no named events.
            return await super().detect(frame, **kwargs)

        # -- internals ----------------------------------------------------------
        def _read_entry_crops(
            self, frame, entries: List[Any], match_id: Optional[str], now: float
        ) -> List[tuple]:
            """OCR each entry box crop → [(entry_box, [NormalizedEvent])]."""
            out = []
            for box in entries:
                crop = self._crop_entry(frame, box)
                if crop is None:
                    continue
                events = self._ocr_region_to_events(crop, match_id, now)
                if events:
                    out.append((box, events))
            return out

        def _crop_entry(self, frame, box):
            """Entry box → padded, frame-clamped BGR crop (None if degenerate)."""
            h, w = frame.shape[0], frame.shape[1]
            pad_w = float(box.width) * self.entry_pad_fraction
            pad_h = float(box.height) * self.entry_pad_fraction
            x1 = max(0, int(box.x - pad_w))
            y1 = max(0, int(box.y - pad_h))
            x2 = min(w, int(box.x + box.width + pad_w))
            y2 = min(h, int(box.y + box.height + pad_h))
            if x2 - x1 < self.entry_min_px or y2 - y1 < self.entry_min_px:
                return None
            return frame[y1:y2, x1:x2]

        def _entry_events_result(self, frame, per_entry, started, mresult):
            """Vision DetectionResult from entry crops: entry boxes + events."""
            from app.vision.detectors.base_detector import (  # lazy
                DetectionBox,
                DetectionResult,
                DetectionStatus,
            )

            h, w = frame.shape[0], frame.shape[1]
            boxes, labels, confs, dumped = [], [], [], []
            for box, events in per_entry:
                for ev in events:
                    boxes.append(
                        DetectionBox(
                            x=float(box.x), y=float(box.y),
                            width=float(box.width), height=float(box.height),
                            confidence=float(getattr(box, "confidence", 1.0)),
                            label=str(getattr(box, "label", "")),
                        )
                    )
                    labels.append(str(getattr(ev, "event_type", "knock")))
                    try:
                        confs.append(
                            float(ev.payload.get("ocr_confidence", 0.0)) / 100.0
                        )
                    except Exception:
                        confs.append(0.0)
                    dumped.append(ev.model_dump())

            trained_name = str(getattr(mresult, "detector_name", ""))
            result = DetectionResult(
                detector_name=self.name,
                status=DetectionStatus.SUCCESS,
                frame_width=w,
                frame_height=h,
                processing_time_ms=(time.time() - started) * 1000,
                boxes=boxes,
                labels=labels,
                confidences=confs,
                extra_data={
                    "events": dumped,
                    "chain_source": "trained_model+ocr",
                    "trained_detector": trained_name,
                    "entry_boxes_used": len(per_entry),
                    "ocr_available": self.ocr_available(),
                },
            )
            self._last_result = result
            return result

    return HybridKillFeedDetector()


__all__ = [
    "KILLFEED_ENTRY_CLASS",
    "build_chained_detector",
    "build_killfeed_detector",
]
