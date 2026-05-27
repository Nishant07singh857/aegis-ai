# backend/app/vision/detectors/zone_detector.py
"""
AEGIS AI - Zone Detector
Detects safe zone and blue zone boundaries from the BGMI HUD.
Tracks zone phase, timer, and position changes.
"""

import time
from typing import List, Optional, Tuple
from dataclasses import dataclass, field
import numpy as np
import structlog

from .base_detector import BaseDetector, DetectionResult, DetectionBox, DetectionStatus

logger = structlog.get_logger("aegis.vision.zone")


@dataclass
class ZoneInfo:
    """Zone information"""
    timestamp: float = field(default_factory=time.time)
    
    # Current zone
    zone_phase: int = 0  # 0-8
    safe_zone_center_x: float = 0.0
    safe_zone_center_y: float = 0.0
    safe_zone_radius: float = 0.0
    
    # Blue zone
    blue_zone_center_x: float = 0.0
    blue_zone_center_y: float = 0.0
    blue_zone_radius: float = 0.0
    blue_zone_damage: int = 0  # Damage per tick
    
    # Next zone
    next_zone_center_x: Optional[float] = None
    next_zone_center_y: Optional[float] = None
    
    # Timer
    zone_shrink_timer: float = 0.0  # Seconds until next shrink
    zone_shrink_duration: float = 0.0  # How long shrink takes
    is_shrinking: bool = False
    
    # Map info
    map_name: str = "erangel"
    map_size: float = 8000.0
    
    # Detection
    confidence: float = 0.0
    
    def get_zone_damage(self) -> int:
        """Get blue zone damage based on phase"""
        damage_per_phase = {1: 1, 2: 2, 3: 4, 4: 6, 5: 8, 6: 10, 7: 12, 8: 15}
        return damage_per_phase.get(self.zone_phase, 1)


class ZoneDetector(BaseDetector):
    """
    Detects zone information from BGMI minimap and HUD.
    
    Tracks:
    - Safe zone (white circle) position and radius
    - Blue zone (blue circle) position and radius
    - Zone phase (1-8)
    - Shrink timer
    - Next zone prediction
    
    Uses minimap analysis and HUD timer reading.
    """
    
    def __init__(self):
        super().__init__(name="zone_detector")
        
        # Zone display regions
        self.minimap_region = DetectionBox(
            x=0.72, y=0.70, width=0.26, height=0.28
        )
        self.timer_region = DetectionBox(
            x=0.45, y=0.05, width=0.10, height=0.04
        )
        
        # Zone phase radius ranges (in meters) for Erangel
        self.zone_radii = {
            0: 4000,  # Initial circle
            1: 2600,  # First shrink
            2: 1700,  # Second shrink
            3: 1100,  # Third shrink
            4: 700,   # Fourth shrink
            5: 450,   # Fifth shrink
            6: 280,   # Sixth shrink
            7: 150,   # Seventh shrink
            8: 60,    # Final circle
        }
        
        # Previous zone for change detection
        self._previous_phase = 0
        self._previous_center = (0.0, 0.0)
        self._zone_history: List[ZoneInfo] = []
        
        self._log = logger.bind(detector=self.name)
    
    async def detect(self, frame: np.ndarray, **kwargs) -> DetectionResult:
        """
        Detect zone from frame.
        
        Args:
            frame: Full game frame
            **kwargs: map_name, current_phase
        """
        
        start_time = time.time()
        h, w = frame.shape[:2]
        
        # Extract minimap region
        mx1 = int(self.minimap_region.x * w)
        my1 = int(self.minimap_region.y * h)
        mx2 = int((self.minimap_region.x + self.minimap_region.width) * w)
        my2 = int((self.minimap_region.y + self.minimap_region.height) * h)
        
        minimap = frame[my1:my2, mx1:mx2]
        
        # Extract timer region
        tx1 = int(self.timer_region.x * w)
        ty1 = int(self.timer_region.y * h)
        tx2 = int((self.timer_region.x + self.timer_region.width) * w)
        ty2 = int((self.timer_region.y + self.timer_region.height) * h)
        
        timer_region = frame[ty1:ty2, tx1:tx2]
        
        if minimap.size == 0:
            return DetectionResult(
                detector_name=self.name,
                status=DetectionStatus.FAILED,
                frame_width=w,
                frame_height=h,
            )
        
        # Detect zones
        zone_info = self._detect_zones(minimap, timer_region, w, h)
        
        # Check for zone change
        if zone_info:
            zone_info = self._check_zone_change(zone_info)
            self._zone_history.append(zone_info)
            if len(self._zone_history) > 20:
                self._zone_history = self._zone_history[-20:]
        
        result = DetectionResult(
            detector_name=self.name,
            status=DetectionStatus.SUCCESS if zone_info else DetectionStatus.NOT_FOUND,
            frame_width=w,
            frame_height=h,
            processing_time_ms=self._time_detection(start_time),
            extra_data={
                "zone": {
                    "phase": zone_info.zone_phase if zone_info else 0,
                    "safe_center": (
                        round(zone_info.safe_zone_center_x, 1),
                        round(zone_info.safe_zone_center_y, 1),
                    ) if zone_info else (0, 0),
                    "safe_radius": round(zone_info.safe_zone_radius, 1) if zone_info else 0,
                    "blue_center": (
                        round(zone_info.blue_zone_center_x, 1),
                        round(zone_info.blue_zone_center_y, 1),
                    ) if zone_info else (0, 0),
                    "timer": round(zone_info.zone_shrink_timer, 1) if zone_info else 0,
                    "is_shrinking": zone_info.is_shrinking if zone_info else False,
                    "damage": zone_info.get_zone_damage() if zone_info else 0,
                    "confidence": round(zone_info.confidence, 3) if zone_info else 0,
                },
            },
        )
        
        self._last_result = result
        return result
    
    def _detect_zones(
        self,
        minimap: np.ndarray,
        timer_region: np.ndarray,
        frame_w: int,
        frame_h: int,
    ) -> Optional[ZoneInfo]:
        """Detect zone circles and timer"""
        
        info = ZoneInfo()
        
        try:
            import cv2
            
            mh, mw = minimap.shape[:2]
            
            # Convert to HSV
            hsv = cv2.cvtColor(minimap, cv2.COLOR_BGR2HSV)
            
            # Detect white circle (safe zone)
            safe_circle = self._detect_circle(hsv, (0, 0, 200), (180, 30, 255))
            
            if safe_circle:
                # Convert minimap position to world coordinates
                # Scale based on minimap size vs map size
                scale = info.map_size / mw
                
                info.safe_zone_center_x = safe_circle[0][0] * scale
                info.safe_zone_center_y = safe_circle[0][1] * scale
                info.safe_zone_radius = safe_circle[1] * scale
                
                # Determine zone phase from radius
                info.zone_phase = self._determine_phase(info.safe_zone_radius)
                info.confidence += 0.4
            
            # Detect blue circle
            blue_circle = self._detect_circle(hsv, (100, 100, 100), (130, 255, 255))
            
            if blue_circle:
                scale = info.map_size / mw
                info.blue_zone_center_x = blue_circle[0][0] * scale
                info.blue_zone_center_y = blue_circle[0][1] * scale
                info.blue_zone_radius = blue_circle[1] * scale
                info.confidence += 0.3
            
            # Read zone timer using OCR
            if timer_region.size > 0:
                timer_text = self._read_timer(timer_region)
                if timer_text:
                    try:
                        # Parse timer (format: "MM:SS" or just seconds)
                        if ':' in timer_text:
                            parts = timer_text.split(':')
                            info.zone_shrink_timer = int(parts[0]) * 60 + int(parts[1])
                        else:
                            info.zone_shrink_timer = int(timer_text)
                        info.confidence += 0.2
                    except ValueError:
                        pass
            
            # Check if zone is shrinking
            if info.blue_zone_radius > info.safe_zone_radius:
                info.is_shrinking = True
                info.zone_shrink_duration = max(0, 
                    (info.blue_zone_radius - info.safe_zone_radius) / 10
                )
            
        except ImportError:
            self._log.warning("OpenCV not available")
        except Exception as e:
            self._log.error(f"Zone detection error: {e}")
        
        return info if info.confidence > 0.3 else None
    
    def _detect_circle(
        self,
        hsv: np.ndarray,
        lower: Tuple[int, int, int],
        upper: Tuple[int, int, int],
    ) -> Optional[Tuple[Tuple[float, float], float]]:
        """Detect a circle in HSV image"""
        
        try:
            import cv2
            
            lower = np.array(lower, dtype=np.uint8)
            upper = np.array(upper, dtype=np.uint8)
            
            mask = cv2.inRange(hsv, lower, upper)
            
            # Find circles using Hough transform
            circles = cv2.HoughCircles(
                mask,
                cv2.HOUGH_GRADIENT,
                dp=1,
                minDist=50,
                param1=50,
                param2=30,
                minRadius=20,
                maxRadius=min(mask.shape[0], mask.shape[1]) // 2,
            )
            
            if circles is not None and len(circles) > 0:
                circle = circles[0][0]
                return ((float(circle[0]), float(circle[1])), float(circle[2]))
                
        except ImportError:
            pass
        
        return None
    
    def _read_timer(self, region: np.ndarray) -> Optional[str]:
        """Read zone timer using OCR"""
        
        try:
            import cv2
            from paddleocr import PaddleOCR
            
            if not hasattr(self, '_ocr'):
                self._ocr = PaddleOCR(
                    use_angle_cls=False,
                    lang='en',
                    use_gpu=True,
                    show_log=False,
                )
            
            gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
            
            results = self._ocr.ocr(thresh, cls=False)
            
            if results and results[0]:
                for line in results[0]:
                    if line and len(line) >= 2:
                        text = line[1][0].strip()
                        # Look for time pattern
                        import re
                        time_match = re.search(r'(\d{1,2}:\d{2})|(\d+)', text)
                        if time_match:
                            return time_match.group()
            
        except ImportError:
            pass
        except Exception as e:
            self._log.debug(f"Timer OCR error: {e}")
        
        return None
    
    def _determine_phase(self, radius: float) -> int:
        """Determine zone phase from radius"""
        for phase, expected_radius in self.zone_radii.items():
            if abs(radius - expected_radius) < expected_radius * 0.3:
                return phase
        
        # Estimate based on radius
        if radius > 2000:
            return 1
        elif radius > 1000:
            return 3
        elif radius > 500:
            return 5
        elif radius > 200:
            return 7
        else:
            return 8
    
    def _check_zone_change(self, info: ZoneInfo) -> ZoneInfo:
        """Check if zone has changed and update history"""
        
        # Check phase change
        if info.zone_phase != self._previous_phase and self._previous_phase > 0:
            info.is_shrinking = True
            self._log.info(
                f"Zone changed: Phase {self._previous_phase} → {info.zone_phase}",
                new_center=(round(info.safe_zone_center_x, 1), round(info.safe_zone_center_y, 1)),
                new_radius=round(info.safe_zone_radius, 1),
            )
        
        self._previous_phase = info.zone_phase
        self._previous_center = (info.safe_zone_center_x, info.safe_zone_center_y)
        
        return info
    
    def get_current_phase(self) -> int:
        """Get current zone phase"""
        return self._previous_phase
    
    def get_detection_regions(self, frame_width: int, frame_height: int) -> List[DetectionBox]:
        """Get zone detection regions"""
        return [self.minimap_region, self.timer_region]