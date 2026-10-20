"""Anti-cheat review API router — mounted at /api/v1 by app/main.py.

Auth model (role-gated, admin/director only — flags accuse real
players, so the review surface is not broadcast-control general):

  * GET  /anti-cheat/flags            — admin, director (list, filters)
  * GET  /anti-cheat/flags/{id}       — admin, director
  * POST /anti-cheat/flags/{id}/review — admin, director (confirm|dismiss)
  * GET  /anti-cheat/checks           — admin, director (live vs disabled
                                        checks with honest reasons)

Status codes: 401 unauthenticated, 403 insufficient role,
404 unknown flag, 409 already reviewed, 422 invalid action.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TokenPayload, require_role
from app.db.session import get_db
from app.services.vision_intelligence.anti_cheat import checks_registry

from .schemas import (
    AntiCheatFlagResponse,
    CheckInfo,
    ChecksResponse,
    ReviewRequest,
)
from .service import AntiCheatFlagService

router = APIRouter(prefix="/anti-cheat", tags=["Anti-Cheat"])

#: Review surface: admin + director ONLY.
require_reviewer = require_role("admin", "director")


def get_flag_service(db: AsyncSession = Depends(get_db)) -> AntiCheatFlagService:
    return AntiCheatFlagService(db)


def _to_response(flag) -> AntiCheatFlagResponse:
    return AntiCheatFlagResponse(
        id=flag.id,
        match_id=flag.match_id,
        tournament_id=flag.tournament_id,
        check_name=flag.check_name,
        cheat_type=flag.cheat_type,
        player_id=flag.player_id,
        player_name=flag.player_name,
        team_id=flag.team_id,
        team_name=flag.team_name,
        confidence=flag.confidence,
        confidence_score=flag.confidence_score,
        description=flag.description,
        evidence=flag.evidence or {},
        status=flag.status,
        reviewed_by=flag.reviewed_by,
        reviewed_at=flag.reviewed_at_utc,
        detected_at=flag.detected_at_utc,
    )


@router.get("/flags", response_model=List[AntiCheatFlagResponse])
async def list_flags(
    status_filter: Optional[str] = Query(
        default=None, alias="status", description="pending | confirmed | dismissed"
    ),
    match_id: Optional[str] = Query(default=None),
    check_name: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    service: AntiCheatFlagService = Depends(get_flag_service),
    user: TokenPayload = Depends(require_reviewer),
):
    flags = await service.list_flags(
        status=status_filter, match_id=match_id, check_name=check_name, limit=limit
    )
    return [_to_response(f) for f in flags]


@router.get("/checks", response_model=ChecksResponse)
async def list_checks(
    user: TokenPayload = Depends(require_reviewer),
):
    """Which anti-cheat checks are live on vision data, and which are
    disabled (with the reason) because they need telemetry a spectator
    feed does not produce."""
    registry = checks_registry()
    return ChecksResponse(
        live=[CheckInfo(**c) for c in registry if c["status"] == "live"],
        disabled=[CheckInfo(**c) for c in registry if c["status"] == "disabled"],
    )


@router.get("/flags/{flag_id}", response_model=AntiCheatFlagResponse)
async def get_flag(
    flag_id: str,
    service: AntiCheatFlagService = Depends(get_flag_service),
    user: TokenPayload = Depends(require_reviewer),
):
    flag = await service.get_flag(flag_id)
    if flag is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown anti-cheat flag {flag_id!r}.",
        )
    return _to_response(flag)


@router.post("/flags/{flag_id}/review", response_model=AntiCheatFlagResponse)
async def review_flag(
    flag_id: str,
    payload: ReviewRequest,
    service: AntiCheatFlagService = Depends(get_flag_service),
    user: TokenPayload = Depends(require_reviewer),
):
    try:
        flag = await service.review(
            flag_id, action=payload.action, reviewer=user.username
        )
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_409_CONFLICT
            if "already reviewed" in message
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        raise HTTPException(status_code=code, detail=message)
    if flag is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown anti-cheat flag {flag_id!r}.",
        )
    return _to_response(flag)
