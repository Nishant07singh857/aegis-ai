# backend/app/ml/detection_result.py
"""
AEGIS AI - ML Detection Result Types (self-contained)

Phase 15 (W3 - Model Integration) needs result objects that are
drop-in compatible with the Phase 14 heuristic detectors
(``backend/app/vision/detectors/base_detector.py``) — but the vision
package is being edited concurrently, so these types are defined here
on purpose instead of imported.

Shape contract (identical names/semantics to Phase-14):
    - ``DetectionBox``: x, y, width, height, confidence, label;
      properties x1, y1, x2, y2, center, area;
      methods to_tuple(), to_xyxy(), iou(), contains_point()
    - ``DetectionResult``: detector_name, status, timestamp,
      boxes/labels/confidences, frame_width/height,
      processing_time_ms, source_id, extra_data;
      property count; property is_successful;
      methods get_best_detection(), filter_by_confidence(),
      to_dict()
    - ``DetectionStatus``: str enum with the same values
      (success / low_confidence / failed / not_found / skipped)

Status semantics mirror Phase-14 convention:
    SUCCESS when at least one detection exists, NOT_FOUND when empty.
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger("aegis.ml.detection_result")


class DetectionStatus(str, Enum):
    """Detection result status — same values as Phase-14 vision enum."""

    SUCCESS = "success"
    LOW_CONFIDENCE = "low_confidence"
    FAILED = "failed"
    NOT_FOUND = "not_found"
    SKIPPED = "skipped"


@dataclass
class DetectionBox:
    """Bounding box for a detection (same surface as Phase-14 DetectionBox)."""

    x: float
    y: float
    width: float
    height: float
    confidence: float = 1.0
    label: str = ""

    @property
    def x1(self) -> float:
        return self.x

    @property
    def y1(self) -> float:
        return self.y

    @property
    def x2(self) -> float:
        return self.x + self.width

    @property
    def y2(self) -> float:
        return self.y + self.height

    @property
    def center(self) -> Tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)

    @property
    def area(self) -> float:
        return self.width * self.height

    def to_tuple(self) -> Tuple[float, float, float, float]:
        return (self.x, self.y, self.width, self.height)

    def to_xyxy(self) -> Tuple[float, float, float, float]:
        return (self.x1, self.y1, self.x2, self.y2)

    def iou(self, other: "DetectionBox") -> float:
        """Intersection over Union with another box."""
        x1 = max(self.x1, other.x1)
        y1 = max(self.y1, other.y1)
        x2 = min(self.x2, other.x2)
        y2 = min(self.y2, other.y2)

        if x2 <= x1 or y2 <= y1:
            return 0.0

        intersection = (x2 - x1) * (y2 - y1)
        union = self.area + other.area - intersection
        return intersection / union if union > 0 else 0.0

    def contains_point(self, x: float, y: float) -> bool:
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2


@dataclass
class DetectionResult:
    """Detection result (same surface as Phase-14 DetectionResult)."""

    detector_name: str
    status: DetectionStatus = DetectionStatus.SUCCESS
    timestamp: float = field(default_factory=time.time)

    boxes: List[DetectionBox] = field(default_factory=list)
    labels: List[str] = field(default_factory=list)
    confidences: List[float] = field(default_factory=list)

    frame_width: int = 1920
    frame_height: int = 1080
    processing_time_ms: float = 0.0
    source_id: str = ""

    extra_data: Dict[str, Any] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.boxes)

    @property
    def is_successful(self) -> bool:
        return self.status == DetectionStatus.SUCCESS

    def get_best_detection(self) -> Optional[DetectionBox]:
        """Detection with the highest confidence, or None."""
        if not self.boxes:
            return None
        # Defensive: confidences may be shorter than boxes in hand-built results.
        n = min(len(self.boxes), len(self.confidences))
        if n == 0:
            return self.boxes[0]
        best_idx = max(range(n), key=lambda i: self.confidences[i])
        return self.boxes[best_idx]

    def filter_by_confidence(self, threshold: float) -> "DetectionResult":
        """New result keeping only detections at/above ``threshold``."""
        filtered = DetectionResult(
            detector_name=self.detector_name,
            status=self.status,
            timestamp=self.timestamp,
            frame_width=self.frame_width,
            frame_height=self.frame_height,
            processing_time_ms=self.processing_time_ms,
            source_id=self.source_id,
            extra_data=self.extra_data.copy(),
        )
        for box, label, conf in zip(self.boxes, self.labels, self.confidences):
            if conf >= threshold:
                filtered.boxes.append(box)
                filtered.labels.append(label)
                filtered.confidences.append(conf)
        return filtered

    def to_dict(self) -> Dict[str, Any]:
        """Same dict layout as Phase-14 DetectionResult.to_dict()."""
        return {
            "detector": self.detector_name,
            "status": self.status.value,
            "timestamp": self.timestamp,
            "count": self.count,
            "processing_time_ms": round(self.processing_time_ms, 2),
            "detections": [
                {
                    "box": box.to_tuple(),
                    "label": label,
                    "confidence": round(conf, 3),
                }
                for box, label, conf in zip(self.boxes, self.labels, self.confidences)
            ],
            "extra": self.extra_data,
        }

    @classmethod
    def from_compatible(cls, other: Any, detector_name: Optional[str] = None) -> "DetectionResult":
        """
        Adapt any duck-typed detection result (e.g. a Phase-14
        ``vision`` DetectionResult) into this type without importing it.

        Only attribute names are used — no vision imports anywhere here.
        """
        boxes_raw = list(getattr(other, "boxes", []) or [])
        boxes: List[DetectionBox] = []
        for b in boxes_raw:
            if isinstance(b, DetectionBox):
                boxes.append(b)
            else:
                boxes.append(
                    DetectionBox(
                        x=float(getattr(b, "x", 0.0)),
                        y=float(getattr(b, "y", 0.0)),
                        width=float(getattr(b, "width", 0.0)),
                        height=float(getattr(b, "height", 0.0)),
                        confidence=float(getattr(b, "confidence", 1.0)),
                        label=str(getattr(b, "label", "")),
                    )
                )

        status_raw = getattr(other, "status", None)
        status = status_raw if isinstance(status_raw, DetectionStatus) else DetectionStatus(
            getattr(status_raw, "value", status_raw) or "success"
        )

        return cls(
            detector_name=detector_name or str(getattr(other, "detector_name", "unknown")),
            status=status,
            timestamp=float(getattr(other, "timestamp", time.time())),
            boxes=boxes,
            labels=list(getattr(other, "labels", []) or []),
            confidences=[float(c) for c in (getattr(other, "confidences", []) or [])],
            frame_width=int(getattr(other, "frame_width", 1920)),
            frame_height=int(getattr(other, "frame_height", 1080)),
            processing_time_ms=float(getattr(other, "processing_time_ms", 0.0)),
            source_id=str(getattr(other, "source_id", "")),
            extra_data=dict(getattr(other, "extra_data", {}) or {}),
        )
