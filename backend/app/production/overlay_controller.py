from .structured_logger import get_production_logger

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
