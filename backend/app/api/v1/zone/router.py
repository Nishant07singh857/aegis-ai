"""Zone timer API router — mounted at /api/v1 by app/main.py.

Auth model:

  * GET  /zone/state    — any authenticated user (viewer included)
  * POST /zone/phases   — operator+ (configure the phase schedule)
  * POST /zone/advance  — operator+ (next phase; completing the last phase
                         marks the schedule complete)
  * POST /zone/reset    — operator+ (restart the schedule at phase 1)

Status codes: 401 unauthenticated, 403 insufficient role, 409 invalid
operation for the current zone state (advance/reset with no phases, or
advance past completion), 422 invalid phase definitions.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import TokenPayload, get_current_user, require_operator
from app.services.zone import InvalidZoneStateError, zone_service

from .schemas import ZonePhasesRequest, ZoneState

router = APIRouter(prefix="/zone", tags=["Zone"])


@router.get("/state", response_model=ZoneState)
async def get_state(user: TokenPayload = Depends(get_current_user)):
    return zone_service.snapshot()


@router.post("/phases", response_model=ZoneState)
async def set_phases(
    payload: ZonePhasesRequest, user: TokenPayload = Depends(require_operator)
):
    try:
        return await zone_service.set_phases(
            phases=[p.model_dump() for p in payload.phases],
            updated_by=user.username,
        )
    except InvalidZoneStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post("/advance", response_model=ZoneState)
async def advance(user: TokenPayload = Depends(require_operator)):
    try:
        return await zone_service.advance(updated_by=user.username)
    except InvalidZoneStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.post("/reset", response_model=ZoneState)
async def reset(user: TokenPayload = Depends(require_operator)):
    try:
        return await zone_service.reset(updated_by=user.username)
    except InvalidZoneStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
