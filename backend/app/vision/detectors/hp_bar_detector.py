# backend/app/vision/detectors/hp_bar_detector.py
"""
AEGIS AI - HP Bar Detector
Detects and reads health bars, armor bars, and boost levels
from the BGMI spectator HUD.
"""

import time
from typing import List, Tuple
from dataclasses import dataclass, field
import numpy as np
import structlog

from .base_detector import BaseDetector, DetectionResult, DetectionBox, DetectionStatus

logger = structlog.get_logger("aegis.vision.hp_bar")


@dataclass
class HPBarInfo:
    """Health bar information for a player"""
    player_id: str = ""
    player_name: str = ""
    team_id: str = ""
    
    # HP
    hp_current: int = 100
    hp_max: int = 100
    hp_percentage: float = 100.0
    hp_bar_width_px: int = 0
    hp_bar_color: Tuple[int, int, int] = (0, 255, 0)
    
    # Armor
    armor_current: int = 100
    armor_max: int = 100
    armor_percentage: float = 100.0
    armor_type: str = "level3"  # level1, level2, level3
    
    # Boost
    boost_level: int = 0  # 0-3 bars
    is_boosted: bool = False
    
    # Status
    is_alive: bool = True
    is_knocked: bool = False
    is_healing: bool = False
    
    # Position on screen
    bar_x: float = 0.0
    bar_y: float = 0.0
    bar_width: float = 0.0
    bar_height: float = 0.0
    
    # Detection confidence
    confidence: float = 0.0
    timestamp: float = field(default_factory=time.time)


class HPBarDetector(BaseDetector):
    """
    Detects and reads health bars from the BGMI spectator HUD.
    
    Tracks:
    - Current HP and max HP
    - Armor level and durability
    - Boost bar level (0-3)
    - Knock state indicator
    - Healing animation
    
    Uses color thresholding and template matching
    to locate and read HP/armor bars.
    """
    
    def __init__(self):
        super().__init__(name="hp_bar_detector")
        
        # HP bar regions (spectator mode shows team HP on left)
        self.hp_bar_region = DetectionBox(
            x=0.02, y=0.15, width=0.08, height=0.60
        )
        
        # HP color ranges (BGR)
        self.hp_colors = {
            "full": ((0, 200, 0), (50, 255, 50)),      # Green
            "medium": ((0, 200, 200), (50, 255, 255)),  # Yellow
            "low": ((0, 0, 200), (50, 50, 255)),        # Red
            "critical": ((0, 0, 150), (30, 30, 200)),   # Dark red
        }
        
        # Armor color ranges
        self.armor_colors = {
            "level3": ((200, 150, 0), (255, 200, 50)),   # Gold/Yellow
            "level2": ((150, 100, 150), (200, 150, 200)), # Purple
            "level1": ((100, 100, 100), (180, 180, 180)), # Gray
        }
        
        # Boost bar colors (orange/yellow)
        self.boost_color = ((0, 150, 200), (50, 200, 255))
        
        # Knock indicators (red overlay)
        self.knock_color = ((0, 0, 180), (30, 30, 255))
        
        self._log = logger.bind(detector=self.name)
    
    async def detect(self, frame: np.ndarray, **kwargs) -> DetectionResult:
        """
        Detect HP bars from frame.
        
        Args:
            frame: Full game frame
            **kwargs: player_count, expected_positions
        """
        
        start_time = time.time()
        h, w = frame.shape[:2]
        
        # Extract HP bar region
        x1 = int(self.hp_bar_region.x * w)
        y1 = int(self.hp_bar_region.y * h)
        x2 = int((self.hp_bar_region.x + self.hp_bar_region.width) * w)
        y2 = int((self.hp_bar_region.y + self.hp_bar_region.height) * h)
        
        region = frame[y1:y2, x1:x2]
        
        if region.size == 0:
            return DetectionResult(
                detector_name=self.name,
                status=DetectionStatus.FAILED,
                frame_width=w,
                frame_height=h,
            )
        
        # Detect HP bars
        hp_infos = self._detect_hp_bars(region, x1, y1)
        
        # Detect armor bars
        self._detect_armor_bars(region, hp_infos, x1, y1)
        
        # Detect boost levels
        self._detect_boost_bars(region, hp_infos, x1, y1)
        
        # Detect knock state
        self._detect_knock_state(region, hp_infos, x1, y1)
        
        result = DetectionResult(
            detector_name=self.name,
            status=DetectionStatus.SUCCESS if hp_infos else DetectionStatus.NOT_FOUND,
            frame_width=w,
            frame_height=h,
            processing_time_ms=self._time_detection(start_time),
            extra_data={
                "players": [
                    {
                        "player_id": info.player_id,
                        "player_name": info.player_name,
                        "hp": info.hp_current,
                        "hp_pct": round(info.hp_percentage, 1),
                        "armor": info.armor_current,
                        "armor_type": info.armor_type,
                        "boost": info.boost_level,
                        "is_knocked": info.is_knocked,
                        "confidence": round(info.confidence, 3),
                    }
                    for info in hp_infos
                ],
            },
        )
        
        self._last_result = result
        return result
    
    def _detect_hp_bars(
        self,
        region: np.ndarray,
        offset_x: int,
        offset_y: int,
    ) -> List[HPBarInfo]:
        """
        Detect HP bars using color thresholding.
        HP bars are horizontal bars with green/yellow/red colors.
        """
        
        hp_infos = []
        
        try:
            import cv2
            
            # Convert to HSV for better color detection
            hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
            
            # Detect green bars (full HP)
            for color_name, (lower, upper) in self.hp_colors.items():
                lower = np.array(lower, dtype=np.uint8)
                upper = np.array(upper, dtype=np.uint8)
                
                mask = cv2.inRange(hsv, lower, upper)
                
                # Find contours
                contours, _ = cv2.findContours(
                    mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                )
                
                for contour in contours:
                    x, y, bw, bh = cv2.boundingRect(contour)
                    
                    # Filter by aspect ratio (HP bars are wide and thin)
                    aspect_ratio = bw / max(1, bh)
                    if aspect_ratio < 3:
                        continue
                    
                    # Filter by size
                    if bw < 30 or bh < 5:
                        continue
                    
                    # Calculate HP percentage based on bar width
                    # Full HP bar is typically ~200px wide
                    hp_pct = min(100, (bw / 200) * 100)
                    hp_current = int(hp_pct)
                    
                    info = HPBarInfo(
                        player_name=f"Player_{len(hp_infos)+1}",
                        hp_current=hp_current,
                        hp_percentage=hp_pct,
                        hp_bar_width_px=bw,
                        hp_bar_color=(
                            self.hp_colors[color_name][0][0],
                            self.hp_colors[color_name][0][1],
                            self.hp_colors[color_name][0][2],
                        ),
                        bar_x=offset_x + x,
                        bar_y=offset_y + y,
                        bar_width=bw,
                        bar_height=bh,
                        confidence=0.7,
                    )
                    
                    # Check if HP is critical
                    if hp_pct < 25:
                        info.hp_bar_color = (0, 0, 255)  # Red
                    
                    hp_infos.append(info)
            
            # Sort by Y position (player order)
            hp_infos.sort(key=lambda info: info.bar_y)
            
            # Assign player IDs
            for i, info in enumerate(hp_infos):
                info.player_id = f"player_{i+1}"
            
        except ImportError:
            self._log.warning("OpenCV not available")
        except Exception as e:
            self._log.error(f"HP bar detection error: {e}")
        
        return hp_infos
    
    def _detect_armor_bars(
        self,
        region: np.ndarray,
        hp_infos: List[HPBarInfo],
        offset_x: int,
        offset_y: int,
    ) -> None:
        """Detect armor bars near HP bars"""
        
        for info in hp_infos:
            # Armor bar is typically right below HP bar
            armor_y = int(info.bar_y + info.bar_height + 5)
            armor_region = region[
                max(0, armor_y - offset_y):min(region.shape[0], armor_y - offset_y + 10),
                max(0, int(info.bar_x - offset_x)):min(region.shape[1], int(info.bar_x - offset_x + 200)),
            ]
            
            if armor_region.size == 0:
                continue
            
            # Detect armor color
            for armor_type, (lower, upper) in self.armor_colors.items():
                try:
                    import cv2
                    hsv = cv2.cvtColor(armor_region, cv2.COLOR_BGR2HSV)
                    lower = np.array(lower, dtype=np.uint8)
                    upper = np.array(upper, dtype=np.uint8)
                    mask = cv2.inRange(hsv, lower, upper)
                    
                    if np.sum(mask) > 100:
                        info.armor_type = armor_type
                        info.armor_percentage = min(100, (np.sum(mask) / (armor_region.size / 3)) * 200)
                        info.armor_current = int(info.armor_percentage)
                        break
                except ImportError:
                    pass
    
    def _detect_boost_bars(
        self,
        region: np.ndarray,
        hp_infos: List[HPBarInfo],
        offset_x: int,
        offset_y: int,
    ) -> None:
        """Detect boost bar level"""
        
        for info in hp_infos:
            # Boost bar is above HP bar
            boost_y = int(info.bar_y - 15)
            boost_region = region[
                max(0, boost_y - offset_y):min(region.shape[0], int(info.bar_y - offset_y)),
                max(0, int(info.bar_x - offset_x)):min(region.shape[1], int(info.bar_x - offset_x + 100)),
            ]
            
            if boost_region.size == 0:
                continue
            
            try:
                import cv2
                hsv = cv2.cvtColor(boost_region, cv2.COLOR_BGR2HSV)
                lower = np.array(self.boost_color[0], dtype=np.uint8)
                upper = np.array(self.boost_color[1], dtype=np.uint8)
                mask = cv2.inRange(hsv, lower, upper)
                
                # Count boost segments (each segment is ~30px wide)
                boost_pixels = np.sum(mask) / 255
                info.boost_level = min(3, int(boost_pixels / 30))
                info.is_boosted = info.boost_level > 0
            except ImportError:
                pass
    
    def _detect_knock_state(
        self,
        region: np.ndarray,
        hp_infos: List[HPBarInfo],
        offset_x: int,
        offset_y: int,
    ) -> None:
        """Detect if player is knocked (red overlay on HP bar)"""
        
        for info in hp_infos:
            # Check for red overlay on HP bar
            hp_region = region[
                max(0, int(info.bar_y - offset_y)):min(region.shape[0], int(info.bar_y - offset_y + info.bar_height)),
                max(0, int(info.bar_x - offset_x)):min(region.shape[1], int(info.bar_x - offset_x + info.bar_width)),
            ]
            
            if hp_region.size == 0:
                continue
            
            try:
                import cv2
                hsv = cv2.cvtColor(hp_region, cv2.COLOR_BGR2HSV)
                lower = np.array(self.knock_color[0], dtype=np.uint8)
                upper = np.array(self.knock_color[1], dtype=np.uint8)
                mask = cv2.inRange(hsv, lower, upper)
                
                # Significant red overlay = knocked
                if np.sum(mask) > hp_region.size * 0.3:
                    info.is_knocked = True
                    info.is_alive = False
            except ImportError:
                pass
    
    def get_detection_regions(self, frame_width: int, frame_height: int) -> List[DetectionBox]:
        """Get HP bar detection region"""
        return [self.hp_bar_region]