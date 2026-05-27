# backend/app/vision/detectors/utility_detector.py
"""
AEGIS AI - Utility Detector
Detects grenade throws, smoke deployment, molotov usage,
and other utility items from the BGMI spectator view.
"""

import time
from typing import List, Tuple
from dataclasses import dataclass, field
import numpy as np
import structlog

from .base_detector import BaseDetector, DetectionResult, DetectionBox, DetectionStatus

logger = structlog.get_logger("aegis.vision.utility")


@dataclass
class UtilityInfo:
    """Utility usage information"""
    event_id: str = ""
    timestamp: float = field(default_factory=time.time)
    
    # Utility type
    utility_type: str = ""  # grenade, smoke, molotov, flash, stun
    
    # Thrower
    player_id: str = ""
    player_name: str = ""
    team_id: str = ""
    
    # Trajectory
    throw_position: Tuple[float, float] = (0.0, 0.0)
    landing_position: Tuple[float, float] = (0.0, 0.0)
    trajectory_points: List[Tuple[float, float]] = field(default_factory=list)
    
    # Effect
    is_exploded: bool = False
    explosion_time: float = 0.0
    affected_players: List[str] = field(default_factory=list)
    damage_dealt: int = 0
    
    # Smoke specific
    smoke_radius: float = 0.0
    smoke_duration: float = 25.0
    
    # Detection
    confidence: float = 0.0
    frame_detected: int = 0


class UtilityDetector(BaseDetector):
    """
    Detects utility item usage in BGMI.
    
    Detects:
    - Grenade throws (explosive, trajectory)
    - Smoke grenades (deployment, coverage area)
    - Molotov cocktails (fire area)
    - Flash/Stun grenades
    
    Uses object detection and motion tracking
    to identify utility usage events.
    """
    
    def __init__(self):
        super().__init__(name="utility_detector")
        
        # Full frame (utility can appear anywhere)
        self.detection_region = DetectionBox(
            x=0.0, y=0.0, width=1.0, height=1.0
        )
        
        # Utility item indicators
        self.utility_indicators = {
            "grenade": {
                "throw_icon": "frag_icon",
                "color_range": ((0, 100, 100), (30, 200, 200)),  # Green-ish
                "explosion_color": ((0, 100, 200), (50, 200, 255)),  # Orange fire
            },
            "smoke": {
                "throw_icon": "smoke_icon",
                "color_range": ((100, 100, 100), (200, 200, 200)),  # Gray/white smoke
                "smoke_color": ((150, 150, 150), (255, 255, 255)),
            },
            "molotov": {
                "throw_icon": "molotov_icon",
                "color_range": ((0, 150, 200), (50, 200, 255)),  # Fire colors
                "fire_color": ((0, 100, 200), (50, 150, 255)),
            },
        }
        
        # Active utilities being tracked
        self.active_smokes: List[UtilityInfo] = []
        self.active_fires: List[UtilityInfo] = []
        
        self._log = logger.bind(detector=self.name)
    
    async def detect(self, frame: np.ndarray, **kwargs) -> DetectionResult:
        """
        Detect utility usage from frame.
        
        Args:
            frame: Full game frame
            **kwargs: known_positions
        """
        
        start_time = time.time()
        h, w = frame.shape[:2]
        
        # Detect all utility types
        detected = []
        
        # Check for explosions (grenades)
        grenade_events = self._detect_explosions(frame)
        detected.extend(grenade_events)
        
        # Check for smoke
        smoke_events = self._detect_smoke(frame)
        detected.extend(smoke_events)
        
        # Check for fire (molotov)
        fire_events = self._detect_fire(frame)
        detected.extend(fire_events)
        
        # Update active utilities
        self._update_active_utilities(detected)
        
        result = DetectionResult(
            detector_name=self.name,
            status=DetectionStatus.SUCCESS if detected else DetectionStatus.NOT_FOUND,
            frame_width=w,
            frame_height=h,
            processing_time_ms=self._time_detection(start_time),
            extra_data={
                "utilities_detected": len(detected),
                "events": [
                    {
                        "type": u.utility_type,
                        "position": u.landing_position,
                        "affected_players": u.affected_players,
                        "damage": u.damage_dealt,
                        "confidence": round(u.confidence, 3),
                    }
                    for u in detected
                ],
                "active_smokes": len(self.active_smokes),
                "active_fires": len(self.active_fires),
            },
        )
        
        self._last_result = result
        return result
    
    def _detect_explosions(self, frame: np.ndarray) -> List[UtilityInfo]:
        """Detect grenade explosions"""
        
        events = []
        
        try:
            import cv2
            
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            
            # Detect explosion colors (bright orange/yellow flash)
            lower = np.array([10, 150, 200], dtype=np.uint8)
            upper = np.array([30, 255, 255], dtype=np.uint8)
            
            mask = cv2.inRange(hsv, lower, upper)
            
            # Find explosion regions
            contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            
            for contour in contours:
                area = cv2.contourArea(contour)
                
                # Filter by size (explosions are medium-sized bright spots)
                if area < 100 or area > 10000:
                    continue
                
                M = cv2.moments(contour)
                if M["m00"] > 0:
                    cx = int(M["m10"] / M["m00"])
                    cy = int(M["m01"] / M["m00"])
                    
                    event = UtilityInfo(
                        event_id=f"explosion_{int(time.time()*1000)}",
                        utility_type="grenade",
                        landing_position=(float(cx), float(cy)),
                        is_exploded=True,
                        explosion_time=time.time(),
                        confidence=0.7,
                    )
                    events.append(event)
            
        except ImportError:
            pass
        except Exception as e:
            self._log.error(f"Explosion detection error: {e}")
        
        return events
    
    def _detect_smoke(self, frame: np.ndarray) -> List[UtilityInfo]:
        """Detect smoke grenades"""
        
        events = []
        
        try:
            import cv2
            
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            
            # Detect smoke (grayish-white clouds)
            lower = np.array([0, 0, 150], dtype=np.uint8)
            upper = np.array([180, 30, 255], dtype=np.uint8)
            
            mask = cv2.inRange(hsv, lower, upper)
            
            # Find large smoke regions
            contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            
            for contour in contours:
                area = cv2.contourArea(contour)
                
                # Smoke clouds are large
                if area < 1000:
                    continue
                
                # Check if this is a new smoke (not already tracked)
                M = cv2.moments(contour)
                if M["m00"] > 0:
                    cx = int(M["m10"] / M["m00"])
                    cy = int(M["m01"] / M["m00"])
                    
                    # Check if near existing smoke
                    is_new = True
                    for active in self.active_smokes:
                        dist = np.sqrt(
                            (cx - active.landing_position[0])**2 +
                            (cy - active.landing_position[1])**2
                        )
                        if dist < 100:  # Within 100px of existing smoke
                            is_new = False
                            break
                    
                    if is_new:
                        event = UtilityInfo(
                            event_id=f"smoke_{int(time.time()*1000)}",
                            utility_type="smoke",
                            landing_position=(float(cx), float(cy)),
                            smoke_radius=np.sqrt(area / np.pi),
                            confidence=0.65,
                        )
                        events.append(event)
            
        except ImportError:
            pass
        except Exception as e:
            self._log.error(f"Smoke detection error: {e}")
        
        return events
    
    def _detect_fire(self, frame: np.ndarray) -> List[UtilityInfo]:
        """Detect molotov fire"""
        
        events = []
        
        try:
            import cv2
            
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            
            # Detect fire (bright orange/red flickering)
            lower = np.array([0, 200, 200], dtype=np.uint8)
            upper = np.array([20, 255, 255], dtype=np.uint8)
            
            mask = cv2.inRange(hsv, lower, upper)
            
            contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            
            for contour in contours:
                area = cv2.contourArea(contour)
                
                if area < 200:
                    continue
                
                M = cv2.moments(contour)
                if M["m00"] > 0:
                    cx = int(M["m10"] / M["m00"])
                    cy = int(M["m01"] / M["m00"])
                    
                    event = UtilityInfo(
                        event_id=f"fire_{int(time.time()*1000)}",
                        utility_type="molotov",
                        landing_position=(float(cx), float(cy)),
                        confidence=0.7,
                    )
                    events.append(event)
            
        except ImportError:
            pass
        except Exception as e:
            self._log.error(f"Fire detection error: {e}")
        
        return events
    
    def _update_active_utilities(self, detected: List[UtilityInfo]) -> None:
        """Update active utilities list"""
        
        current_time = time.time()
        
        # Add new smokes
        for event in detected:
            if event.utility_type == "smoke":
                self.active_smokes.append(event)
            elif event.utility_type == "molotov":
                self.active_fires.append(event)
        
        # Remove expired smokes (25 second duration)
        self.active_smokes = [
            s for s in self.active_smokes
            if current_time - s.timestamp < 30
        ]
        
        # Remove expired fires (8 second duration)
        self.active_fires = [
            f for f in self.active_fires
            if current_time - f.timestamp < 10
        ]
    
    def get_active_smoke_positions(self) -> List[Tuple[float, float]]:
        """Get positions of active smokes"""
        return [s.landing_position for s in self.active_smokes]
    
    def get_detection_regions(self, frame_width: int, frame_height: int) -> List[DetectionBox]:
        """Get full frame detection region"""
        return [self.detection_region]