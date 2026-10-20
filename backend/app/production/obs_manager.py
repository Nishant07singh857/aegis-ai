"""
DEPRECATED (Phase 22 / W3, Tech — OBS consolidation).

The canonical OBS client for this product is
``app.broadcast.obs_client.OBSClient`` (hand-rolled obs-websocket v5,
the client the director/broadcast path actually uses). This
``production/`` stack is a parallel mock-era implementation kept ONLY
because ``app.live_production_integration`` still imports
``OBSManager`` from here. Do not add features here; new OBS work goes
through ``broadcast/obs_client.py``. Removing this stack is blocked
on migrating ``live_production_integration.py``.
"""

import asyncio
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
    """
    Central Orchestrator for the OBS Automation Layer.
    Initializes and wires all production micro-controllers together.
    """
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
        """Consumes events directly from the Event Bus"""
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
