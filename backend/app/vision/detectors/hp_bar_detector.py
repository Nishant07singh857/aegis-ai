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
    # Honest default: no armor bar observed yet -> "unknown", NOT an
    # invented "level3" classification (Integrity Fix Pack, H20).
    armor_type: str = "unknown"  # level1, level2, level3, unknown
    
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
    # What the confidence number actually IS. For this detector it is
    # the contour fill ratio (mask pixels / bbox area) of the detected
    # bar — an evidence quantity measured on this frame, NOT a
    # calibrated probability (this detector was never in the Phase 23
    # measurement programme; H20).
    confidence_basis: str = "contour_fill_ratio"
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
        
        # HP color ranges — HSV (OpenCV H: 0-179, S/V: 0-255).
        #
        # H20 BUG FIX: these ranges were previously defined as BGR
        # triples but applied via cv2.inRange to an HSV-converted
        # image, so a pure-green (full HP) bar matched NOTHING and a
        # dark-red bar matched nothing either. The hue bands below are
        # the BGR->HSV conversions of the original endpoint colours
        # (green (0,255,0)->H60, yellow (0,255,255)->H30,
        # red (0,0,255)->H0, dark red (0,0,150)->H0/V150), with
        # saturation/value floors widened so dim/dull bars still read
        # (cf. the dark-bar runtime end-to-end case, BGR (0,50,50) ->
        # HSV (30,255,50)). Red hues wrap around H=0/179; the wrap
        # halves for "low"/"critical" live in _hp_hue_wrap below.
        # "low" vs "critical" are split on value (bright vs dark red).
        self.hp_colors = {
            "full": ((35, 80, 50), (85, 255, 255)),       # Green
            "medium": ((20, 80, 50), (34, 255, 255)),     # Yellow
            "low": ((0, 80, 181), (10, 255, 255)),       # Bright red
            "critical": ((0, 80, 50), (10, 255, 180)),   # Dark red
        }
        # Red hue wrap-around halves (H near 179), same S/V splits.
        self._hp_hue_wrap = {
            "low": ((170, 80, 181), (179, 255, 255)),
            "critical": ((170, 80, 50), (179, 255, 180)),
        }
        # Representative BGR colours for each HP class (display only —
        # hp_bar_color is a BGR triple everywhere it is consumed).
        self.hp_display_colors = {
            "full": (0, 255, 0),
            "medium": (0, 255, 255),
            "low": (0, 0, 255),
            "critical": (0, 0, 150),
        }

        # Armor color ranges — HSV (same BGR-on-HSV bug as hp_colors:
        # gold (0,200,255)BGR -> H24, purple -> H~150, gray = low S).
        self.armor_colors = {
            "level3": ((18, 100, 100), (35, 255, 255)),   # Gold/Yellow
            "level2": ((135, 60, 80), (165, 255, 255)),   # Purple
            "level1": ((0, 0, 90), (179, 60, 200)),       # Gray
        }

        # Boost bar colors (orange/yellow) — HSV (orange -> H~21)
        self.boost_color = ((10, 120, 120), (30, 255, 255))

        # Knock indicators (red overlay) — HSV
        self.knock_color = ((0, 120, 120), (10, 255, 255))
        
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
                        "confidence_basis": info.confidence_basis,
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
            
            # Detect bars per HP colour class (HSV ranges — see __init__)
            for color_name, (lower, upper) in self.hp_colors.items():
                lower = np.array(lower, dtype=np.uint8)
                upper = np.array(upper, dtype=np.uint8)

                mask = cv2.inRange(hsv, lower, upper)

                # Red hues wrap around H=0/179: OR in the wrap half.
                wrap = self._hp_hue_wrap.get(color_name)
                if wrap is not None:
                    wrap_mask = cv2.inRange(
                        hsv,
                        np.array(wrap[0], dtype=np.uint8),
                        np.array(wrap[1], dtype=np.uint8),
                    )
                    mask = cv2.bitwise_or(mask, wrap_mask)

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

                    # Confidence is DERIVED from evidence on this frame:
                    # the fraction of the bounding box actually filled by
                    # bar-coloured pixels (a solid bar ~= 1.0, a fragmented
                    # colour smear much less). It is a fill ratio, not a
                    # calibrated probability — see HPBarInfo.confidence_basis.
                    bar_roi = mask[y:y + bh, x:x + bw]
                    fill_ratio = (
                        float(cv2.countNonZero(bar_roi)) / float(bw * bh)
                        if bw > 0 and bh > 0 else 0.0
                    )

                    info = HPBarInfo(
                        player_name=f"Player_{len(hp_infos)+1}",
                        hp_current=hp_current,
                        hp_percentage=hp_pct,
                        hp_bar_width_px=bw,
                        hp_bar_color=self.hp_display_colors[color_name],
                        bar_x=offset_x + x,
                        bar_y=offset_y + y,
                        bar_width=bw,
                        bar_height=bh,
                        confidence=round(fill_ratio, 3),
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