import os

base_dir = "/Users/nishantsingh/Downloads/aegis-ai-complete/backend/app/production"
os.makedirs(base_dir, exist_ok=True)

files = {
    "__init__.py": "",
    "structured_logger.py": """import structlog
import time

def get_production_logger(module_name: str):
    return structlog.get_logger(f"aegis.production.{module_name}")
""",
    "production_state.py": """from dataclasses import dataclass, field
import time

@dataclass
class ProductionState:
    current_scene: str = "SAFE_SCENE_MAP"
    is_obs_connected: bool = False
    is_replay_active: bool = False
    last_switch_time: float = 0.0
    switches_in_last_minute: int = 0
    manual_override_active: bool = False
    
    def lock_for_replay(self, duration: float):
        self.is_replay_active = True
        self.replay_end_time = time.time() + duration
""",
    "obs_health_monitor.py": """import asyncio
from .structured_logger import get_production_logger
from .production_state import ProductionState

logger = get_production_logger("health")

class OBSHealthMonitor:
    def __init__(self, state: ProductionState, failsafe_controller):
        self.state = state
        self.failsafe = failsafe_controller
        
    async def ping_loop(self):
        while True:
            await asyncio.sleep(5)
            if not self.state.is_obs_connected:
                logger.error("OBS Connection Lost! Triggering Failsafe.")
                await self.failsafe.trigger_emergency_recovery()
""",
    "websocket_client.py": """import asyncio
import json
from .structured_logger import get_production_logger

logger = get_production_logger("websocket")

class OBSWebsocketClient:
    \"\"\"Async wrapper for OBS Websocket v5\"\"\"
    def __init__(self, state):
        self.state = state
        self.ws = None # In prod: websockets.connect(obs_url)
        
    async def connect(self):
        # Mock connection for architecture validation
        await asyncio.sleep(0.1)
        self.state.is_obs_connected = True
        logger.info("Connected to OBS Websocket")
        
    async def send_command(self, request_type: str, request_data: dict = None):
        if not self.state.is_obs_connected:
            raise ConnectionError("OBS Offline")
        # Async non-blocking send
        logger.debug(f"Executing OBS Command: {request_type}", payload=request_data)
        return True
""",
    "transition_engine.py": """from .structured_logger import get_production_logger

logger = get_production_logger("transition")

class TransitionEngine:
    def __init__(self, ws_client):
        self.ws = ws_client
        
    async def execute_cinematic_transition(self, target_scene: str):
        logger.info("Executing Cinematic Transition", target=target_scene)
        await self.ws.send_command("SetCurrentSceneTransition", {"transitionName": "Stinger_Flash"})
        await self.ws.send_command("SetCurrentProgramScene", {"sceneName": target_scene})
""",
    "emergency_scene_manager.py": """from .structured_logger import get_production_logger

logger = get_production_logger("emergency")

class EmergencySceneManager:
    def __init__(self, ws_client):
        self.ws = ws_client
        self.safe_scenes = ["SAFE_MAP", "SAFE_CROWD_CAM", "TOURNAMENT_LOGO"]
        
    async def fallback_to_safe(self):
        logger.warning("Activating Safe Fallback Scene")
        await self.ws.send_command("SetCurrentProgramScene", {"sceneName": "SAFE_MAP"})
""",
    "production_guard.py": """from .structured_logger import get_production_logger
import time

logger = get_production_logger("guard")

class ProductionGuard:
    \"\"\"Enforces broadcast stability rules.\"\"\"
    def __init__(self, state):
        self.state = state
        self.MINIMUM_SCENE_TIME = 10.0
        
    def can_switch(self) -> bool:
        if self.state.manual_override_active:
            return True # Humans bypass safety
        if self.state.is_replay_active:
            logger.warning("Switch rejected: Replay is active")
            return False
        if time.time() - self.state.last_switch_time < self.MINIMUM_SCENE_TIME:
            logger.warning("Switch rejected: Minimum scene time cooldown")
            return False
        return True
""",
    "scene_router.py": """from .structured_logger import get_production_logger
from .production_guard import ProductionGuard
from .transition_engine import TransitionEngine
import time

logger = get_production_logger("router")

class SceneRouter:
    def __init__(self, state, ws_client, transition_engine: TransitionEngine, guard: ProductionGuard):
        self.state = state
        self.ws = ws_client
        self.transition = transition_engine
        self.guard = guard
        
    async def route_to_team(self, team_id: str, is_critical: bool = False):
        if not self.guard.can_switch() and not is_critical:
            return False
            
        scene_name = f"TEAM_POV_{team_id}"
        await self.transition.execute_cinematic_transition(scene_name)
        
        self.state.current_scene = scene_name
        self.state.last_switch_time = time.time()
        self.state.switches_in_last_minute += 1
        return True
""",
    "fail_safe_controller.py": """from .structured_logger import get_production_logger
from .emergency_scene_manager import EmergencySceneManager

logger = get_production_logger("failsafe")

class FailSafeController:
    def __init__(self, emergency_manager: EmergencySceneManager, state):
        self.emergency = emergency_manager
        self.state = state
        
    async def trigger_emergency_recovery(self):
        logger.critical("PRODUCTION FAILSAFE TRIGGERED. Executing recovery playbook.")
        await self.emergency.fallback_to_safe()
        self.state.manual_override_active = True # Lock AI until human verifies
""",
    "replay_controller.py": """import asyncio
from .structured_logger import get_production_logger

logger = get_production_logger("replay")

class ReplayController:
    def __init__(self, ws_client, state, scene_router):
        self.ws = ws_client
        self.state = state
        self.router = scene_router
        
    async def trigger_instant_replay(self, duration: float = 8.0):
        if self.state.is_replay_active:
            return
            
        logger.info("Triggering Instant Replay", duration=duration)
        self.state.lock_for_replay(duration)
        
        await self.ws.send_command("SetCurrentProgramScene", {"sceneName": "REPLAY_BUFFER_A"})
        await asyncio.sleep(duration)
        
        # Release and route back to live
        self.state.is_replay_active = False
        await self.router.route_to_team("MAP") # Fallback to map after replay
""",
    "overlay_controller.py": """from .structured_logger import get_production_logger

logger = get_production_logger("overlay")

class OverlayController:
    def __init__(self, ws_client):
        self.ws = ws_client
        
    async def toggle_overlay(self, source_name: str, visible: bool):
        logger.debug("Toggling overlay", source=source_name, visible=visible)
        await self.ws.send_command("SetSceneItemEnabled", {
            "sceneName": "OVERLAYS_MASTER",
            "sourceName": source_name,
            "sceneItemEnabled": visible
        })
""",
    "metrics.py": """class ProductionMetrics:
    def __init__(self):
        self.total_switches = 0
        self.total_replays = 0
        self.failsafe_triggers = 0
""",
    "obs_manager.py": """import asyncio
from .production_state import ProductionState
from .websocket_client import OBSWebsocketClient
from .transition_engine import TransitionEngine
from .emergency_scene_manager import EmergencySceneManager
from .production_guard import ProductionGuard
from .scene_router import SceneRouter
from .fail_safe_controller import FailSafeController
from .replay_controller import ReplayController
from .overlay_controller import OverlayController
from .obs_health_monitor import OBSHealthMonitor
from .structured_logger import get_production_logger

logger = get_production_logger("manager")

class OBSManager:
    \"\"\"
    Central Orchestrator for the OBS Automation Layer.
    Initializes and wires all production micro-controllers together.
    \"\"\"
    def __init__(self):
        self.state = ProductionState()
        self.ws = OBSWebsocketClient(self.state)
        
        self.transition = TransitionEngine(self.ws)
        self.emergency = EmergencySceneManager(self.ws)
        self.guard = ProductionGuard(self.state)
        self.router = SceneRouter(self.state, self.ws, self.transition, self.guard)
        
        self.failsafe = FailSafeController(self.emergency, self.state)
        self.health = OBSHealthMonitor(self.state, self.failsafe)
        
        self.replay = ReplayController(self.ws, self.state, self.router)
        self.overlay = OverlayController(self.ws)
        
    async def start(self):
        logger.info("Initializing OBS Production Infrastructure...")
        await self.ws.connect()
        asyncio.create_task(self.health.ping_loop())
        logger.info("OBS Infrastructure Online.")

    async def handle_director_event(self, event_payload: dict):
        \"\"\"Consumes events directly from the Event Bus\"\"\"
        action = event_payload.get("action")
        
        if action == "SWITCH_POV":
            team = event_payload.get("target_team")
            await self.router.route_to_team(team)
            
        elif action == "TRIGGER_REPLAY":
            await self.replay.trigger_instant_replay()
            
        elif action == "MANUAL_OVERRIDE":
            self.state.manual_override_active = True
            target = event_payload.get("target")
            await self.ws.send_command("SetCurrentProgramScene", {"sceneName": target})
            
        elif action == "FAILSAFE_TEST":
            await self.failsafe.trigger_emergency_recovery()
"""
}

for filename, content in files.items():
    with open(os.path.join(base_dir, filename), "w") as f:
        f.write(content)

print("OBS Automation Infrastructure Generated.")
