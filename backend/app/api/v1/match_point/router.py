"""Match Point / Smash Rule API router — mounted at /api/v1 by app/main.py.

Auth model:

  * GET  /match-point/status    — any authenticated user (viewer included)
  * POST /match-point/evaluate  — operator+ (decide who is on match point
                                 from the posted standings)
  * POST /match-point/config    — operator+ (reconfigure the rules engine)

Smash Rule championship engine (Phase 20):

  * GET  /match-point/smash/status        — any authenticated user
  * POST /match-point/smash/configure     — operator+ (configure the smash
                                           state machine; resets it)
  * POST /match-point/smash/match-result  — operator+ (record a completed
                                           match; advances the state machine
                                           and may declare the champion)
  * POST /match-point/smash/reset         — operator+ (clear smash state)

Status codes: 401 unauthenticated, 403 insufficient role, 422 invalid config
or standings payload.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import TokenPayload, get_current_user, require_operator
from app.services.match_point import (
    InvalidMatchPointConfigError,
    InvalidSmashConfigError,
    SmashRuleTransitionError,
    match_point_service,
)

from .schemas import (
    MatchPointConfigUpdate,
    MatchPointEvaluateRequest,
    MatchPointState,
    SmashConfigUpdate,
    SmashMatchResultRequest,
    SmashTransitionResponse,
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


# ── Smash Rule championship engine ────────────────────────────────────


@router.get("/smash/status", response_model=Dict[str, Any])
async def smash_status(user: TokenPayload = Depends(get_current_user)):
    """Full smash-rule engine snapshot: state, target, match-point teams
    with beat progression, champion, broadcast beats and directives."""
    return match_point_service.smash_status()


@router.post("/smash/configure", response_model=Dict[str, Any])
async def smash_configure(
    payload: SmashConfigUpdate,
    user: TokenPayload = Depends(require_operator),
):
    try:
        return await match_point_service.configure_smash(
            updated_by=user.username, **payload.model_dump(exclude_unset=True)
        )
    except InvalidSmashConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post("/smash/match-result", response_model=SmashTransitionResponse)
async def smash_match_result(
    payload: SmashMatchResultRequest,
    user: TokenPayload = Depends(require_operator),
):
    try:
        return await match_point_service.record_smash_match_result(
            match_number=payload.match_number,
            winner_team_id=payload.winner_team_id,
            standings=[row.model_dump() for row in payload.standings],
            updated_by=user.username,
        )
    except SmashRuleTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post("/smash/reset", response_model=Dict[str, Any])
async def smash_reset(user: TokenPayload = Depends(require_operator)):
    return await match_point_service.reset_smash(updated_by=user.username)
