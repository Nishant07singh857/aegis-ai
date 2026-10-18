"""Director API router — mounted at /api/v1 by app/main.py.

The director is the single source of truth for what's on the broadcast
output page. Auth model:

  * GET  /director/state        — any authenticated user (viewer included)
  * POST /director/mode         — operator+ (explicit auto<->manual flips)
  * POST /director/scene        — operator+, MANUAL mode only
  * POST /director/overlays     — operator+, MANUAL mode only
  * POST /director/lower-third  — operator+, MANUAL mode only

Status codes: 401 unauthenticated, 403 insufficient role, 422 invalid
values, 409 operator write while the director is in AUTO mode (the request
is valid but conflicts with the current mode — flip to manual first).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import TokenPayload, get_current_user, require_operator
from app.services.director_service import DirectorModeError, director_service

from .schemas import (
    DirectorState,
    LowerThirdRequest,
    ModeRequest,
    OverlaysRequest,
    SceneRequest,
)

router = APIRouter(prefix="/director", tags=["Director"])


def _auto_mode_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "Director is in AUTO mode: operator scene/overlay/lower-third "
            "writes are rejected. Switch to MANUAL first via "
            'POST /api/v1/director/mode {"mode": "manual"}.'
        ),
    )


@router.get("/state", response_model=DirectorState)
async def get_director_state(
    _user: TokenPayload = Depends(get_current_user),
):
    """Current director state. Any authenticated user, viewers included."""
    return director_service.snapshot()


@router.post("/mode", response_model=DirectorState)
async def set_director_mode(
    payload: ModeRequest,
    user: TokenPayload = Depends(require_operator),
):
    """Explicit auto<->manual transition. Operator or admin."""
    return await director_service.set_mode(
        payload.mode.value, updated_by=user.username
    )


@router.post("/scene", response_model=DirectorState)
async def set_director_scene(
    payload: SceneRequest,
    user: TokenPayload = Depends(require_operator),
):
    """Operator scene select. Rejected with 409 while in AUTO mode."""
    try:
        return await director_service.set_scene(
            payload.scene.value, updated_by=user.username
        )
    except DirectorModeError:
        raise _auto_mode_conflict()


@router.post("/overlays", response_model=DirectorState)
async def set_director_overlays(
    payload: OverlaysRequest,
    user: TokenPayload = Depends(require_operator),
):
    """Operator overlay toggles (partial update). 409 while in AUTO mode."""
    try:
        return await director_service.set_overlays(
            scorebug=payload.scorebug,
            killfeed=payload.killfeed,
            updated_by=user.username,
        )
    except DirectorModeError:
        raise _auto_mode_conflict()


@router.post("/lower-third", response_model=DirectorState)
async def set_director_lower_third(
    payload: LowerThirdRequest,
    user: TokenPayload = Depends(require_operator),
):
    """Operator lower-third control. 409 while in AUTO mode."""
    try:
        return await director_service.set_lower_third(
            visible=payload.visible,
            title=payload.title,
            subtitle=payload.subtitle,
            updated_by=user.username,
        )
    except DirectorModeError:
        raise _auto_mode_conflict()
