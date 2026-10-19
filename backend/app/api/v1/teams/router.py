"""Teams API router — mounted at /api/v1 by app/main.py.

Reads are public; every write endpoint requires admin auth (Phase 1 auth:
JWT bearer token or X-API-Key, see app/api/deps.py).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import enforce_team_scope, require_admin, require_team_scope
from app.db.session import get_db
from .schemas import (
    TeamCreate,
    TeamDetailResponse,
    TeamListResponse,
    TeamPatch,
    TeamUpdate,
)
from .service import TeamService

router = APIRouter()


def get_team_service(db: AsyncSession = Depends(get_db)) -> TeamService:
    return TeamService(db)


# ---------- public reads ----------


@router.get("/teams", response_model=TeamListResponse)
async def list_teams(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    service: TeamService = Depends(get_team_service),
    scope: Optional[str] = Depends(require_team_scope),
):
    teams = await service.list_teams(skip=skip, limit=limit)
    if scope is not None:
        # Phase 16: observers see ONLY their assigned team.
        teams = [t for t in teams if t.id == scope]
    return {"data": teams}


@router.get("/teams/{team_id}", response_model=TeamDetailResponse)
async def get_team(
    team_id: str,
    service: TeamService = Depends(get_team_service),
    scope: Optional[str] = Depends(require_team_scope),
):
    # Phase 16: an observer requesting any other team's id gets a 403.
    enforce_team_scope(team_id, scope)
    team = await service.get_team(team_id)
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return {"data": team}


# ---------- admin writes ----------


@router.post(
    "/teams",
    response_model=TeamDetailResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_team(
    payload: TeamCreate,
    service: TeamService = Depends(get_team_service),
):
    team = await service.create_team(payload)
    return {"data": team}


@router.put(
    "/teams/{team_id}",
    response_model=TeamDetailResponse,
    dependencies=[Depends(require_admin)],
)
async def update_team(
    team_id: str,
    payload: TeamUpdate,
    service: TeamService = Depends(get_team_service),
):
    team = await service.update_team(team_id, payload)
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return {"data": team}


@router.patch(
    "/teams/{team_id}",
    response_model=TeamDetailResponse,
    dependencies=[Depends(require_admin)],
)
async def patch_team(
    team_id: str,
    payload: TeamPatch,
    service: TeamService = Depends(get_team_service),
):
    team = await service.patch_team(team_id, payload)
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return {"data": team}


@router.delete(
    "/teams/{team_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def delete_team(
    team_id: str,
    service: TeamService = Depends(get_team_service),
):
    deleted = await service.delete_team(team_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
