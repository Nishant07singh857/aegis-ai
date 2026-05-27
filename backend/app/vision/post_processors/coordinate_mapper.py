# backend/app/vision/post_processors/coordinate_mapper.py
"""
AEGIS AI - Coordinate Mapper
Converts screen coordinates to game world coordinates.
Handles different map scales, minimap calibration, and coordinate systems.
"""

import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
import structlog
import numpy as np

logger = structlog.get_logger("aegis.vision.coordinates")


@dataclass
class MapCalibration:
    """Calibration data for screen-to-world coordinate mapping"""
    map_name: str = "erangel"
    map_width_meters: float = 8000.0
    map_height_meters: float = 8000.0
    
    # Screen region for minimap
    minimap_screen_x: float = 0.72
    minimap_screen_y: float = 0.70
    minimap_screen_width: float = 0.26
    minimap_screen_height: float = 0.28
    
    # Screen resolution
    screen_width: int = 1920
    screen_height: int = 1080
    
    # World offset (where the player is on the map)
    world_offset_x: float = 0.0
    world_offset_y: float = 0.0
    
    # Scale factor (pixels per meter on minimap)
    minimap_scale: float = 0.05  # pixels per meter at 1080p
    
    # Calibration quality
    is_calibrated: bool = False
    calibration_confidence: float = 0.0
    last_calibrated: float = 0.0


@dataclass
class ScreenToWorld:
    """Screen to world coordinate conversion result"""
    screen_x: float
    screen_y: float
    world_x: float
    world_y: float
    confidence: float = 1.0
    source: str = "minimap"  # "minimap", "hud", "interpolated"


class CoordinateMapper:
    """
    Converts between screen coordinates and game world coordinates.
    
    Handles:
    - Screen pixel → World meter conversion
    - Minimap position → Map position
    - Different map sizes (Erangel 8km, Sanhok 4km, etc.)
    - Perspective correction for 3D game world
    - Multiple reference points for accuracy
    
    Coordinate systems:
    - Screen: (0,0) top-left, (1920,1080) bottom-right
    - World: (0,0) bottom-left of map, (8000,8000) top-right for Erangel
    """
    
    def __init__(self):
        # Map configurations
        self.map_configs = {
            "erangel": {"width": 8000, "height": 8000},
            "miramar": {"width": 8000, "height": 8000},
            "sanhok": {"width": 4000, "height": 4000},
            "vikendi": {"width": 6000, "height": 6000},
            "livik": {"width": 2000, "height": 2000},
            "karakin": {"width": 2000, "height": 2000},
        }
        
        # Active calibration
        self.calibration = MapCalibration()
        
        # Reference points for calibration
        self.reference_points: List[Dict[str, Any]] = []
        
        # Coordinate history for smoothing
        self.position_history: Dict[str, List[Tuple[float, float, float]]] = {}
        
        self._log = logger.bind(component="coordinate_mapper")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CALIBRATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def calibrate(
        self,
        map_name: str,
        screen_width: int,
        screen_height: int,
        known_world_position: Tuple[float, float],
        known_screen_position: Tuple[float, float],
        minimap_region: Optional[Tuple[float, float, float, float]] = None,
    ) -> bool:
        """
        Calibrate the coordinate mapper.
        
        Args:
            map_name: Map being played
            screen_width: Screen width in pixels
            screen_height: Screen height in pixels
            known_world_position: Known world position (x, y) in meters
            known_screen_position: Known screen position (x, y) in pixels
            minimap_region: Minimap region (x, y, width, height) as screen fractions
        """
        
        map_config = self.map_configs.get(map_name)
        if not map_config:
            self._log.error(f"Unknown map: {map_name}")
            return False
        
        self.calibration.map_name = map_name
        self.calibration.map_width_meters = map_config["width"]
        self.calibration.map_height_meters = map_config["height"]
        self.calibration.screen_width = screen_width
        self.calibration.screen_height = screen_height
        
        if minimap_region:
            self.calibration.minimap_screen_x = minimap_region[0]
            self.calibration.minimap_screen_y = minimap_region[1]
            self.calibration.minimap_screen_width = minimap_region[2]
            self.calibration.minimap_screen_height = minimap_region[3]
        
        # Calculate world offset
        # The minimap shows area around the player
        minimap_center_screen_x = int(
            (self.calibration.minimap_screen_x + self.calibration.minimap_screen_width / 2) * screen_width
        )
        minimap_center_screen_y = int(
            (self.calibration.minimap_screen_y + self.calibration.minimap_screen_height / 2) * screen_height
        )
        
        # Offset = world position - minimap center in world
        self.calibration.world_offset_x = known_world_position[0]
        self.calibration.world_offset_y = known_world_position[1]
        
        # Calculate minimap scale
        minimap_width_px = int(self.calibration.minimap_screen_width * screen_width)
        minimap_coverage_meters = 400  # Approximate coverage of minimap
        
        self.calibration.minimap_scale = minimap_width_px / minimap_coverage_meters
        
        self.calibration.is_calibrated = True
        self.calibration.calibration_confidence = 0.8
        self.calibration.last_calibrated = time.time()
        
        # Add reference point
        self.reference_points.append({
            "world": known_world_position,
            "screen": known_screen_position,
            "confidence": 1.0,
        })
        
        self._log.info(
            f"Calibrated for {map_name}: "
            f"scale={self.calibration.minimap_scale:.4f} px/m, "
            f"offset=({self.calibration.world_offset_x:.0f}, {self.calibration.world_offset_y:.0f})"
        )
        
        return True
    
    def add_reference_point(
        self,
        world_pos: Tuple[float, float],
        screen_pos: Tuple[float, float],
        confidence: float = 0.8,
    ) -> None:
        """Add a reference point for improved accuracy"""
        
        self.reference_points.append({
            "world": world_pos,
            "screen": screen_pos,
            "confidence": confidence,
        })
        
        # Keep last 20 reference points
        if len(self.reference_points) > 20:
            self.reference_points = self.reference_points[-20:]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CONVERSIONS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def minimap_to_world(
        self,
        minimap_x: float,
        minimap_y: float,
    ) -> ScreenToWorld:
        """
        Convert minimap coordinates to world coordinates.
        
        Args:
            minimap_x: X position on minimap (0 to minimap_width)
            minimap_y: Y position on minimap (0 to minimap_height)
            
        Returns:
            ScreenToWorld with world coordinates
        """
        
        if not self.calibration.is_calibrated:
            return ScreenToWorld(
                screen_x=minimap_x,
                screen_y=minimap_y,
                world_x=minimap_x,
                world_y=minimap_y,
                confidence=0.1,
                source="uncalibrated",
            )
        
        # Convert minimap pixel position to world meters
        # Center of minimap = player position
        minimap_center_x = self.calibration.minimap_screen_width * self.calibration.screen_width / 2
        minimap_center_y = self.calibration.minimap_screen_height * self.calibration.screen_height / 2
        
        # Offset from center
        dx_px = minimap_x - minimap_center_x
        dy_px = minimap_y - minimap_center_y
        
        # Convert to meters
        dx_meters = dx_px / self.calibration.minimap_scale
        dy_meters = dy_px / self.calibration.minimap_scale  # Same scale for X and Y
        
        # World position = player position + offset
        world_x = self.calibration.world_offset_x + dx_meters
        world_y = self.calibration.world_offset_y + dy_meters
        
        # Clamp to map bounds
        world_x = max(0, min(self.calibration.map_width_meters, world_x))
        world_y = max(0, min(self.calibration.map_height_meters, world_y))
        
        return ScreenToWorld(
            screen_x=minimap_x,
            screen_y=minimap_y,
            world_x=world_x,
            world_y=world_y,
            confidence=self.calibration.calibration_confidence,
            source="minimap",
        )
    
    def screen_to_world(
        self,
        screen_x: float,
        screen_y: float,
        screen_width: int = 1920,
        screen_height: int = 1080,
    ) -> ScreenToWorld:
        """
        Convert screen coordinates to world coordinates.
        
        Uses interpolation from reference points for better accuracy.
        """
        
        # Check if point is within minimap region
        minimap_x1 = self.calibration.minimap_screen_x * screen_width
        minimap_y1 = self.calibration.minimap_screen_y * screen_height
        minimap_x2 = (self.calibration.minimap_screen_x + self.calibration.minimap_screen_width) * screen_width
        minimap_y2 = (self.calibration.minimap_screen_y + self.calibration.minimap_screen_height) * screen_height
        
        if minimap_x1 <= screen_x <= minimap_x2 and minimap_y1 <= screen_y <= minimap_y2:
            # On minimap - use minimap conversion
            rel_x = screen_x - minimap_x1
            rel_y = screen_y - minimap_y1
            return self.minimap_to_world(rel_x, rel_y)
        
        # Off minimap - use reference point interpolation
        return self._interpolate_position(screen_x, screen_y)
    
    def world_to_screen(
        self,
        world_x: float,
        world_y: float,
        screen_width: int = 1920,
        screen_height: int = 1080,
    ) -> Tuple[float, float]:
        """
        Convert world coordinates to screen coordinates.
        Reverse of screen_to_world.
        """
        
        if not self.calibration.is_calibrated:
            return (world_x, world_y)
        
        # Calculate offset from player
        dx_meters = world_x - self.calibration.world_offset_x
        dy_meters = world_y - self.calibration.world_offset_y
        
        # Convert to pixels
        dx_px = dx_meters * self.calibration.minimap_scale
        dy_px = dy_meters * self.calibration.minimap_scale
        
        # Minimap center in screen pixels
        minimap_center_x = int(
            (self.calibration.minimap_screen_x + self.calibration.minimap_screen_width / 2) * screen_width
        )
        minimap_center_y = int(
            (self.calibration.minimap_screen_y + self.calibration.minimap_screen_height / 2) * screen_height
        )
        
        screen_x = minimap_center_x + dx_px
        screen_y = minimap_center_y + dy_px
        
        return (screen_x, screen_y)
    
    def _interpolate_position(self, screen_x: float, screen_y: float) -> ScreenToWorld:
        """Interpolate position from reference points"""
        
        if len(self.reference_points) < 2:
            # Fallback to simple scaling
            world_x = (screen_x / self.calibration.screen_width) * self.calibration.map_width_meters
            world_y = (screen_y / self.calibration.screen_height) * self.calibration.map_height_meters
            
            return ScreenToWorld(
                screen_x=screen_x,
                screen_y=screen_y,
                world_x=world_x,
                world_y=world_y,
                confidence=0.2,
                source="interpolated",
            )
        
        # Weighted average of nearest reference points
        total_weight = 0.0
        weighted_x = 0.0
        weighted_y = 0.0
        
        for ref in self.reference_points[-5:]:
            ref_screen = ref["screen"]
            ref_world = ref["world"]
            
            # Distance-based weight
            dist = np.sqrt(
                (screen_x - ref_screen[0])**2 + (screen_y - ref_screen[1])**2
            )
            
            if dist < 1:
                dist = 1
            
            weight = ref["confidence"] / dist
            total_weight += weight
            weighted_x += ref_world[0] * weight
            weighted_y += ref_world[1] * weight
        
        if total_weight > 0:
            world_x = weighted_x / total_weight
            world_y = weighted_y / total_weight
        else:
            world_x = screen_x
            world_y = screen_y
        
        return ScreenToWorld(
            screen_x=screen_x,
            screen_y=screen_y,
            world_x=world_x,
            world_y=world_y,
            confidence=min(0.8, total_weight / 10),
            source="interpolated",
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # UTILITIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def distance_world(self, pos1: Tuple[float, float], pos2: Tuple[float, float]) -> float:
        """Calculate distance between two world positions in meters"""
        return np.sqrt((pos1[0] - pos2[0])**2 + (pos1[1] - pos2[1])**2)
    
    def distance_screen(self, pos1: Tuple[float, float], pos2: Tuple[float, float]) -> float:
        """Calculate distance between two screen positions in pixels"""
        return np.sqrt((pos1[0] - pos2[0])**2 + (pos1[1] - pos2[1])**2)
    
    def is_in_safe_zone(
        self,
        world_x: float,
        world_y: float,
        zone_center: Tuple[float, float],
        zone_radius: float,
    ) -> bool:
        """Check if world position is inside safe zone"""
        return self.distance_world((world_x, world_y), zone_center) <= zone_radius
    
    def get_map_bounds(self) -> Tuple[float, float, float, float]:
        """Get map bounds (min_x, min_y, max_x, max_y)"""
        return (0, 0, self.calibration.map_width_meters, self.calibration.map_height_meters)
    
    def update_player_position(self, world_x: float, world_y: float) -> None:
        """Update the player's known world position (for continuous calibration)"""
        
        self.calibration.world_offset_x = world_x
        self.calibration.world_offset_y = world_y
    
    def get_stats(self) -> Dict[str, Any]:
        """Get coordinate mapper statistics"""
        return {
            "calibration": {
                "map": self.calibration.map_name,
                "is_calibrated": self.calibration.is_calibrated,
                "confidence": round(self.calibration.calibration_confidence, 3),
                "scale": round(self.calibration.minimap_scale, 4),
                "offset": (
                    round(self.calibration.world_offset_x, 1),
                    round(self.calibration.world_offset_y, 1),
                ),
            },
            "reference_points": len(self.reference_points),
        }
    
    def reset(self) -> None:
        """Reset coordinate mapper"""
        self.calibration = MapCalibration()
        self.reference_points.clear()
        self._log.info("Coordinate mapper reset")