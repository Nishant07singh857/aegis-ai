"""Stream API router — mounted at /api/v1 by app/main.py.

POST /stream/start and POST /stream/stop require operator+ auth (they spend
CPU and broadcast to the world). GET /stream/status is an authenticated
read — any logged-in user (JWT or X-API-Key) may watch stream state.

Stream keys NEVER appear here: they are read from env vars inside
app/streaming, and status reports only ``*_configured`` booleans.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user, require_operator
from app.streaming import get_stream_service
from app.streaming.destinations import DestinationError

from .schemas import StreamActionResponse, StreamStartRequest, StreamStatusResponse

router = APIRouter()


def _stream_error_to_http(exc: Exception) -> HTTPException:
    """Map service errors to HTTP without ever leaking key material."""
    if isinstance(exc, DestinationError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )
    if isinstance(exc, ValueError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )
    if isinstance(exc, RuntimeError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Stream operation failed.",
    )


@router.post(
    "/stream/start",
    response_model=StreamActionResponse,
    dependencies=[Depends(require_operator)],
)
async def start_stream(payload: StreamStartRequest):
    service = get_stream_service()
    if payload.director_url:
        service.config.director_url = payload.director_url
    try:
        result = await service.start(
            destinations_list=payload.destinations,
            custom_rtmp_url=payload.custom_rtmp_url,
            resolution=payload.resolution,
        )
    except Exception as exc:  # noqa: BLE001 — mapped above, no key leak
        raise _stream_error_to_http(exc)
    return StreamActionResponse(
        message=f"Stream started → {', '.join(result['destinations_requested'])}.",
        state=result["state"],
    )


@router.post(
    "/stream/stop",
    response_model=StreamActionResponse,
    dependencies=[Depends(require_operator)],
)
async def stop_stream():
    service = get_stream_service()
    result = await service.stop()
    return StreamActionResponse(message="Stream stopped.", state=result["state"])


@router.get(
    "/stream/status",
    response_model=StreamStatusResponse,
    dependencies=[Depends(get_current_user)],
)
async def stream_status():
    return StreamStatusResponse(**get_stream_service().status())
