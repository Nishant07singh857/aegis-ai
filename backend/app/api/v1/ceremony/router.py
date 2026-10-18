"""WWCD ceremony API router — mounted at /api/v1 by app/main.py.

Auth model:

  * GET  /ceremony/state    — any authenticated user (viewer included)
  * POST /ceremony/start    — operator+ (begins the ceremony for a winner)
  * POST /ceremony/advance  — operator+ (next ceremony step, strict order)
  * POST /ceremony/reset    — operator+ (back to idle, winner cleared)

Status codes: 401 unauthenticated, 403 insufficient role, 404 unknown
winner_team_id, 409 invalid transition (advance before start / past done /
start while running), 422 missing winner identity.

Winner identity: when ``winner_team_id`` is given without ``team_name``, the
name is resolved against the real teams service (DB when reachable,
otherwise the seeded demo roster — same source as GET /api/v1/teams). When
only ``team_name`` is given it is used as-is. Stats and interview payloads
are passed through verbatim and never invented.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import TokenPayload, get_current_user, require_operator
from app.api.v1.teams.service import TeamService
from app.services.ceremony import (
    InvalidCeremonyTransitionError,
    ceremony_service,
)

from .schemas import CeremonyStartRequest, CeremonyState

router = APIRouter(prefix="/ceremony", tags=["Ceremony"])


def _transition_error(exc: InvalidCeremonyTransitionError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail=str(exc)
    )


@router.get("/state", response_model=CeremonyState)
async def get_state(user: TokenPayload = Depends(get_current_user)):
    return ceremony_service.snapshot()


@router.post("/start", response_model=CeremonyState)
async def start(
    payload: CeremonyStartRequest, user: TokenPayload = Depends(require_operator)
):
    team_name = payload.team_name
    if payload.winner_team_id and not team_name:
        team = await TeamService(None).get_team(payload.winner_team_id)
        if team is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"Unknown winner_team_id {payload.winner_team_id!r}: no "
                    "such team in the teams service."
                ),
            )
        team_name = team.name
    try:
        return await ceremony_service.start(
            winner_team_id=payload.winner_team_id,
            team_name=team_name,
            stats=payload.stats,
            interview=payload.interview,
            updated_by=user.username,
        )
    except InvalidCeremonyTransitionError as exc:
        # Missing winner identity is a request problem, not a transition
        # conflict; everything else is a 409.
        if "without a winner" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        raise _transition_error(exc) from exc


@router.post("/advance", response_model=CeremonyState)
async def advance(user: TokenPayload = Depends(require_operator)):
    try:
        return await ceremony_service.advance(updated_by=user.username)
    except InvalidCeremonyTransitionError as exc:
        raise _transition_error(exc) from exc


@router.post("/reset", response_model=CeremonyState)
async def reset(user: TokenPayload = Depends(require_operator)):
    return await ceremony_service.reset(updated_by=user.username)
