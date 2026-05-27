# backend/app/broadcast/source_manager.py
"""
AEGIS AI - Source Manager
Manages OBS sources for video feeds, overlays, and media.
Handles source creation, configuration, and lifecycle.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.broadcast.sources")


class SourceType(str, Enum):
    """Types of OBS sources"""
    VIDEO_CAPTURE = "video_capture"
    GAME_CAPTURE = "game_capture"
    WINDOW_CAPTURE = "window_capture"
    DISPLAY_CAPTURE = "display_capture"
    MEDIA_SOURCE = "media_source"
    IMAGE = "image"
    TEXT = "text"
    BROWSER = "browser"
    COLOR_SOURCE = "color_source"
    AUDIO_INPUT = "audio_input"
    AUDIO_OUTPUT = "audio_output"
    NDISOURCE = "ndi_source"
    RTMP_STREAM = "rtmp_stream"


@dataclass
class Source:
    """An OBS source"""
    name: str
    source_type: SourceType
    source_id: str = ""
    
    # Configuration
    settings: Dict[str, Any] = field(default_factory=dict)
    
    # State
    active: bool = True
    visible: bool = True
    volume: float = 1.0
    muted: bool = False
    
    # Metadata
    created_at: float = field(default_factory=time.time)
    feed_id: str = ""  # Maps to spectator device
    team_id: str = ""
    player_id: str = ""


class SourceManager:
    """
    Manages OBS sources for broadcast production.
    
    Features:
    - Source creation and configuration
    - Video feed management (16 spectator feeds)
    - Overlay source management
    - Audio source control
    - Source grouping and organization
    """
    
    def __init__(self, obs_client: Any):
        self.obs = obs_client
        self.sources: Dict[str, Source] = {}
        self._log = logger.bind(component="source_manager")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SOURCE MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def create_source(
        self,
        name: str,
        source_type: SourceType,
        settings: Optional[Dict[str, Any]] = None,
        scene_name: Optional[str] = None,
    ) -> bool:
        """Create a new source"""
        
        source_kind = self._get_obs_source_kind(source_type)
        
        response = await self.obs.send_command("CreateInput", {
            "inputName": name,
            "inputKind": source_kind,
            "inputSettings": settings or {},
            "sceneName": scene_name,
        })
        
        if response.success:
            self.sources[name] = Source(
                name=name,
                source_type=source_type,
                source_id=response.data.get("inputUuid", ""),
                settings=settings or {},
            )
            self._log.info(f"Source created: {name} ({source_type.value})")
            return True
        
        return False
    
    async def remove_source(self, name: str) -> bool:
        """Remove a source"""
        response = await self.obs.send_command("RemoveInput", {"inputName": name})
        
        if response.success:
            self.sources.pop(name, None)
            return True
        return False
    
    async def update_settings(self, name: str, settings: Dict[str, Any]) -> bool:
        """Update source settings"""
        response = await self.obs.send_command("SetInputSettings", {
            "inputName": name,
            "inputSettings": settings,
        })
        
        if response.success and name in self.sources:
            self.sources[name].settings.update(settings)
            return True
        return False
    
    async def set_volume(self, name: str, volume: float) -> bool:
        """Set source volume"""
        success = await self.obs.set_volume(name, volume)
        if success and name in self.sources:
            self.sources[name].volume = volume
        return success
    
    async def set_mute(self, name: str, muted: bool) -> bool:
        """Mute/unmute source"""
        success = await self.obs.set_mute(name, muted)
        if success and name in self.sources:
            self.sources[name].muted = muted
        return success
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FEED MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def setup_spectator_feeds(self, num_feeds: int = 16) -> bool:
        """Setup spectator feed sources"""
        
        for i in range(1, num_feeds + 1):
            feed_name = f"Spectator Feed {i}"
            
            settings = {
                "device": f"device_{i}",
                "resolution": "1920x1080",
                "fps": 30,
            }
            
            await self.create_source(feed_name, SourceType.VIDEO_CAPTURE, settings)
            
            if feed_name in self.sources:
                self.sources[feed_name].feed_id = f"feed_{i:03d}"
        
        self._log.info(f"Spectator feeds setup: {num_feeds}")
        return True
    
    async def assign_feed_to_team(self, feed_name: str, team_id: str) -> bool:
        """Assign a feed to a team"""
        if feed_name in self.sources:
            self.sources[feed_name].team_id = team_id
            return True
        return False
    
    async def setup_browser_overlays(self) -> bool:
        """Setup browser source overlays"""
        
        overlay_configs = [
            ("Scoreboard Overlay", "http://localhost:3000/overlays/scoreboard", 1920, 1080),
            ("Kill Feed Overlay", "http://localhost:3000/overlays/killfeed", 400, 600),
            ("Minimap Overlay", "http://localhost:3000/overlays/minimap", 300, 300),
            ("Commentary Overlay", "http://localhost:3000/overlays/commentary", 1920, 100),
            ("Team Panel Overlay", "http://localhost:3000/overlays/teampanel", 300, 400),
        ]
        
        for name, url, width, height in overlay_configs:
            settings = {
                "url": url,
                "width": width,
                "height": height,
                "fps": 30,
            }
            
            await self.create_source(name, SourceType.BROWSER, settings)
        
        self._log.info(f"Browser overlays setup: {len(overlay_configs)}")
        return True
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HELPERS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _get_obs_source_kind(self, source_type: SourceType) -> str:
        """Map SourceType to OBS input kind"""
        mapping = {
            SourceType.VIDEO_CAPTURE: "dshow_input",
            SourceType.GAME_CAPTURE: "game_capture",
            SourceType.WINDOW_CAPTURE: "window_capture",
            SourceType.DISPLAY_CAPTURE: "monitor_capture",
            SourceType.MEDIA_SOURCE: "ffmpeg_source",
            SourceType.IMAGE: "image_source",
            SourceType.TEXT: "text_gdiplus",
            SourceType.BROWSER: "browser_source",
            SourceType.COLOR_SOURCE: "color_source",
            SourceType.AUDIO_INPUT: "wasapi_input_capture",
            SourceType.AUDIO_OUTPUT: "wasapi_output_capture",
            SourceType.NDISOURCE: "ndi_source",
            SourceType.RTMP_STREAM: "rtmp_source",
        }
        return mapping.get(source_type, "dshow_input")
    
    def get_source(self, name: str) -> Optional[Source]:
        """Get source by name"""
        return self.sources.get(name)
    
    def get_feeds_for_team(self, team_id: str) -> List[Source]:
        """Get all feeds assigned to a team"""
        return [s for s in self.sources.values() if s.team_id == team_id]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get source manager statistics"""
        return {
            "total_sources": len(self.sources),
            "sources": {
                name: {
                    "type": source.source_type.value,
                    "feed_id": source.feed_id,
                    "team_id": source.team_id,
                    "volume": round(source.volume, 2),
                    "muted": source.muted,
                }
                for name, source in self.sources.items()
            },
        }