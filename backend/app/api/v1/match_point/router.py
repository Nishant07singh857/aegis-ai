"""Match Point / Smash Rule API router — mounted at /api/v1 by app/main.py.

Auth model:

  * GET  /match-point/status    — any authenticated user (viewer included)
  * POST /match-point/evaluate  — operator+ (decide who is on match point
                                 from the posted standings)
  * POST /match-point/config    — operator+ (reconfigure the rules engine)

Status codes: 401 unauthenticated, 403 insufficient role, 422 invalid config
or standings payload.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import TokenPayload, get_current_user, require_operator
from app.services.match_point import (
    InvalidMatchPointConfigError,
    match_point_service,
)

from .schemas import (
    MatchPointConfigUpdate,
    MatchPointEvaluateRequest,
    MatchPointState,
)

router = APIRouter(prefix="/match-point", tags=["Match Point"])


@router.get("/status", response_model=MatchPointState)
async def get_status(user: TokenPayload = Depends(get_current_user)):
    return match_point_service.snapshot()


@router.post("/evaluate", response_model=MatchPointState)
async def evaluate(
    payload: MatchPointEvaluateRequest,
    user: TokenPayload = Depends(require_operator),
):
    return await match_point_service.evaluate(
        standings=[row.model_dump() for row in payload.standings],
        day=payload.day,
        updated_by=user.username,
    )


@router.post("/config", response_model=MatchPointState)
async def update_config(
    payload: MatchPointConfigUpdate,
    user: TokenPayload = Depends(require_operator),
):
    updates = payload.model_dump(exclude_unset=True)
    try:
        return await match_point_service.set_config(
            updated_by=user.username, **updates
        )
    except InvalidMatchPointConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
