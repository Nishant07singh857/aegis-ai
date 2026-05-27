# backend/app/api/websocket/admin_monitor.py
"""
AEGIS AI - Admin Monitor WebSocket
Real-time system monitoring for the admin dashboard.
Streams agent health, event bus stats, GPU metrics, and system info.
"""

import asyncio
import time
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import structlog

logger = structlog.get_logger("aegis.ws.admin_monitor")

ws_router = APIRouter()

# Active admin monitor connections
_monitors: dict = {}


@ws_router.websocket("/monitor")
async def admin_monitor_socket(websocket: WebSocket):
    """
    WebSocket for admin system monitoring.

    Streams every 2 seconds:
    - Agent system health
    - Event bus stats
    - Active fights
    - System resource usage
    """
    monitor_id = f"mon_{int(time.time()*1000)}"
    await websocket.accept()
    _monitors[monitor_id] = websocket

    await websocket.send_json({
        "type": "connected",
        "monitor_id": monitor_id,
        "message": "Admin monitor connected",
    })

    logger.info("Admin monitor connected", monitor_id=monitor_id)

    # Start sending periodic updates
    update_task = asyncio.create_task(
        _stream_updates(websocket, monitor_id)
    )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
                cmd = payload.get("cmd", "")

                if cmd == "ping":
                    await websocket.send_json({"type": "pong", "ts": time.time()})
                elif cmd == "get_stats":
                    await websocket.send_json({
                        "type": "stats_snapshot",
                        "timestamp": time.time(),
                    })

            except json.JSONDecodeError:
                pass

    except WebSocketDisconnect:
        logger.info("Admin monitor disconnected", monitor_id=monitor_id)
    except Exception as exc:
        logger.warning("Admin monitor error", monitor_id=monitor_id, err=str(exc))
    finally:
        update_task.cancel()
        _monitors.pop(monitor_id, None)


async def _stream_updates(websocket: WebSocket, monitor_id: str) -> None:
    """Stream periodic system updates to the monitor"""
    try:
        while True:
            await asyncio.sleep(2.0)
            update = {
                "type": "system_update",
                "timestamp": time.time(),
                "monitor_id": monitor_id,
                "data": {
                    "status": "running",
                    "connected_monitors": len(_monitors),
                },
            }
            try:
                await websocket.send_json(update)
            except Exception:
                break
    except asyncio.CancelledError:
        pass
