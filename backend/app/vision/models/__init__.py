# backend/app/vision/models/__init__.py
"""
AEGIS AI - Vision Models
Model wrappers for YOLOv8, PaddleOCR, ByteTrack,
and centralized model registry with versioning.
"""

from .yolo_service import YOLOService, YOLOConfig, YOLOResult
from .paddleocr_service import PaddleOCRService, OCRConfig, OCRResult
from .bytetrack_service import ByteTrackService, TrackConfig, TrackResult
from .model_registry import ModelRegistry, ModelEntry, ModelVersion

__all__ = [
    "YOLOService",
    "YOLOConfig", 
    "YOLOResult",
    "PaddleOCRService",
    "OCRConfig",
    "OCRResult",
    "ByteTrackService",
    "TrackConfig",
    "TrackResult",
    "ModelRegistry",
    "ModelEntry",
    "ModelVersion",
]