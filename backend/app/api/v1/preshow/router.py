"""Pre-show API router — mounted at /api/v1 by app/main.py.

Auth model:

  * GET  /preshow/state      — any authenticated user (viewer included)
  * POST /preshow/step       — operator+ (move the pre-show to a step)
  * POST /preshow/countdown  — operator+ (set the countdown target)
  * POST /preshow/reset      — operator+ (back to countdown, target cleared)

Status codes: 401 unauthenticated, 403 insufficient role, 422 invalid step or
target timestamp.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import TokenPayload, get_current_user, require_operator
from app.services.preshow import (
    InvalidPreshowStepError,
    preshow_service,
)

from .schemas import CountdownRequest, PreshowState, StepRequest

router = APIRouter(prefix="/preshow", tags=["Preshow"])


@router.get("/state", response_model=PreshowState)
async def get_state(user: TokenPayload = Depends(get_current_user)):
    return preshow_service.snapshot()


@router.post("/step", response_model=PreshowState)
async def set_step(
    payload: StepRequest, user: TokenPayload = Depends(require_operator)
):
    try:
        return await preshow_service.set_step(
            payload.step, updated_by=user.username
        )
    except InvalidPreshowStepError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post("/countdown", response_model=PreshowState)
async def set_countdown(
    payload: CountdownRequest, user: TokenPayload = Depends(require_operator)
):
    try:
        return await preshow_service.set_countdown_target(
            payload.target_iso, updated_by=user.username
        )
    except InvalidPreshowStepError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post("/reset", response_model=PreshowState)
async def reset(user: TokenPayload = Depends(require_operator)):
    return await preshow_service.reset(
        updated_by=user.username
    )
