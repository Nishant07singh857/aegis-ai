from .structured_logger import get_production_logger
import time

logger = get_production_logger("guard")

class ProductionGuard:
    """Enforces broadcast stability rules."""
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
