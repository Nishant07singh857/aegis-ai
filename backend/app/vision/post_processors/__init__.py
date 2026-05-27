# backend/app/vision/post_processors/__init__.py
"""
AEGIS AI - Vision Post Processors
Convert raw detections into structured events,
map screen coordinates to game world coordinates,
bounding box utilities, and confidence filtering.
"""

from .event_extractor import EventExtractor, GameEvent, EventType
from .coordinate_mapper import CoordinateMapper, ScreenToWorld, MapCalibration
from .bbox_utils import BBoxUtils, BBoxOperations
from .confidence_filter import ConfidenceFilter, FilterConfig

__all__ = [
    "EventExtractor",
    "GameEvent", 
    "EventType",
    "CoordinateMapper",
    "ScreenToWorld",
    "MapCalibration",
    "BBoxUtils",
    "BBoxOperations",
    "ConfidenceFilter",
    "FilterConfig",
]