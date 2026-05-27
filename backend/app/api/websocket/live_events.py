from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import structlog


logger = structlog.get_logger("aegis.ws.live_events")

ws_router = APIRouter()


@ws_router.websocket("/live/{match_id}")
async def live_events_socket(websocket: WebSocket, match_id: str):
    """
    Minimal live-events WebSocket.
    Later: subscribe to Redis/event_bus and forward match-scoped events.
    """
    await websocket.accept()
    await websocket.send_json({"type": "connected", "match_id": match_id})

    try:
        while True:
            # Keep the socket alive and allow basic client pings.
            payload = await websocket.receive_text()
            if payload.strip().lower() in {"ping", "keepalive"}:
                await websocket.send_json({"type": "pong"})
            else:
                await websocket.send_json({"type": "echo", "data": payload})
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected", match_id=match_id)
    except Exception as exc:
        logger.warning("WebSocket error", match_id=match_id, err=str(exc))
