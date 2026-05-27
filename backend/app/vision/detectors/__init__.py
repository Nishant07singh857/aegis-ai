"""
AEGIS AI - Vision Detectors
Computer vision detection modules for BGMI gameplay analysis.
OCR, object detection, tracking, and game state extraction.
"""

from .base_detector import BaseDetector, DetectionResult, DetectionBox
from .kill_feed_ocr import KillFeedOCR, KillFeedEntry
from .hp_bar_detector import HPBarDetector, HPBarInfo
from .minimap_reader import MinimapReader, MinimapData
from .alive_counter import AliveCounter, AliveCountInfo
from .weapon_detector import WeaponDetector, WeaponInfo
from .utility_detector import UtilityDetector, UtilityInfo
from .vehicle_detector import VehicleDetector, VehicleInfo
from .player_tracker import PlayerTracker, TrackedPlayer
from .zone_detector import ZoneDetector, ZoneInfo
from .knock_detector import KnockDetector, KnockInfo
from .healing_detector import HealingDetector, HealingInfo
from .loot_detector import LootDetector, LootInfo

__all__ = [
    "BaseDetector", "DetectionResult", "DetectionBox",
    "KillFeedOCR", "KillFeedEntry",
    "HPBarDetector", "HPBarInfo",
    "MinimapReader", "MinimapData",
    "AliveCounter", "AliveCountInfo",
    "WeaponDetector", "WeaponInfo",
    "UtilityDetector", "UtilityInfo",
    "VehicleDetector", "VehicleInfo",
    "PlayerTracker", "TrackedPlayer",
    "ZoneDetector", "ZoneInfo",
    "KnockDetector", "KnockInfo",
    "HealingDetector", "HealingInfo",
    "LootDetector", "LootInfo",
]