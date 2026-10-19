"""Players API router — mounted at /api/v1 by app/main.py.

Reads are public; every write endpoint requires admin auth (Phase 1 auth:
JWT bearer token or X-API-Key, see app/api/deps.py). Envelopes match the
teams endpoints: ``{"data": [...]}`` / ``{"data": {...}}``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import enforce_team_scope, require_admin, require_team_scope
from app.db.session import get_db
from .schemas import (
    PlayerCreate,
    PlayerDetailResponse,
    PlayerListResponse,
    PlayerPatch,
    PlayerUpdate,
)
from .service import PlayerService

router = APIRouter()


def get_player_service(db: AsyncSession = Depends(get_db)) -> PlayerService:
    return PlayerService(db)


# ---------- public reads ----------


@router.get("/players", response_model=PlayerListResponse)
async def list_players(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    team_id: Optional[str] = Query(None, description="Filter to one team's roster"),
    service: PlayerService = Depends(get_player_service),
    scope: Optional[str] = Depends(require_team_scope),
):
    # Phase 16: an observer asking for another team's roster gets a 403;
    # with no team_id given, observers are silently filtered to their team.
    enforce_team_scope(team_id, scope)
    if scope is not None:
        team_id = scope
    return {"data": await service.list_players(skip=skip, limit=limit, team_id=team_id)}


@router.get("/teams/{team_id}/players", response_model=PlayerListResponse)
async def list_team_players(
    team_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    service: PlayerService = Depends(get_player_service),
    scope: Optional[str] = Depends(require_team_scope),
):
    enforce_team_scope(team_id, scope)
    return {"data": await service.list_players(skip=skip, limit=limit, team_id=team_id)}


@router.get("/players/{player_id}", response_model=PlayerDetailResponse)
async def get_player(
    player_id: str,
    service: PlayerService = Depends(get_player_service),
    scope: Optional[str] = Depends(require_team_scope),
):
    player = await service.get_player(player_id)
    if player is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")
    # Phase 16: observers may only see players of their assigned team —
    # strictly: a player with no team is not "their team" either.
    if scope is not None and (player.team_id is None or str(player.team_id) != str(scope)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Observers may only access their assigned team's data.",
        )
    return {"data": player}


# ---------- admin writes ----------


@router.post(
    "/players",
    response_model=PlayerDetailResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_player(
    payload: PlayerCreate,
    service: PlayerService = Depends(get_player_service),
):
    player = await service.create_player(payload)
    return {"data": player}


@router.put(
    "/players/{player_id}",
    response_model=PlayerDetailResponse,
    dependencies=[Depends(require_admin)],
)
async def update_player(
    player_id: str,
    payload: PlayerUpdate,
    service: PlayerService = Depends(get_player_service),
):
    player = await service.update_player(player_id, payload)
    if player is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")
    return {"data": player}


@router.patch(
    "/players/{player_id}",
    response_model=PlayerDetailResponse,
    dependencies=[Depends(require_admin)],
)
async def patch_player(
    player_id: str,
    payload: PlayerPatch,
    service: PlayerService = Depends(get_player_service),
):
    player = await service.patch_player(player_id, payload)
    if player is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")
    return {"data": player}


@router.delete(
    "/players/{player_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def delete_player(
    player_id: str,
    service: PlayerService = Depends(get_player_service),
):
    deleted = await service.delete_player(player_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
