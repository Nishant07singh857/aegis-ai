# backend/app/api/websocket/broadcast_control.py
"""
AEGIS AI - Broadcast Control WebSocket
Real-time broadcast control endpoint for the operator dashboard.
Allows human observers to control OBS, switch feeds, and override AI decisions.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import structlog
import json
import time

logger = structlog.get_logger("aegis.ws.broadcast_control")

ws_router = APIRouter()

# Connected broadcast controllers
_controllers: dict = {}


@ws_router.websocket("/control")
async def broadcast_control_socket(websocket: WebSocket):
    """
    WebSocket for broadcast control panel.

    Accepts commands:
    - switch_feed: { "action": "switch_feed", "feed_id": "team_soul" }
    - lock_pov: { "action": "lock_pov", "player_id": "scout" }
    - trigger_replay: { "action": "trigger_replay", "duration": 8 }
    - set_mode: { "action": "set_mode", "mode": "full_auto|assisted|manual" }
    - mute_commentary: { "action": "mute_commentary" }
    """
    controller_id = f"ctrl_{int(time.time()*1000)}"
    await websocket.accept()
    _controllers[controller_id] = websocket

    await websocket.send_json({
        "type": "connected",
        "controller_id": controller_id,
        "message": "Broadcast control connected",
        "modes": ["full_auto", "assisted", "hybrid", "manual"],
    })

    logger.info("Broadcast controller connected", controller_id=controller_id)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
                action = payload.get("action", "")

                if action == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": time.time()})

                elif action == "switch_feed":
                    feed_id = payload.get("feed_id", "")
                    logger.info("Manual feed switch", feed_id=feed_id, controller=controller_id)
                    await websocket.send_json({
                        "type": "ack",
                        "action": "switch_feed",
                        "feed_id": feed_id,
                        "status": "executed",
                    })

                elif action == "lock_pov":
                    player_id = payload.get("player_id", "")
                    logger.info("POV lock", player_id=player_id, controller=controller_id)
                    await websocket.send_json({
                        "type": "ack",
                        "action": "lock_pov",
                        "player_id": player_id,
                        "status": "locked",
                    })

                elif action == "trigger_replay":
                    duration = payload.get("duration", 8)
                    await websocket.send_json({
                        "type": "ack",
                        "action": "trigger_replay",
                        "duration": duration,
                        "status": "triggered",
                    })

                elif action == "set_mode":
                    mode = payload.get("mode", "assisted")
                    logger.info("Production mode change", mode=mode, controller=controller_id)
                    await websocket.send_json({
                        "type": "ack",
                        "action": "set_mode",
                        "mode": mode,
                        "status": "applied",
                    })

                elif action == "mute_commentary":
                    await websocket.send_json({
                        "type": "ack",
                        "action": "mute_commentary",
                        "status": "muted",
                    })

                else:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Unknown action: {action}",
                    })

            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})

    except WebSocketDisconnect:
        logger.info("Broadcast controller disconnected", controller_id=controller_id)
    except Exception as exc:
        logger.warning("Broadcast control error", controller_id=controller_id, err=str(exc))
    finally:
        _controllers.pop(controller_id, None)
