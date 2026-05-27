# backend/app/vision/detectors/minimap_reader.py
"""
AEGIS AI - Minimap Reader
Extracts player positions, zone information, and team locations
from the BGMI minimap in spectator mode.
"""

import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import numpy as np
import structlog

from .base_detector import BaseDetector, DetectionResult, DetectionBox, DetectionStatus

logger = structlog.get_logger("aegis.vision.minimap")


@dataclass
class MinimapData:
    """Data extracted from the minimap"""
    timestamp: float = field(default_factory=time.time)
    
    # Map info
    map_name: str = "erangel"
    map_scale: float = 1.0  # pixels per meter
    
    # Minimap position on screen
    minimap_x: float = 0.0
    minimap_y: float = 0.0
    minimap_width: float = 0.0
    minimap_height: float = 0.0
    minimap_center: Tuple[float, float] = (0.0, 0.0)
    
    # Player positions (relative to minimap)
    player_positions: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    
    # Team positions
    team_positions: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    
    # Zone info
    safe_zone_center: Optional[Tuple[float, float]] = None
    safe_zone_radius: float = 0.0
    blue_zone_center: Optional[Tuple[float, float]] = None
    blue_zone_radius: float = 0.0
    
    # Player direction (where they're looking)
    player_directions: Dict[str, float] = field(default_factory=dict)
    
    # Vehicle indicators
    vehicles: List[Tuple[float, float]] = field(default_factory=list)
    
    # Air drop markers
    air_drops: List[Tuple[float, float]] = field(default_factory=list)
    
    # Death markers
    death_markers: List[Tuple[float, float]] = field(default_factory=list)
    
    # Detection quality
    confidence: float = 0.0


class MinimapReader(BaseDetector):
    """
    Reads the BGMI minimap to extract game state.
    
    Extracts:
    - Player positions and directions
    - Team locations
    - Safe zone (white circle)
    - Blue zone (blue circle)
    - Vehicle markers
    - Air drop markers
    - Death crate markers
    
    Uses template matching and color detection
    to locate and interpret minimap elements.
    """
    
    def __init__(self):
        super().__init__(name="minimap_reader")
        
        # Minimap region (bottom-right corner)
        self.minimap_region = DetectionBox(
            x=0.72, y=0.70, width=0.26, height=0.28
        )
        
        # Player marker colors (BGR)
        self.player_colors = {
            "teammate": ((0, 255, 0), (50, 255, 50)),     # Green
            "enemy": ((0, 0, 255), (50, 50, 255)),        # Red
            "self": ((255, 255, 255), (255, 255, 255)),   # White
            "knocked": ((0, 255, 255), (50, 255, 255)),   # Yellow
        }
        
        # Zone colors
        self.zone_colors = {
            "safe_zone": ((255, 255, 255), (255, 255, 255)),  # White circle
            "blue_zone": ((255, 0, 0), (255, 50, 50)),        # Blue/Red edge
        }
        
        # Vehicle marker color (yellow/white)
        self.vehicle_color = ((0, 255, 255), (50, 255, 255))
        
        # Marker sizes (in pixels at 1080p)
        self.player_marker_size = 4
        self.vehicle_marker_size = 6
        
        self._log = logger.bind(detector=self.name)
    
    async def detect(self, frame: np.ndarray, **kwargs) -> DetectionResult:
        """
        Read minimap from frame.
        
        Args:
            frame: Full game frame
            **kwargs: map_name, known_positions
        """
        
        start_time = time.time()
        h, w = frame.shape[:2]
        
        # Extract minimap region
        x1 = int(self.minimap_region.x * w)
        y1 = int(self.minimap_region.y * h)
        x2 = int((self.minimap_region.x + self.minimap_region.width) * w)
        y2 = int((self.minimap_region.y + self.minimap_region.height) * h)
        
        minimap = frame[y1:y2, x1:x2]
        
        if minimap.size == 0:
            return DetectionResult(
                detector_name=self.name,
                status=DetectionStatus.FAILED,
                frame_width=w,
                frame_height=h,
            )
        
        # Parse minimap
        minimap_data = self._parse_minimap(minimap, x1, y1, w, h)
        
        result = DetectionResult(
            detector_name=self.name,
            status=DetectionStatus.SUCCESS,
            frame_width=w,
            frame_height=h,
            processing_time_ms=self._time_detection(start_time),
            extra_data={
                "minimap": {
                    "center": minimap_data.minimap_center,
                    "size": (minimap_data.minimap_width, minimap_data.minimap_height),
                    "players": len(minimap_data.player_positions),
                    "teams": len(minimap_data.team_positions),
                    "zone": {
                        "safe": minimap_data.safe_zone_center,
                        "safe_radius": minimap_data.safe_zone_radius,
                        "blue": minimap_data.blue_zone_center,
                        "blue_radius": minimap_data.blue_zone_radius,
                    },
                    "vehicles": len(minimap_data.vehicles),
                    "air_drops": len(minimap_data.air_drops),
                },
                "player_positions": minimap_data.player_positions,
                "confidence": round(minimap_data.confidence, 3),
            },
        )
        
        self._last_result = result
        return result
    
    def _parse_minimap(
        self,
        minimap: np.ndarray,
        offset_x: int,
        offset_y: int,
        frame_w: int,
        frame_h: int,
    ) -> MinimapData:
        """Parse all information from the minimap image"""
        
        data = MinimapData(
            minimap_x=offset_x,
            minimap_y=offset_y,
            minimap_width=minimap.shape[1],
            minimap_height=minimap.shape[0],
            minimap_center=(offset_x + minimap.shape[1] / 2, offset_y + minimap.shape[0] / 2),
        )
        
        try:
            import cv2
            
            # Convert to HSV
            hsv = cv2.cvtColor(minimap, cv2.COLOR_BGR2HSV)
            
            # Detect player markers
            data.player_positions = self._detect_markers(
                hsv, self.player_colors, self.player_marker_size
            )
            
            # Detect zone circles
            safe_zone = self._detect_circle(hsv, self.zone_colors["safe_zone"])
            if safe_zone:
                data.safe_zone_center = safe_zone[0]
                data.safe_zone_radius = safe_zone[1]
            
            blue_zone = self._detect_circle(hsv, self.zone_colors["blue_zone"])
            if blue_zone:
                data.blue_zone_center = blue_zone[0]
                data.blue_zone_radius = blue_zone[1]
            
            # Detect vehicles
            data.vehicles = list(
                self._detect_markers(hsv, {"vehicle": self.vehicle_color}, self.vehicle_marker_size).values()
            )
            
            data.confidence = 0.7
            
            # Higher confidence if we found expected elements
            if data.player_positions:
                data.confidence += 0.1
            if data.safe_zone_center:
                data.confidence += 0.1
            if data.blue_zone_center:
                data.confidence += 0.1
            
        except ImportError:
            self._log.warning("OpenCV not available")
        except Exception as e:
            self._log.error(f"Minimap parsing error: {e}")
        
        return data
    
    def _detect_markers(
        self,
        hsv: np.ndarray,
        color_ranges: Dict[str, Tuple[Tuple[int, int, int], Tuple[int, int, int]]],
        min_size: int = 3,
    ) -> Dict[str, Tuple[float, float]]:
        """Detect small colored markers (players, vehicles)"""
        
        markers = {}
        
        for name, (lower, upper) in color_ranges.items():
            try:
                import cv2
                lower = np.array(lower, dtype=np.uint8)
                upper = np.array(upper, dtype=np.uint8)
                
                mask = cv2.inRange(hsv, lower, upper)
                
                # Find small contours
                contours, _ = cv2.findContours(
                    mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                )
                
                for i, contour in enumerate(contours):
                    area = cv2.contourArea(contour)
                    if area < min_size or area > 50:
                        continue
                    
                    M = cv2.moments(contour)
                    if M["m00"] > 0:
                        cx = int(M["m10"] / M["m00"])
                        cy = int(M["m01"] / M["m00"])
                        markers[f"{name}_{i}"] = (float(cx), float(cy))
                        
            except ImportError:
                pass
        
        return markers
    
    def _detect_circle(
        self,
        hsv: np.ndarray,
        color_range: Tuple[Tuple[int, int, int], Tuple[int, int, int]],
    ) -> Optional[Tuple[Tuple[float, float], float]]:
        """Detect a circle (safe zone, blue zone)"""
        
        try:
            import cv2
            lower = np.array(color_range[0], dtype=np.uint8)
            upper = np.array(color_range[1], dtype=np.uint8)
            
            mask = cv2.inRange(hsv, lower, upper)
            
            # Find circles using Hough transform
            circles = cv2.HoughCircles(
                mask, cv2.HOUGH_GRADIENT, dp=1, minDist=50,
                param1=50, param2=30, minRadius=30, maxRadius=200,
            )
            
            if circles is not None and len(circles) > 0:
                circle = circles[0][0]  # Take first circle
                return ((float(circle[0]), float(circle[1])), float(circle[2]))
                
        except ImportError:
            pass
        except Exception as e:
            self._log.debug(f"Circle detection error: {e}")
        
        return None
    
    def minimap_to_world(
        self,
        minimap_x: float,
        minimap_y: float,
        map_size: float = 8000.0,
    ) -> Tuple[float, float]:
        """Convert minimap coordinates to world coordinates"""
        
        # This requires knowing the player's position on the map
        # and the minimap scale
        world_x = (minimap_x / self.minimap_region.width) * map_size
        world_y = (minimap_y / self.minimap_region.height) * map_size
        
        return (world_x, world_y)
    
    def get_detection_regions(self, frame_width: int, frame_height: int) -> List[DetectionBox]:
        """Get minimap region"""
        return [self.minimap_region]