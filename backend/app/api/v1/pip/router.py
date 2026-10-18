"""PiP API router — mounted at /api/v1 by app/main.py.

Auth model:

  * GET  /pip/state — any authenticated user (viewer incl.)
  * POST /pip/state — operator+ (partial update; omitted fields unchanged)

Every change fans out as a ``pip.state`` frame on /ws/live and is
published on the event bus by the service.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import TokenPayload, get_current_user, require_operator
from app.services.pip import PiPStateError, pip_service

from .schemas import PiPState, PiPStateUpdate

router = APIRouter(prefix="/pip", tags=["PiP"])


@router.get("/state", response_model=PiPState)
async def get_pip_state(
    _user: TokenPayload = Depends(get_current_user),
):
    """Current picture-in-picture layout. Any authenticated user."""
    return pip_service.snapshot()


@router.post("/state", response_model=PiPState)
async def set_pip_state(
    payload: PiPStateUpdate,
    user: TokenPayload = Depends(require_operator),
):
    """Operator PiP update (partial — omit fields to keep them). 422 when
    at least one field is given with an invalid value; 422 when the body
    carries no changes at all."""
    if not payload.has_changes():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No changes provided: set at least one of 'enabled', "
            "'main_source', 'inset_source' or 'inset_position'.",
        )
    try:
        return await pip_service.set(
            enabled=payload.enabled,
            main_source=payload.main_source,
            inset_source=payload.inset_source,
            inset_position=payload.inset_position,
            updated_by=user.username,
        )
    except PiPStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
