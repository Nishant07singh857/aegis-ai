# backend/app/broadcast/scene_manager.py
"""
AEGIS AI - Scene Manager
Manages OBS scenes for professional broadcast production.
Handles scene creation, switching, and item management.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.broadcast.scenes")


class SceneType(str, Enum):
    """Types of broadcast scenes"""
    MAIN_CAMERA = "main_camera"
    TEAM_POV = "team_pov"
    PLAYER_POV = "player_pov"
    MAP_OVERVIEW = "map_overview"
    FIGHT_COVERAGE = "fight_coverage"
    CLUTCH_FOCUS = "clutch_focus"
    REPLAY = "replay"
    SCOREBOARD = "scoreboard"
    INTERMISSION = "intermission"
    STARTING_SOON = "starting_soon"
    MATCH_END = "match_end"
    CINEMATIC = "cinematic"
    SPLIT_SCREEN = "split_screen"


@dataclass
class SceneItem:
    """An item in an OBS scene"""
    item_id: int = 0
    name: str = ""
    source_type: str = ""
    visible: bool = True
    position_x: float = 0.0
    position_y: float = 0.0
    width: float = 1920.0
    height: float = 1080.0
    opacity: float = 1.0
    z_index: int = 0


@dataclass
class Scene:
    """An OBS scene"""
    name: str
    scene_type: SceneType = SceneType.MAIN_CAMERA
    items: List[SceneItem] = field(default_factory=list)
    active: bool = False
    created_at: float = field(default_factory=time.time)
    
    # Transitions
    default_transition: str = "cut"
    transition_duration_ms: int = 300


class SceneManager:
    """
    Manages OBS scenes for broadcast production.
    
    Features:
    - Scene creation and deletion
    - Scene switching with transitions
    - Item management (add, remove, modify)
    - Template-based scene setup
    - Scene activation tracking
    """
    
    def __init__(self, obs_client: Any):
        self.obs = obs_client
        self.scenes: Dict[str, Scene] = {}
        self.active_scene: Optional[str] = None
        self.scene_history: List[str] = []
        self._log = logger.bind(component="scene_manager")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SCENE MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def create_scene(self, name: str, scene_type: SceneType = SceneType.MAIN_CAMERA) -> bool:
        """Create a new scene"""
        response = await self.obs.send_command("CreateScene", {"sceneName": name})
        
        if response.success:
            self.scenes[name] = Scene(name=name, scene_type=scene_type)
            self._log.info(f"Scene created: {name}")
            return True
        return False
    
    async def remove_scene(self, name: str) -> bool:
        """Remove a scene"""
        response = await self.obs.send_command("RemoveScene", {"sceneName": name})
        
        if response.success:
            self.scenes.pop(name, None)
            self._log.info(f"Scene removed: {name}")
            return True
        return False
    
    async def switch_to(self, scene_name: str, transition: Optional[str] = None) -> bool:
        """Switch to a scene"""
        if scene_name not in self.scenes and scene_name not in self.obs.scene_list:
            self._log.error(f"Scene not found: {scene_name}")
            return False
        
        # Use specified transition or scene default
        if transition is None and scene_name in self.scenes:
            transition = self.scenes[scene_name].default_transition
        
        if transition:
            await self.obs.send_command("SetCurrentSceneTransition", {
                "transitionName": transition,
            })
        
        success = await self.obs.switch_scene(scene_name)
        
        if success:
            self.scene_history.append(scene_name)
            if len(self.scene_history) > 20:
                self.scene_history = self.scene_history[-20:]
            
            self.active_scene = scene_name
            
            # Update scene active state
            for name, scene in self.scenes.items():
                scene.active = (name == scene_name)
            
            self._log.info(f"Switched to scene: {scene_name}")
        
        return success
    
    async def switch_back(self) -> bool:
        """Switch to previous scene"""
        if len(self.scene_history) >= 2:
            previous = self.scene_history[-2]
            self.scene_history.pop()  # Remove current
            return await self.switch_to(previous)
        return False
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SCENE ITEMS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def add_source_to_scene(
        self,
        scene_name: str,
        source_name: str,
        visible: bool = True,
    ) -> bool:
        """Add a source to a scene"""
        response = await self.obs.send_command("CreateSceneItem", {
            "sceneName": scene_name,
            "sourceName": source_name,
            "sceneItemEnabled": visible,
        })
        
        if response.success:
            item_id = response.data.get("sceneItemId", 0)
            
            if scene_name in self.scenes:
                self.scenes[scene_name].items.append(SceneItem(
                    item_id=item_id,
                    name=source_name,
                    visible=visible,
                ))
            
            return True
        return False
    
    async def remove_source_from_scene(self, scene_name: str, source_name: str) -> bool:
        """Remove a source from a scene"""
        # Find item ID
        item_id = None
        if scene_name in self.scenes:
            for item in self.scenes[scene_name].items:
                if item.name == source_name:
                    item_id = item.item_id
                    break
        
        if item_id is None:
            return False
        
        response = await self.obs.send_command("RemoveSceneItem", {
            "sceneName": scene_name,
            "sceneItemId": item_id,
        })
        
        if response.success and scene_name in self.scenes:
            self.scenes[scene_name].items = [
                i for i in self.scenes[scene_name].items if i.item_id != item_id
            ]
            return True
        
        return False
    
    async def set_item_visible(self, scene_name: str, source_name: str, visible: bool) -> bool:
        """Toggle item visibility"""
        return await self.obs.set_source_visibility(scene_name, source_name, visible)
    
    async def set_item_position(
        self,
        scene_name: str,
        source_name: str,
        x: float,
        y: float,
    ) -> bool:
        """Set item position"""
        item_id = self._get_item_id(scene_name, source_name)
        if item_id is None:
            return False
        
        response = await self.obs.send_command("SetSceneItemTransform", {
            "sceneName": scene_name,
            "sceneItemId": item_id,
            "sceneItemTransform": {"positionX": x, "positionY": y},
        })
        return response.success
    
    def _get_item_id(self, scene_name: str, source_name: str) -> Optional[int]:
        """Get item ID by name"""
        if scene_name in self.scenes:
            for item in self.scenes[scene_name].items:
                if item.name == source_name:
                    return item.item_id
        return None
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TEMPLATES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def setup_broadcast_scenes(self) -> bool:
        """Setup standard broadcast scene collection"""
        
        scene_configs = [
            ("Main Camera", SceneType.MAIN_CAMERA),
            ("Team A POV", SceneType.TEAM_POV),
            ("Team B POV", SceneType.TEAM_POV),
            ("Map Overview", SceneType.MAP_OVERVIEW),
            ("Fight Coverage", SceneType.FIGHT_COVERAGE),
            ("Clutch Focus", SceneType.CLUTCH_FOCUS),
            ("Replay", SceneType.REPLAY),
            ("Scoreboard", SceneType.SCOREBOARD),
            ("Intermission", SceneType.INTERMISSION),
            ("Starting Soon", SceneType.STARTING_SOON),
            ("Match End", SceneType.MATCH_END),
            ("Cinematic", SceneType.CINEMATIC),
        ]
        
        for name, scene_type in scene_configs:
            if name not in self.obs.scene_list:
                await self.create_scene(name, scene_type)
        
        self._log.info(f"Broadcast scenes setup: {len(scene_configs)} scenes")
        return True
    
    def get_stats(self) -> Dict[str, Any]:
        """Get scene manager statistics"""
        return {
            "active_scene": self.active_scene,
            "scene_count": len(self.scenes),
            "scenes": {
                name: {
                    "type": scene.scene_type.value,
                    "items": len(scene.items),
                    "active": scene.active,
                }
                for name, scene in self.scenes.items()
            },
            "history": self.scene_history[-5:],
        }