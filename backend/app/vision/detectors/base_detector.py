# backend/app/vision/detectors/base_detector.py
"""
AEGIS AI - Base Detector
Abstract base class for all computer vision detectors.
Provides common detection interface, result formatting, and utilities.
"""

import time
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import numpy as np
import structlog

logger = structlog.get_logger("aegis.vision.detectors")


class DetectionStatus(str, Enum):
    """Detection result status"""
    SUCCESS = "success"
    LOW_CONFIDENCE = "low_confidence"
    FAILED = "failed"
    NOT_FOUND = "not_found"
    SKIPPED = "skipped"


@dataclass
class DetectionBox:
    """Bounding box for a detection"""
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
    
    def iou(self, other: 'DetectionBox') -> float:
        """Calculate Intersection over Union with another box"""
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
        """Check if box contains a point"""
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2


@dataclass
class DetectionResult:
    """Result from a detector"""
    detector_name: str
    status: DetectionStatus = DetectionStatus.SUCCESS
    timestamp: float = field(default_factory=time.time)
    
    # Detections
    boxes: List[DetectionBox] = field(default_factory=list)
    labels: List[str] = field(default_factory=list)
    confidences: List[float] = field(default_factory=list)
    
    # Metadata
    frame_width: int = 1920
    frame_height: int = 1080
    processing_time_ms: float = 0.0
    source_id: str = ""
    
    # Additional data (detector-specific)
    extra_data: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def count(self) -> int:
        return len(self.boxes)
    
    @property
    def is_successful(self) -> bool:
        return self.status == DetectionStatus.SUCCESS
    
    def get_best_detection(self) -> Optional[DetectionBox]:
        """Get detection with highest confidence"""
        if not self.boxes:
            return None
        best_idx = max(range(len(self.confidences)), key=lambda i: self.confidences[i])
        return self.boxes[best_idx]
    
    def filter_by_confidence(self, threshold: float) -> 'DetectionResult':
        """Filter detections by confidence threshold"""
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
        """Convert to dictionary"""
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


class BaseDetector(ABC):
    """
    Abstract base class for all computer vision detectors.
    
    All detectors must implement:
    - detect(frame) → DetectionResult
    - get_detection_regions() → List[DetectionBox]
    
    Provides:
    - Frame preprocessing
    - Result formatting
    - Performance timing
    - Error handling
    - Configuration management
    """
    
    def __init__(self, name: str = "base_detector"):
        self.name = name
        self.config: Dict[str, Any] = {}
        self._total_detections = 0
        self._total_processing_time = 0.0
        self._last_result: Optional[DetectionResult] = None
        self._log = logger.bind(detector=name)
    
    @abstractmethod
    async def detect(self, frame: np.ndarray, **kwargs) -> DetectionResult:
        """
        Run detection on a frame.
        
        Args:
            frame: Input image (BGR format)
            **kwargs: Additional parameters
            
        Returns:
            DetectionResult with detections
        """
        pass
    
    @abstractmethod
    def get_detection_regions(self, frame_width: int, frame_height: int) -> List[DetectionBox]:
        """
        Get regions of interest for this detector.
        
        Args:
            frame_width: Frame width in pixels
            frame_height: Frame height in pixels
            
        Returns:
            List of DetectionBox regions to scan
        """
        pass
    
    def preprocess(self, frame: np.ndarray) -> np.ndarray:
        """
        Preprocess frame before detection.
        Override in subclass for custom preprocessing.
        """
        return frame
    
    def postprocess(
        self,
        raw_detections: Any,
        frame_width: int,
        frame_height: int,
    ) -> DetectionResult:
        """
        Postprocess raw detections into standard format.
        Override in subclass for custom postprocessing.
        """
        return DetectionResult(
            detector_name=self.name,
            frame_width=frame_width,
            frame_height=frame_height,
        )
    
    def set_config(self, **kwargs) -> None:
        """Update detector configuration"""
        self.config.update(kwargs)
    
    def get_config(self) -> Dict[str, Any]:
        """Get detector configuration"""
        return self.config.copy()
    
    def get_last_result(self) -> Optional[DetectionResult]:
        """Get last detection result"""
        return self._last_result
    
    def get_stats(self) -> Dict[str, Any]:
        """Get detector statistics"""
        avg_time = (
            self._total_processing_time / max(1, self._total_detections)
            if self._total_detections > 0 else 0
        )
        
        return {
            "detector": self.name,
            "total_detections": self._total_detections,
            "avg_processing_time_ms": round(avg_time, 2),
            "last_result": self._last_result.to_dict() if self._last_result else None,
        }
    
    def _time_detection(self, start_time: float) -> float:
        """Record detection timing"""
        elapsed = (time.time() - start_time) * 1000
        self._total_detections += 1
        self._total_processing_time += elapsed
        return elapsed
    
    def reset(self) -> None:
        """Reset detector state"""
        self._total_detections = 0
        self._total_processing_time = 0.0
        self._last_result = None
        self._log.debug(f"Detector reset: {self.name}")