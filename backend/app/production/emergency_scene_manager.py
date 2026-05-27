from .structured_logger import get_production_logger

logger = get_production_logger("emergency")

class EmergencySceneManager:
    def __init__(self, ws_client):
        self.ws = ws_client
        self.safe_scenes = ["SAFE_MAP", "SAFE_CROWD_CAM", "TOURNAMENT_LOGO"]
        
    async def fallback_to_safe(self):
        logger.warning("Activating Safe Fallback Scene")
        await self.ws.send_command("SetCurrentProgramScene", {"sceneName": "SAFE_MAP"})
