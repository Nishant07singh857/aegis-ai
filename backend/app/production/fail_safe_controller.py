from .structured_logger import get_production_logger
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
