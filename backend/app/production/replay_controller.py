import asyncio
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
