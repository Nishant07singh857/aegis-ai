# backend/app/analytics/heatmap_generator.py
"""
AEGIS AI - Heatmap Generator
Generates tactical heatmaps for player positions, rotations,
deaths, fights, and zone analysis on BGMI maps.
"""

import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict
import structlog
import numpy as np

logger = structlog.get_logger("aegis.analytics.heatmaps")


class HeatmapType(str, Enum):
    """Types of heatmaps"""
    LANDING = "landing"           # Landing positions
    ROTATION = "rotation"         # Movement paths
    DEATH = "death"              # Death locations
    FIGHT = "fight"             # Fight locations
    ZONE = "zone"               # Zone positions
    LOOT = "loot"              # Loot density
    VEHICLE = "vehicle"         # Vehicle usage
    SURVIVAL = "survival"       # Survival hotspots
    ENGAGEMENT = "engagement"   # Engagement ranges
    UTILITY = "utility"         # Utility usage locations


@dataclass
class Heatmap:
    """A generated heatmap"""
    heatmap_id: str
    heatmap_type: HeatmapType
    title: str = ""
    map_name: str = "erangel"
    
    # Data
    data: np.ndarray = field(default_factory=lambda: np.zeros((80, 80)))
    max_value: float = 0.0
    resolution: int = 80  # Grid size
    
    # Map dimensions
    map_width: float = 8000.0
    map_height: float = 8000.0
    
    # Color scheme
    color_scheme: str = "hot"  # hot, cool, viridis, plasma
    
    # Metadata
    generated_at: float = field(default_factory=time.time)
    data_points: int = 0
    team_ids: List[str] = field(default_factory=list)
    
    # Legend
    legend_min: float = 0.0
    legend_max: float = 100.0
    legend_label: str = "Activity Level"


class HeatmapGenerator:
    """
    Generates tactical heatmaps for analysis.
    
    Features:
    - Multiple heatmap types
    - Configurable resolution
    - Gaussian smoothing
    - Normalization options
    - Map overlay compatibility
    - Multi-team comparison
    """
    
    def __init__(self):
        self.heatmaps: Dict[str, Heatmap] = {}
        
        # Position buffers
        self.position_buffers: Dict[HeatmapType, List[Tuple[float, float]]] = defaultdict(list)
        
        # Map configurations
        self.map_configs = {
            "erangel": {"width": 8000, "height": 8000},
            "miramar": {"width": 8000, "height": 8000},
            "sanhok": {"width": 4000, "height": 4000},
            "vikendi": {"width": 6000, "height": 6000},
            "livik": {"width": 2000, "height": 2000},
            "karakin": {"width": 2000, "height": 2000},
        }
        
        self._log = logger.bind(component="heatmap_generator")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DATA COLLECTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def add_position(
        self,
        heatmap_type: HeatmapType,
        world_x: float,
        world_y: float,
        weight: float = 1.0,
        team_id: str = "",
    ) -> None:
        """Add a position to the heatmap data"""
        # Add multiple times based on weight
        count = max(1, int(weight * 10))
        for _ in range(count):
            self.position_buffers[heatmap_type].append((world_x, world_y))
        
        # Limit buffer size
        if len(self.position_buffers[heatmap_type]) > 50000:
            self.position_buffers[heatmap_type] = self.position_buffers[heatmap_type][-40000:]
    
    def add_positions_batch(
        self,
        heatmap_type: HeatmapType,
        positions: List[Tuple[float, float]],
        weights: Optional[List[float]] = None,
    ) -> None:
        """Add multiple positions at once"""
        for i, (x, y) in enumerate(positions):
            weight = weights[i] if weights and i < len(weights) else 1.0
            self.add_position(heatmap_type, x, y, weight)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HEATMAP GENERATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def generate(
        self,
        heatmap_type: HeatmapType,
        map_name: str = "erangel",
        resolution: int = 80,
        smoothing_sigma: float = 1.5,
        title: str = "",
    ) -> Heatmap:
        """
        Generate a heatmap from collected data.
        
        Args:
            heatmap_type: Type of heatmap to generate
            map_name: Map name for dimensions
            resolution: Grid resolution
            smoothing_sigma: Gaussian smoothing sigma
            title: Heatmap title
            
        Returns:
            Generated Heatmap
        """
        
        map_config = self.map_configs.get(map_name, self.map_configs["erangel"])
        map_width = map_config["width"]
        map_height = map_config["height"]
        
        positions = self.position_buffers.get(heatmap_type, [])
        
        if not positions:
            return Heatmap(
                heatmap_id=f"hm_{int(time.time())}",
                heatmap_type=heatmap_type,
                title=title,
                map_name=map_name,
                map_width=map_width,
                map_height=map_height,
                resolution=resolution,
            )
        
        # Create grid
        grid = np.zeros((resolution, resolution))
        
        # Fill grid
        for world_x, world_y in positions:
            # Convert world coords to grid indices
            grid_x = int((world_x / map_width) * resolution)
            grid_y = int((world_y / map_height) * resolution)
            
            # Clamp to valid range
            grid_x = max(0, min(resolution - 1, grid_x))
            grid_y = max(0, min(resolution - 1, grid_y))
            
            grid[grid_y, grid_x] += 1
        
        # Apply Gaussian smoothing
        if smoothing_sigma > 0:
            from scipy.ndimage import gaussian_filter
            grid = gaussian_filter(grid, sigma=smoothing_sigma)
        
        # Normalize
        max_val = grid.max()
        if max_val > 0:
            grid = grid / max_val * 100
        
        heatmap = Heatmap(
            heatmap_id=f"hm_{int(time.time())}_{heatmap_type.value}",
            heatmap_type=heatmap_type,
            title=title or f"{heatmap_type.value.title()} Heatmap",
            map_name=map_name,
            data=grid,
            max_value=float(max_val),
            resolution=resolution,
            map_width=map_width,
            map_height=map_height,
            data_points=len(positions),
            legend_max=100.0,
        )
        
        self.heatmaps[heatmap.heatmap_id] = heatmap
        
        return heatmap
    
    def generate_all(self, map_name: str = "erangel") -> Dict[HeatmapType, Heatmap]:
        """Generate all heatmap types"""
        
        results = {}
        
        for heatmap_type in HeatmapType:
            if self.position_buffers.get(heatmap_type):
                results[heatmap_type] = self.generate(heatmap_type, map_name)
        
        return results
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ANALYSIS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_hotspots(
        self,
        heatmap: Heatmap,
        threshold: float = 70.0,
        min_area: int = 3,
    ) -> List[Dict[str, Any]]:
        """Identify hotspots in a heatmap"""
        
        hotspots = []
        visited = np.zeros_like(heatmap.data, dtype=bool)
        
        for y in range(heatmap.resolution):
            for x in range(heatmap.resolution):
                if heatmap.data[y, x] >= threshold and not visited[y, x]:
                    # Flood fill to find hotspot region
                    region = []
                    stack = [(y, x)]
                    
                    while stack:
                        cy, cx = stack.pop()
                        if visited[cy, cx]:
                            continue
                        
                        visited[cy, cx] = True
                        
                        if heatmap.data[cy, cx] >= threshold:
                            region.append((cy, cx))
                            
                            # Check neighbors
                            for dy, dx in [(-1,0), (1,0), (0,-1), (0,1)]:
                                ny, nx = cy + dy, cx + dx
                                if 0 <= ny < heatmap.resolution and 0 <= nx < heatmap.resolution:
                                    if not visited[ny, nx]:
                                        stack.append((ny, nx))
                    
                    if len(region) >= min_area:
                        # Calculate center in world coordinates
                        center_y = sum(p[0] for p in region) / len(region)
                        center_x = sum(p[1] for p in region) / len(region)
                        
                        world_x = (center_x / heatmap.resolution) * heatmap.map_width
                        world_y = (center_y / heatmap.resolution) * heatmap.map_height
                        
                        hotspots.append({
                            "center": (round(world_x, 1), round(world_y, 1)),
                            "intensity": round(float(heatmap.data[y, x]), 1),
                            "area": len(region),
                        })
        
        return sorted(hotspots, key=lambda h: h["intensity"], reverse=True)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_heatmap_data(self, heatmap_id: str) -> Optional[Dict[str, Any]]:
        """Get heatmap data for API"""
        
        heatmap = self.heatmaps.get(heatmap_id)
        if not heatmap:
            return None
        
        # Downsample for API response
        data_preview = heatmap.data[::4, ::4].tolist() if heatmap.resolution > 20 else heatmap.data.tolist()
        
        return {
            "heatmap_id": heatmap.heatmap_id,
            "type": heatmap.heatmap_type.value,
            "title": heatmap.title,
            "map_name": heatmap.map_name,
            "resolution": heatmap.resolution,
            "data_points": heatmap.data_points,
            "max_value": round(heatmap.max_value, 1),
            "hotspots": self.get_hotspots(heatmap)[:10],
            "data_preview": data_preview,
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get heatmap generator statistics"""
        return {
            "heatmaps_generated": len(self.heatmaps),
            "buffered_positions": {
                ht.value: len(positions)
                for ht, positions in self.position_buffers.items()
            },
            "by_type": {
                ht.value: len([h for h in self.heatmaps.values() if h.heatmap_type == ht])
                for ht in HeatmapType
            },
        }
    
    def reset(self) -> None:
        """Reset generator"""
        self.heatmaps.clear()
        self.position_buffers.clear()
        self._log.info("Heatmap generator reset")