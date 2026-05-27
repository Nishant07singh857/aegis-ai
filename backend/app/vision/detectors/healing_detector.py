# backend/app/vision/detectors/healing_detector.py
"""
AEGIS AI - Healing Detector
Detects healing and boosting actions from BGMI spectator view.
Tracks first aid, bandage, boost usage and healing progress.
"""

import time
from typing import Dict, List
from dataclasses import dataclass, field
import numpy as np
import structlog

from .base_detector import BaseDetector, DetectionResult, DetectionBox, DetectionStatus

logger = structlog.get_logger("aegis.vision.healing")


@dataclass
class HealingInfo:
    """Healing action information"""
    event_id: str = ""
    timestamp: float = field(default_factory=time.time)
    
    # Player
    player_id: str = ""
    player_name: str = ""
    team_id: str = ""
    
    # Healing details
    heal_type: str = ""  # first_aid, bandage, medkit, boost, painkiller
    heal_amount: int = 0
    heal_duration: float = 0.0  # seconds
    heal_progress: float = 0.0  # 0-100%
    
    # Status
    is_active: bool = False
    is_completed: bool = False
    is_cancelled: bool = False
    cancelled_by: str = ""  # "player", "damage", "movement"
    
    # Position
    position_x: float = 0.0
    position_y: float = 0.0
    
    # HUD
    progress_bar_x: float = 0.0
    progress_bar_y: float = 0.0
    progress_bar_width: float = 0.0
    
    # Detection
    confidence: float = 0.0


class HealingDetector(BaseDetector):
    """
    Detects healing actions from BGMI spectator view.
    
    Detects:
    - First aid usage (casts, progress bar)
    - Bandage usage
    - Medkit usage
    - Boost usage (painkiller, energy drink)
    - Healing progress tracking
    - Heal cancellation detection
    
    Uses progress bar detection and HUD analysis.
    """
    
    def __init__(self):
        super().__init__(name="healing_detector")
        
        # Healing progress bar region (center-bottom of screen)
        self.heal_region = DetectionBox(
            x=0.40, y=0.50, width=0.20, height=0.05
        )
        
        # Healing types and durations
        self.heal_types = {
            "first_aid": {"duration": 6.0, "amount": 75, "color": (0, 255, 0)},
            "bandage": {"duration": 3.0, "amount": 10, "color": (255, 255, 255)},
            "medkit": {"duration": 8.0, "amount": 100, "color": (0, 200, 255)},
            "painkiller": {"duration": 6.0, "amount": 60, "color": (255, 200, 0)},
            "energy_drink": {"duration": 4.0, "amount": 40, "color": (255, 255, 0)},
            "adrenaline": {"duration": 6.0, "amount": 100, "color": (255, 150, 0)},
        }
        
        # Active heals being tracked
        self.active_heals: Dict[str, HealingInfo] = {}
        
        # Progress bar detection
        self.progress_bar_color = (255, 255, 255)  # White progress bar
        self.progress_bar_bg_color = (50, 50, 50)   # Dark background
        
        self._log = logger.bind(detector=self.name)
    
    async def detect(self, frame: np.ndarray, **kwargs) -> DetectionResult:
        """
        Detect healing actions from frame.
        
        Args:
            frame: Full game frame
            **kwargs: player_id, known_heals
        """
        
        start_time = time.time()
        h, w = frame.shape[:2]
        
        # Detect healing progress bars
        healing_events = self._detect_heal_progress(frame)
        
        # Update active heals
        self._update_active_heals(healing_events)
        
        result = DetectionResult(
            detector_name=self.name,
            status=DetectionStatus.SUCCESS if healing_events or self.active_heals else DetectionStatus.NOT_FOUND,
            frame_width=w,
            frame_height=h,
            processing_time_ms=self._time_detection(start_time),
            extra_data={
                "healing_detected": len(healing_events),
                "active_heals": len(self.active_heals),
                "events": [
                    {
                        "type": e.heal_type,
                        "progress": round(e.heal_progress, 1),
                        "completed": e.is_completed,
                        "cancelled": e.is_cancelled,
                        "confidence": round(e.confidence, 3),
                    }
                    for e in healing_events
                ],
            },
        )
        
        self._last_result = result
        return result
    
    def _detect_heal_progress(self, frame: np.ndarray) -> List[HealingInfo]:
        """Detect healing progress bars"""
        
        events = []
        
        try:
            import cv2
            
            h, w = frame.shape[:2]
            
            # Extract heal region
            x1 = int(self.heal_region.x * w)
            y1 = int(self.heal_region.y * h)
            x2 = int((self.heal_region.x + self.heal_region.width) * w)
            y2 = int((self.heal_region.y + self.heal_region.height) * h)
            
            region = frame[y1:y2, x1:x2]
            
            if region.size == 0:
                return events
            
            # Convert to grayscale
            gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
            
            # Detect progress bar (white rectangle on dark background)
            # Threshold for bright pixels (progress bar)
            _, bright = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
            
            # Find contours
            contours, _ = cv2.findContours(
                bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            
            for contour in contours:
                x, y, bw, bh = cv2.boundingRect(contour)
                
                # Progress bar is wide and thin
                aspect_ratio = bw / max(1, bh)
                if aspect_ratio < 3 or bh < 5 or bw < 20:
                    continue
                
                # Calculate progress (width relative to expected full width)
                expected_full_width = int(self.heal_region.width * w * 0.8)
                progress = min(100, (bw / expected_full_width) * 100)
                
                # Determine heal type based on color
                heal_type = "first_aid"  # Default
                if region.size > 0 and y + bh < region.shape[0]:
                    bar_color = region[y + bh//2, x + bw//2]
                    heal_type = self._identify_heal_type(bar_color)
                
                event = HealingInfo(
                    event_id=f"heal_{int(time.time()*1000)}",
                    heal_type=heal_type,
                    heal_duration=self.heal_types.get(heal_type, {}).get("duration", 6.0),
                    heal_amount=self.heal_types.get(heal_type, {}).get("amount", 75),
                    heal_progress=progress,
                    is_active=progress < 95,
                    is_completed=progress >= 95,
                    position_x=x1 + x,
                    position_y=y1 + y,
                    progress_bar_x=x1 + x,
                    progress_bar_y=y1 + y,
                    progress_bar_width=bw,
                    confidence=0.7,
                )
                
                events.append(event)
            
        except ImportError:
            pass
        except Exception as e:
            self._log.error(f"Healing detection error: {e}")
        
        return events
    
    def _identify_heal_type(self, color: np.ndarray) -> str:
        """Identify heal type from bar color"""
        
        b, g, r = int(color[0]), int(color[1]), int(color[2])
        
        # Green = first aid
        if g > r and g > b and g > 150:
            return "first_aid"
        # Yellow/Orange = boost
        elif r > 150 and g > 150 and b < 100:
            return "painkiller"
        # Yellow = energy drink
        elif r > 200 and g > 200 and b < 100:
            return "energy_drink"
        # Blue = medkit
        elif b > r and b > g and b > 150:
            return "medkit"
        # White = bandage
        elif r > 200 and g > 200 and b > 200:
            return "bandage"
        
        return "first_aid"
    
    def _update_active_heals(self, detected: List[HealingInfo]) -> None:
        """Update active healing tracking"""
        
        current_time = time.time()
        
        # Update existing heals
        for event in detected:
            # Find matching active heal
            matched = False
            for heal_id, active in list(self.active_heals.items()):
                if (abs(active.position_x - event.position_x) < 50 and
                    abs(active.position_y - event.position_y) < 20):
                    # Update progress
                    active.heal_progress = event.heal_progress
                    active.is_completed = event.is_completed
                    active.timestamp = current_time
                    matched = True
                    break
            
            if not matched and event.is_active:
                self.active_heals[event.event_id] = event
        
        # Remove completed/cancelled heals
        completed = []
        for heal_id, active in self.active_heals.items():
            if active.is_completed:
                completed.append(heal_id)
            elif current_time - active.timestamp > active.heal_duration + 1:
                # Heal should have completed by now
                active.is_completed = True
                completed.append(heal_id)
        
        for heal_id in completed:
            del self.active_heals[heal_id]
    
    def get_active_heals(self) -> List[HealingInfo]:
        """Get currently active healing actions"""
        return list(self.active_heals.values())
    
    def get_detection_regions(self, frame_width: int, frame_height: int) -> List[DetectionBox]:
        """Get healing detection regions"""
        return [self.heal_region]