from .structured_logger import get_production_logger

logger = get_production_logger("transition")

class TransitionEngine:
    def __init__(self, ws_client):
        self.ws = ws_client
        
    async def execute_cinematic_transition(self, target_scene: str):
        logger.info("Executing Cinematic Transition", target=target_scene)
        await self.ws.send_command("SetCurrentSceneTransition", {"transitionName": "Stinger_Flash"})
        await self.ws.send_command("SetCurrentProgramScene", {"sceneName": target_scene})
