# backend/app/vision/detectors/loot_detector.py
"""
AEGIS AI - Loot Detector
Detects loot crates, death boxes, and item pickups
from the BGMI spectator view.
"""

import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import numpy as np
import structlog

from .base_detector import BaseDetector, DetectionResult, DetectionBox, DetectionStatus

logger = structlog.get_logger("aegis.vision.loot")


@dataclass
class LootInfo:
    """Loot detection information"""
    event_id: str = ""
    timestamp: float = field(default_factory=time.time)
    
    # Loot type
    loot_type: str = ""  # death_box, air_drop, flare_drop, world_loot
    
    # Position
    position_x: float = 0.0
    position_y: float = 0.0
    
    # Content (if identifiable)
    contains_items: List[str] = field(default_factory=list)
    is_looted: bool = False
    
    # Player interaction
    interacting_player: str = ""
    is_being_looted: bool = False
    
    # Visual
    bounding_box: Optional[DetectionBox] = None
    marker_color: Tuple[int, int, int] = (255, 255, 255)
    
    # Timing
    spawned_at: float = 0.0
    despawned_at: Optional[float] = None
    lifetime: float = 0.0
    
    # Detection
    confidence: float = 0.0
    # What the confidence number actually IS: the contour fill ratio
    # (mask pixels / bbox area) of the detected blob on this frame —
    # an evidence quantity, NOT a calibrated probability (never
    # measured in the Phase 23 programme; H20).
    confidence_basis: str = "contour_fill_ratio"


class LootDetector(BaseDetector):
    """
    Detects loot-related objects in BGMI.
    
    Detects:
    - Death crates (player death boxes)
    - Air drops (supply crates)
    - Flare gun drops
    - World loot items
    
    Uses color detection and template matching
    to identify loot objects.
    """
    
    def __init__(self):
        super().__init__(name="loot_detector")
        
        # Full frame detection
        self.detection_region = DetectionBox(
            x=0.0, y=0.0, width=1.0, height=1.0
        )
        
        # Death box visual properties
        self.death_box_color = (0, 200, 100)  # Green glow
        self.death_box_size_range = (20, 80)   # Pixel size range
        
        # Air drop visual properties
        self.air_drop_color = (255, 200, 0)    # Yellow/Red smoke
        self.air_drop_size_range = (30, 100)
        
        # Active loot objects
        self.active_objects: Dict[str, LootInfo] = {}
        self.loot_history: List[LootInfo] = []
        
        self._log = logger.bind(detector=self.name)
    
    async def detect(self, frame: np.ndarray, **kwargs) -> DetectionResult:
        """
        Detect loot objects from frame.
        
        Args:
            frame: Full game frame
            **kwargs: known_positions
        """
        
        start_time = time.time()
        h, w = frame.shape[:2]
        
        # Detect death boxes
        death_boxes = self._detect_death_boxes(frame)
        
        # Detect air drops
        air_drops = self._detect_air_drops(frame)
        
        # Update active objects
        all_detected = death_boxes + air_drops
        self._update_objects(all_detected)
        
        result = DetectionResult(
            detector_name=self.name,
            status=DetectionStatus.SUCCESS if all_detected else DetectionStatus.NOT_FOUND,
            frame_width=w,
            frame_height=h,
            processing_time_ms=self._time_detection(start_time),
            extra_data={
                "loot_detected": len(all_detected),
                "death_boxes": len(death_boxes),
                "air_drops": len(air_drops),
                "objects": [
                    {
                        "type": obj.loot_type,
                        "position": (round(obj.position_x, 1), round(obj.position_y, 1)),
                        "is_looted": obj.is_looted,
                        "lifetime": round(obj.lifetime, 1),
                        "confidence": round(obj.confidence, 3),
                        "confidence_basis": obj.confidence_basis,
                    }
                    for obj in all_detected
                ],
            },
        )
        
        self._last_result = result
        return result
    
    def _detect_death_boxes(self, frame: np.ndarray) -> List[LootInfo]:
        """Detect death crates (green glowing boxes)"""
        
        boxes = []
        
        try:
            import cv2
            
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            
            # Detect green glow of death boxes
            lower = np.array([35, 100, 100], dtype=np.uint8)
            upper = np.array([85, 255, 255], dtype=np.uint8)
            
            mask = cv2.inRange(hsv, lower, upper)
            
            # Find bright green spots
            contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            
            for contour in contours:
                area = cv2.contourArea(contour)
                
                # Death boxes are small-medium bright spots
                if area < self.death_box_size_range[0] or area > self.death_box_size_range[1] * 5:
                    continue
                
                M = cv2.moments(contour)
                if M["m00"] > 0:
                    cx = int(M["m10"] / M["m00"])
                    cy = int(M["m01"] / M["m00"])
                    
                    x, y, bw, bh = cv2.boundingRect(contour)

                    # Confidence derived from evidence: fill ratio of
                    # the colour mask inside the bbox (solid glow blob
                    # high, fragmented smear low) — not a constant.
                    blob_roi = mask[y:y + bh, x:x + bw]
                    fill_ratio = (
                        float(cv2.countNonZero(blob_roi)) / float(bw * bh)
                        if bw > 0 and bh > 0 else 0.0
                    )

                    box = LootInfo(
                        event_id=f"deathbox_{int(time.time()*1000)}_{cx}_{cy}",
                        loot_type="death_box",
                        position_x=float(cx),
                        position_y=float(cy),
                        bounding_box=DetectionBox(
                            x=float(x), y=float(y),
                            width=float(bw), height=float(bh),
                        ),
                        marker_color=(0, 200, 100),
                        spawned_at=time.time(),
                        confidence=round(fill_ratio, 3),
                    )
                    boxes.append(box)
            
        except ImportError:
            pass
        except Exception as e:
            self._log.error(f"Death box detection error: {e}")
        
        return boxes
    
    def _detect_air_drops(self, frame: np.ndarray) -> List[LootInfo]:
        """Detect air drop crates (red/yellow smoke)"""
        
        drops = []
        
        try:
            import cv2
            
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            
            # Detect red/yellow smoke of air drops
            # Red smoke
            lower_red1 = np.array([0, 100, 100], dtype=np.uint8)
            upper_red1 = np.array([10, 255, 255], dtype=np.uint8)
            lower_red2 = np.array([160, 100, 100], dtype=np.uint8)
            upper_red2 = np.array([180, 255, 255], dtype=np.uint8)
            
            mask_red1 = cv2.inRange(hsv, lower_red1, upper_red1)
            mask_red2 = cv2.inRange(hsv, lower_red2, upper_red2)
            mask_red = cv2.bitwise_or(mask_red1, mask_red2)
            
            # Yellow smoke
            lower_yellow = np.array([20, 100, 100], dtype=np.uint8)
            upper_yellow = np.array([35, 255, 255], dtype=np.uint8)
            mask_yellow = cv2.inRange(hsv, lower_yellow, upper_yellow)
            
            # Combine
            mask = cv2.bitwise_or(mask_red, mask_yellow)
            
            # Find large smoke regions
            contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            
            for contour in contours:
                area = cv2.contourArea(contour)
                
                # Air drop smoke is large
                if area < 200:
                    continue
                
                M = cv2.moments(contour)
                if M["m00"] > 0:
                    cx = int(M["m10"] / M["m00"])
                    cy = int(M["m01"] / M["m00"])

                    # Confidence derived from evidence: fill ratio of
                    # the smoke colour mask inside the bbox.
                    x, y, bw, bh = cv2.boundingRect(contour)
                    blob_roi = mask[y:y + bh, x:x + bw]
                    fill_ratio = (
                        float(cv2.countNonZero(blob_roi)) / float(bw * bh)
                        if bw > 0 and bh > 0 else 0.0
                    )

                    drop = LootInfo(
                        event_id=f"airdrop_{int(time.time()*1000)}_{cx}_{cy}",
                        loot_type="air_drop",
                        position_x=float(cx),
                        position_y=float(cy),
                        marker_color=(255, 200, 0),
                        spawned_at=time.time(),
                        confidence=round(fill_ratio, 3),
                    )
                    drops.append(drop)
            
        except ImportError:
            pass
        except Exception as e:
            self._log.error(f"Air drop detection error: {e}")
        
        return drops
    
    def _update_objects(self, detected: List[LootInfo]) -> None:
        """Update active loot objects"""
        
        current_time = time.time()
        
        for new_obj in detected:
            matched = False
            
            for obj_id, existing in list(self.active_objects.items()):
                # Check if same object (within 50px)
                dist = np.sqrt(
                    (new_obj.position_x - existing.position_x)**2 +
                    (new_obj.position_y - existing.position_y)**2
                )
                
                if dist < 50:
                    # Update existing
                    existing.position_x = new_obj.position_x
                    existing.position_y = new_obj.position_y
                    existing.timestamp = current_time
                    existing.lifetime = current_time - existing.spawned_at
                    matched = True
                    break
            
            if not matched:
                # New object
                self.active_objects[new_obj.event_id] = new_obj
                self.loot_history.append(new_obj)
        
        # Remove expired objects (not seen for 10 seconds)
        expired = []
        for obj_id, obj in self.active_objects.items():
            if current_time - obj.timestamp > 10.0:
                obj.despawned_at = current_time
                expired.append(obj_id)
        
        for obj_id in expired:
            del self.active_objects[obj_id]
    
    def get_active_loot(self) -> List[LootInfo]:
        """Get currently visible loot objects"""
        return list(self.active_objects.values())
    
    def get_detection_regions(self, frame_width: int, frame_height: int) -> List[DetectionBox]:
        """Get full frame detection region"""
        return [self.detection_region]