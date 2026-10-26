"""Stream API router — mounted at /api/v1 by app/main.py.

Phase 32 (K-C2) — TOPOLOGY DECISION, FINAL: the API-driven stream path is
DEPRECATED and disabled. It could never work in the production backend
image (Xvfb/Chromium/FFmpeg are absent there, so start always failed
with 409), and stuffing a display stack into the API image would conflate
the API server with a capture box. THE OFFICIAL TOPOLOGY is the shell
streamer: ``infrastructure/docker/Dockerfile.streamer`` +
``streamer-entrypoint.sh``, run via
``infrastructure/docker/docker-compose.streaming.yml`` (profile
``streaming``) — the image that actually ships Chromium/FFmpeg/Xvfb and
renders /broadcast/output into RTMP.

Consequences, implemented here (no live-looking dead path left):

  * POST /stream/start and POST /stream/stop return 410 Gone with the
    streamer instructions in the detail — they never spawn a pipeline.
  * GET /stream/status returns state "deprecated" (same response shape,
    so existing pollers do not crash) with the guidance in last_error.
    It does NOT report the shell streamer's state: the API process cannot
    see that container. A status value of "idle"/"running" here would be
    theatre, so it is not emitted.
  * ``app.streaming.service.StreamService`` is retained ONLY as a
    library for its pure command builders and its unit tests; it is no
    longer reachable from any HTTP route. See its module docstring.

Stream keys NEVER appear here: they are read from env vars inside
app/streaming, and status reports only ``*_configured`` booleans.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user, require_operator
from app.streaming.destinations import DestinationError

from .schemas import StreamActionResponse, StreamStartRequest, StreamStatusResponse

router = APIRouter()

#: Shared 410 detail for the disabled API stream path.
_DEPRECATED_DETAIL = (
    "The API-driven stream path is deprecated (Phase 32, K-C2) and cannot "
    "start a stream: the production backend image has no Xvfb/Chromium/"
    "FFmpeg. Use the official shell streamer topology instead: "
    "docker compose -f infrastructure/docker/docker-compose.prod.yml "
    "-f infrastructure/docker/docker-compose.streaming.yml "
    "--profile streaming up -d --build "
    "(see docs/STREAMING.md). No real RTMP stream has been demonstrated "
    "from this VM; the first real RTMP push must be proven on the "
    "production rig."
)


def _gone() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_410_GONE, detail=_DEPRECATED_DETAIL
    )


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
    """DEPRECATED (Phase 32, K-C2): always 410. Use the shell streamer."""
    raise _gone()


@router.post(
    "/stream/stop",
    response_model=StreamActionResponse,
    dependencies=[Depends(require_operator)],
)
async def stop_stream():
    """DEPRECATED (Phase 32, K-C2): always 410. Use the shell streamer."""
    raise _gone()


@router.get(
    "/stream/status",
    response_model=StreamStatusResponse,
    dependencies=[Depends(get_current_user)],
)
async def stream_status():
    """DEPRECATED (Phase 32, K-C2): reports state "deprecated", never a
    live/idle claim about a streamer this process cannot see."""
    return StreamStatusResponse(
        state="deprecated",
        last_error=_DEPRECATED_DETAIL,
    )
