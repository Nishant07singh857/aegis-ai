from .structured_logger import get_production_logger
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
