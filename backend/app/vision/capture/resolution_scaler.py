# backend/app/vision/capture/resolution_scaler.py
"""
AEGIS AI - Resolution Scaler
Dynamic resolution scaling based on feed priority.
High priority feeds get full resolution, low priority get reduced.
"""

import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.vision.scaler")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PriorityLevel(str, Enum):
    """Feed priority levels for resolution allocation"""
    CRITICAL = "critical"     # Active fight, clutch - FULL quality
    HIGH = "high"            # Potential fight, star player
    MEDIUM = "medium"        # Normal gameplay
    LOW = "low"             # Looting, camping
    MINIMAL = "minimal"      # Dead spectator, idle

class ResolutionPreset(str, Enum):
    """Resolution presets"""
    UHD = "uhd"         # 3840x2160
    QHD = "qhd"         # 2560x1440
    FHD = "fhd"         # 1920x1080
    HD = "hd"           # 1280x720
    SD = "sd"           # 854x480
    LOW = "low"         # 640x360
    THUMBNAIL = "thumbnail"  # 320x180

@dataclass
class ScalerConfig:
    """Resolution scaler configuration"""
    # Resolution mapping per priority
    resolution_map: Dict[PriorityLevel, Tuple[int, int]] = field(default_factory=lambda: {
        PriorityLevel.CRITICAL: (1920, 1080),
        PriorityLevel.HIGH: (1920, 1080),
        PriorityLevel.MEDIUM: (1280, 720),
        PriorityLevel.LOW: (854, 480),
        PriorityLevel.MINIMAL: (640, 360),
    })
    
    # FPS mapping per priority
    fps_map: Dict[PriorityLevel, int] = field(default_factory=lambda: {
        PriorityLevel.CRITICAL: 30,
        PriorityLevel.HIGH: 30,
        PriorityLevel.MEDIUM: 20,
        PriorityLevel.LOW: 10,
        PriorityLevel.MINIMAL: 5,
    })
    
    # Bitrate mapping (Mbps)
    bitrate_map: Dict[PriorityLevel, int] = field(default_factory=lambda: {
        PriorityLevel.CRITICAL: 8,
        PriorityLevel.HIGH: 6,
        PriorityLevel.MEDIUM: 3,
        PriorityLevel.LOW: 1,
        PriorityLevel.MINIMAL: 0.5,
    })
    
    # Max high-priority feeds
    max_critical_feeds: int = 2
    max_high_feeds: int = 4
    
    # Total bandwidth budget (Mbps)
    total_bandwidth_budget: float = 100.0
    
    # Transition smoothing
    transition_time_ms: int = 500


@dataclass
class FeedAllocation:
    """Current resolution allocation for a feed"""
    source_id: str
    priority: PriorityLevel = PriorityLevel.MEDIUM
    target_width: int = 1280
    target_height: int = 720
    target_fps: int = 20
    target_bitrate: int = 3
    current_width: int = 1280
    current_height: int = 720
    current_fps: int = 20
    is_transitioning: bool = False
    transition_start: float = 0.0
    bandwidth_used: float = 0.0


class ResolutionScaler:
    """
    Dynamic resolution scaling based on feed priority.
    
    Features:
    - Priority-based resolution allocation
    - Bandwidth budget management
    - Smooth transitions between resolutions
    - GPU memory optimization
    - Automatic scaling based on fight activity
    """
    
    def __init__(self, config: Optional[ScalerConfig] = None):
        self.config = config or ScalerConfig()
        self.allocations: Dict[str, FeedAllocation] = {}
        self._log = logger.bind(component="resolution_scaler")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PRIORITY MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def register_feed(self, source_id: str, priority: PriorityLevel = PriorityLevel.MEDIUM) -> FeedAllocation:
        """Register a feed for resolution management"""
        
        resolution = self.config.resolution_map.get(priority, (1280, 720))
        fps = self.config.fps_map.get(priority, 20)
        bitrate = self.config.bitrate_map.get(priority, 3)
        
        allocation = FeedAllocation(
            source_id=source_id,
            priority=priority,
            target_width=resolution[0],
            target_height=resolution[1],
            target_fps=fps,
            target_bitrate=bitrate,
            current_width=resolution[0],
            current_height=resolution[1],
            current_fps=fps,
        )
        
        self.allocations[source_id] = allocation
        
        self._log.info(
            f"Feed registered: {source_id}",
            priority=priority.value,
            resolution=f"{resolution[0]}x{resolution[1]}",
            fps=fps,
        )
        
        return allocation
    
    def set_priority(self, source_id: str, priority: PriorityLevel) -> bool:
        """Change feed priority"""
        
        allocation = self.allocations.get(source_id)
        if not allocation:
            return False
        
        old_priority = allocation.priority
        allocation.priority = priority
        
        # Get new target resolution
        resolution = self.config.resolution_map.get(priority, (1280, 720))
        fps = self.config.fps_map.get(priority, 20)
        bitrate = self.config.bitrate_map.get(priority, 3)
        
        # Check if actually changing
        if (resolution[0] != allocation.target_width or
            resolution[1] != allocation.target_height or
            fps != allocation.target_fps):
            
            allocation.target_width = resolution[0]
            allocation.target_height = resolution[1]
            allocation.target_fps = fps
            allocation.target_bitrate = bitrate
            
            # Start transition
            allocation.is_transitioning = True
            allocation.transition_start = time.time()
            
            self._log.info(
                f"Priority change: {source_id}",
                from_priority=old_priority.value,
                to_priority=priority.value,
                resolution=f"{resolution[0]}x{resolution[1]}",
            )
        
        return True
    
    def set_fight_priority(self, fight_feeds: List[str]) -> None:
        """Set high priority for feeds involved in fights"""
        
        # Count current high-priority feeds
        high_priority = [
            aid for aid, alloc in self.allocations.items()
            if alloc.priority in [PriorityLevel.CRITICAL, PriorityLevel.HIGH]
        ]
        
        # Set fight feeds to critical/high
        for i, source_id in enumerate(fight_feeds):
            if i < self.config.max_critical_feeds:
                self.set_priority(source_id, PriorityLevel.CRITICAL)
            elif i < self.config.max_high_feeds:
                self.set_priority(source_id, PriorityLevel.HIGH)
        
        # Downgrade non-fight feeds if needed
        for source_id in high_priority:
            if source_id not in fight_feeds:
                self.set_priority(source_id, PriorityLevel.MEDIUM)
    
    def set_idle_priority(self, source_id: str) -> None:
        """Set minimal priority for idle/dead feeds"""
        self.set_priority(source_id, PriorityLevel.MINIMAL)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # RESOLUTION CALCULATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_current_resolution(self, source_id: str) -> Optional[Tuple[int, int]]:
        """Get current resolution for a feed"""
        allocation = self.allocations.get(source_id)
        if not allocation:
            return None
        
        # Check if transitioning
        if allocation.is_transitioning:
            elapsed = (time.time() - allocation.transition_start) * 1000
            
            if elapsed >= self.config.transition_time_ms:
                # Transition complete
                allocation.current_width = allocation.target_width
                allocation.current_height = allocation.target_height
                allocation.current_fps = allocation.target_fps
                allocation.is_transitioning = False
            else:
                # Interpolate
                progress = elapsed / self.config.transition_time_ms
                allocation.current_width = int(
                    allocation.current_width + 
                    (allocation.target_width - allocation.current_width) * progress
                )
                allocation.current_height = int(
                    allocation.current_height + 
                    (allocation.target_height - allocation.current_height) * progress
                )
        
        return (allocation.current_width, allocation.current_height)
    
    def get_current_fps(self, source_id: str) -> Optional[int]:
        """Get current FPS for a feed"""
        allocation = self.allocations.get(source_id)
        if not allocation:
            return None
        return allocation.current_fps
    
    def get_current_bitrate(self, source_id: str) -> Optional[int]:
        """Get current bitrate for a feed"""
        allocation = self.allocations.get(source_id)
        if not allocation:
            return None
        return allocation.target_bitrate
    
    def get_bandwidth_usage(self) -> Dict[str, Any]:
        """Get current bandwidth usage"""
        total = 0.0
        per_feed = {}
        
        for source_id, allocation in self.allocations.items():
            # Estimate bandwidth based on resolution and FPS
            pixels = allocation.current_width * allocation.current_height
            fps = allocation.current_fps
            bitrate = allocation.target_bitrate
            
            # Simple bandwidth estimation
            bandwidth = bitrate * (fps / 30) * (pixels / (1920 * 1080))
            allocation.bandwidth_used = bandwidth
            total += bandwidth
            
            per_feed[source_id] = {
                "bandwidth_mbps": round(bandwidth, 2),
                "resolution": f"{allocation.current_width}x{allocation.current_height}",
                "fps": allocation.current_fps,
                "priority": allocation.priority.value,
            }
        
        return {
            "total_bandwidth_mbps": round(total, 2),
            "budget_mbps": self.config.total_bandwidth_budget,
            "utilization_percent": round(total / self.config.total_bandwidth_budget * 100, 1),
            "per_feed": per_feed,
        }
    
    def is_within_budget(self) -> bool:
        """Check if total bandwidth is within budget"""
        usage = self.get_bandwidth_usage()
        return usage["total_bandwidth_mbps"] <= self.config.total_bandwidth_budget
    
    def optimize_for_budget(self) -> None:
        """Reduce quality of lower priority feeds to stay within budget"""
        
        if self.is_within_budget():
            return
        
        # Get feeds sorted by priority (lowest first)
        sorted_feeds = sorted(
            self.allocations.values(),
            key=lambda a: list(PriorityLevel).index(a.priority),
        )
        
        for allocation in sorted_feeds:
            if self.is_within_budget():
                break
            
            # Reduce resolution of this feed
            current_res = (allocation.current_width, allocation.current_height)
            
            if current_res == (1920, 1080):
                allocation.target_width = 1280
                allocation.target_height = 720
                allocation.target_fps = max(15, allocation.target_fps - 5)
            elif current_res == (1280, 720):
                allocation.target_width = 854
                allocation.target_height = 480
                allocation.target_fps = max(10, allocation.target_fps - 5)
            elif current_res == (854, 480):
                allocation.target_width = 640
                allocation.target_height = 360
                allocation.target_fps = max(5, allocation.target_fps - 5)
            
            allocation.is_transitioning = True
            allocation.transition_start = time.time()
            
            self._log.info(
                f"Reduced resolution for {allocation.source_id} to stay within budget",
            )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_allocation(self, source_id: str) -> Optional[Dict[str, Any]]:
        """Get allocation details for a feed"""
        allocation = self.allocations.get(source_id)
        if not allocation:
            return None
        
        return {
            "source_id": allocation.source_id,
            "priority": allocation.priority.value,
            "current": {
                "width": allocation.current_width,
                "height": allocation.current_height,
                "fps": allocation.current_fps,
            },
            "target": {
                "width": allocation.target_width,
                "height": allocation.target_height,
                "fps": allocation.target_fps,
            },
            "bitrate_mbps": allocation.target_bitrate,
            "is_transitioning": allocation.is_transitioning,
            "bandwidth_mbps": round(allocation.bandwidth_used, 2),
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get resolution scaler statistics"""
        return {
            "feeds_managed": len(self.allocations),
            "bandwidth": self.get_bandwidth_usage(),
            "allocations": {
                source_id: self.get_allocation(source_id)
                for source_id in self.allocations
            },
            "within_budget": self.is_within_budget(),
        }
    
    def reset(self) -> None:
        """Reset resolution scaler"""
        self.allocations.clear()
        self._log.info("Resolution scaler reset")