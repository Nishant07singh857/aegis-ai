# backend/app/overlays/layout_manager.py
"""
AEGIS AI - Layout Manager
Manages overlay layouts, positioning, and responsive design
for different broadcast scenes and screen sizes.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.overlays.layout")


class LayoutType(str, Enum):
    """Types of overlay layouts"""
    FULL_GAME = "full_game"           # Game fullscreen with minimal overlays
    GAME_WITH_SCOREBOARD = "game_with_scoreboard"  # Game + scoreboard
    GAME_WITH_TEAM_PANEL = "game_with_team_panel"  # Game + side panel
    SPLIT_SCREEN = "split_screen"     # Two feeds side by side
    TRIPLE_FEED = "triple_feed"       # Three feeds
    QUAD_FEED = "quad_feed"          # Four feeds
    REPLAY_LAYOUT = "replay_layout"   # Replay with info overlay
    MATCH_START = "match_start"       # Pre-match layout
    MATCH_END = "match_end"          # Post-match layout
    INTERMISSION = "intermission"     # Break layout
    MINIMAL = "minimal"              # Clean feed, no overlays
    CUSTOM = "custom"


@dataclass
class OverlayElement:
    """A single overlay element"""
    element_id: str
    element_type: str  # "scoreboard", "killfeed", "minimap", etc.
    
    # Position (fractional 0-1)
    x: float = 0.0
    y: float = 0.0
    width: float = 1.0
    height: float = 1.0
    
    # Z-index
    z_index: int = 0
    
    # Visibility
    visible: bool = True
    opacity: float = 1.0
    
    # Animation
    animation_in: str = "fade"
    animation_out: str = "fade"
    animation_duration_ms: int = 300


@dataclass
class OverlayLayout:
    """A complete overlay layout"""
    layout_id: str
    layout_type: LayoutType = LayoutType.FULL_GAME
    name: str = ""
    
    # Screen dimensions
    screen_width: int = 1920
    screen_height: int = 1080
    
    # Elements
    elements: List[OverlayElement] = field(default_factory=list)
    
    # Background
    background_color: str = "#000000"
    background_opacity: float = 0.0
    
    # Transitions
    transition_in: str = "fade"
    transition_out: str = "fade"
    transition_duration_ms: int = 500
    
    # Active
    is_active: bool = False
    created_at: float = field(default_factory=time.time)


class LayoutManager:
    """
    Manages overlay layouts for broadcast.
    
    Features:
    - Predefined layout templates
    - Dynamic element positioning
    - Layout switching with animations
    - Responsive scaling for different resolutions
    - Z-index management
    - Layout presets for different match phases
    """
    
    def __init__(self):
        self.layouts: Dict[str, OverlayLayout] = {}
        self.active_layout: Optional[str] = None
        
        # Initialize default layouts
        self._initialize_defaults()
        
        self._log = logger.bind(component="layout_manager")
    
    def _initialize_defaults(self) -> None:
        """Initialize default overlay layouts"""
        
        # Full game layout (minimal overlays)
        full_game = OverlayLayout(
            layout_id="full_game",
            layout_type=LayoutType.FULL_GAME,
            name="Full Game",
            elements=[
                OverlayElement("killfeed", "kill_feed", x=0.78, y=0.05, width=0.20, height=0.40, z_index=10),
                OverlayElement("alive_counter", "alive_counter", x=0.85, y=0.02, width=0.12, height=0.04, z_index=10),
                OverlayElement("minimap", "minimap", x=0.72, y=0.70, width=0.26, height=0.28, z_index=5),
                OverlayElement("zone_indicator", "zone_indicator", x=0.45, y=0.02, width=0.10, height=0.04, z_index=10),
                OverlayElement("commentary_bar", "commentary_bar", x=0.10, y=0.92, width=0.80, height=0.06, z_index=15),
            ],
        )
        
        # Game with scoreboard
        with_scoreboard = OverlayLayout(
            layout_id="game_with_scoreboard",
            layout_type=LayoutType.GAME_WITH_SCOREBOARD,
            name="Game + Scoreboard",
            elements=[
                OverlayElement("scoreboard", "scoreboard", x=0.78, y=0.05, width=0.20, height=0.60, z_index=10),
                OverlayElement("minimap", "minimap", x=0.72, y=0.70, width=0.26, height=0.28, z_index=5),
                OverlayElement("alive_counter", "alive_counter", x=0.02, y=0.02, width=0.08, height=0.04, z_index=10),
                OverlayElement("commentary_bar", "commentary_bar", x=0.10, y=0.92, width=0.80, height=0.06, z_index=15),
            ],
        )
        
        # Game with team panel
        with_panel = OverlayLayout(
            layout_id="game_with_team_panel",
            layout_type=LayoutType.GAME_WITH_TEAM_PANEL,
            name="Game + Team Panel",
            elements=[
                OverlayElement("team_panel", "team_panel", x=0.02, y=0.15, width=0.12, height=0.60, z_index=10),
                OverlayElement("minimap", "minimap", x=0.72, y=0.70, width=0.26, height=0.28, z_index=5),
                OverlayElement("killfeed", "kill_feed", x=0.85, y=0.05, width=0.13, height=0.35, z_index=10),
                OverlayElement("commentary_bar", "commentary_bar", x=0.15, y=0.92, width=0.70, height=0.06, z_index=15),
            ],
        )
        
        # Replay layout
        replay = OverlayLayout(
            layout_id="replay_layout",
            layout_type=LayoutType.REPLAY_LAYOUT,
            name="Replay",
            elements=[
                OverlayElement("replay_header", "replay_header", x=0.05, y=0.02, width=0.40, height=0.08, z_index=20),
                OverlayElement("replay_info", "replay_info", x=0.05, y=0.88, width=0.30, height=0.08, z_index=20),
            ],
            background_color="#111111",
            background_opacity=0.3,
        )
        
        # Match start layout
        match_start = OverlayLayout(
            layout_id="match_start",
            layout_type=LayoutType.MATCH_START,
            name="Match Start",
            elements=[
                OverlayElement("match_intro", "match_intro", x=0.10, y=0.20, width=0.80, height=0.60, z_index=20),
                OverlayElement("sponsor_banner", "sponsor_banner", x=0.25, y=0.85, width=0.50, height=0.10, z_index=15),
            ],
            background_color="#000011",
            background_opacity=0.8,
        )
        
        # Match end layout
        match_end = OverlayLayout(
            layout_id="match_end",
            layout_type=LayoutType.MATCH_END,
            name="Match End",
            elements=[
                OverlayElement("match_result", "match_result", x=0.10, y=0.10, width=0.80, height=0.50, z_index=20),
                OverlayElement("mvp_display", "mvp_display", x=0.20, y=0.55, width=0.60, height=0.30, z_index=20),
                OverlayElement("sponsor_banner", "sponsor_banner", x=0.25, y=0.88, width=0.50, height=0.08, z_index=15),
            ],
            background_color="#000022",
            background_opacity=0.9,
        )
        
        # Minimal layout (clean feed)
        minimal = OverlayLayout(
            layout_id="minimal",
            layout_type=LayoutType.MINIMAL,
            name="Minimal",
            elements=[
                OverlayElement("watermark", "watermark", x=0.88, y=0.92, width=0.10, height=0.06, z_index=5),
            ],
        )
        
        # Store all layouts
        for layout in [full_game, with_scoreboard, with_panel, replay, match_start, match_end, minimal]:
            self.layouts[layout.layout_id] = layout
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # LAYOUT MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def create_layout(self, layout_id: str, layout_type: LayoutType, name: str = "") -> OverlayLayout:
        """Create a custom layout"""
        
        layout = OverlayLayout(
            layout_id=layout_id,
            layout_type=layout_type,
            name=name or layout_id,
        )
        
        self.layouts[layout_id] = layout
        return layout
    
    def add_element(
        self,
        layout_id: str,
        element_id: str,
        element_type: str,
        x: float = 0.0,
        y: float = 0.0,
        width: float = 1.0,
        height: float = 1.0,
        z_index: int = 0,
    ) -> bool:
        """Add an element to a layout"""
        
        layout = self.layouts.get(layout_id)
        if not layout:
            return False
        
        element = OverlayElement(
            element_id=element_id,
            element_type=element_type,
            x=x, y=y, width=width, height=height,
            z_index=z_index,
        )
        
        layout.elements.append(element)
        return True
    
    def remove_element(self, layout_id: str, element_id: str) -> bool:
        """Remove an element from a layout"""
        
        layout = self.layouts.get(layout_id)
        if not layout:
            return False
        
        layout.elements = [e for e in layout.elements if e.element_id != element_id]
        return True
    
    def set_element_visible(self, layout_id: str, element_id: str, visible: bool) -> bool:
        """Toggle element visibility"""
        
        layout = self.layouts.get(layout_id)
        if not layout:
            return False
        
        for element in layout.elements:
            if element.element_id == element_id:
                element.visible = visible
                return True
        return False
    
    def activate_layout(self, layout_id: str) -> bool:
        """Activate a layout"""
        
        if layout_id not in self.layouts:
            return False
        
        # Deactivate current
        if self.active_layout and self.active_layout in self.layouts:
            self.layouts[self.active_layout].is_active = False
        
        # Activate new
        self.layouts[layout_id].is_active = True
        self.active_layout = layout_id
        
        self._log.info(f"Layout activated: {layout_id}")
        return True
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # LAYOUT QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_layout(self, layout_id: str) -> Optional[OverlayLayout]:
        """Get a layout by ID"""
        return self.layouts.get(layout_id)
    
    def get_active_layout(self) -> Optional[OverlayLayout]:
        """Get the currently active layout"""
        if self.active_layout:
            return self.layouts.get(self.active_layout)
        return None
    
    def get_layout_for_phase(self, phase: str) -> str:
        """Get recommended layout for a match phase"""
        
        phase_layouts = {
            "early_game": "full_game",
            "mid_game": "game_with_scoreboard",
            "late_game": "game_with_team_panel",
            "final_circle": "full_game",
            "replay": "replay_layout",
            "match_start": "match_start",
            "match_end": "match_end",
            "intermission": "match_start",
        }
        
        return phase_layouts.get(phase, "full_game")
    
    def get_element_positions(self, layout_id: str) -> Dict[str, Dict[str, Any]]:
        """Get all element positions for a layout"""
        
        layout = self.layouts.get(layout_id)
        if not layout:
            return {}
        
        return {
            element.element_id: {
                "x": element.x,
                "y": element.y,
                "width": element.width,
                "height": element.height,
                "z_index": element.z_index,
                "visible": element.visible,
                "opacity": element.opacity,
            }
            for element in layout.elements
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get layout manager statistics"""
        return {
            "total_layouts": len(self.layouts),
            "active_layout": self.active_layout,
            "layouts": {
                lid: {
                    "name": layout.name,
                    "type": layout.layout_type.value,
                    "elements": len(layout.elements),
                    "active": layout.is_active,
                }
                for lid, layout in self.layouts.items()
            },
        }