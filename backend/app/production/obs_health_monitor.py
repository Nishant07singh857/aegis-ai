import asyncio
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
