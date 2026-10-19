"""Tournament config API router — mounted at /api/v1 by app/main.py.

Reads are public; every write endpoint (including ``activate``) requires
admin auth (Phase 1 auth: JWT bearer token or X-API-Key, see
app/api/deps.py). Envelopes match the teams endpoints:
``{"data": [...]}`` / ``{"data": {...}}``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.db.session import get_db
from .schemas import (
    TournamentCreate,
    TournamentDetailResponse,
    TournamentListResponse,
    TournamentPatch,
    TournamentUpdate,
)
from .service import TournamentService

router = APIRouter()


def get_tournament_service(db: AsyncSession = Depends(get_db)) -> TournamentService:
    return TournamentService(db)


# ---------- public reads ----------


@router.get("/tournament", response_model=TournamentListResponse)
async def list_tournaments(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    service: TournamentService = Depends(get_tournament_service),
):
    return {"data": await service.list_tournaments(skip=skip, limit=limit)}


@router.get("/tournament/active", response_model=TournamentDetailResponse)
async def get_active_tournament(
    service: TournamentService = Depends(get_tournament_service),
):
    tournament = await service.get_active_tournament()
    if tournament is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No active tournament"
        )
    return {"data": tournament}


@router.get("/tournament/{tournament_id}", response_model=TournamentDetailResponse)
async def get_tournament(
    tournament_id: str,
    service: TournamentService = Depends(get_tournament_service),
):
    tournament = await service.get_tournament(tournament_id)
    if tournament is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tournament not found"
        )
    return {"data": tournament}


# ---------- admin writes ----------


@router.post(
    "/tournament",
    response_model=TournamentDetailResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_tournament(
    payload: TournamentCreate,
    service: TournamentService = Depends(get_tournament_service),
):
    tournament = await service.create_tournament(payload)
    return {"data": tournament}


@router.put(
    "/tournament/{tournament_id}",
    response_model=TournamentDetailResponse,
    dependencies=[Depends(require_admin)],
)
async def update_tournament(
    tournament_id: str,
    payload: TournamentUpdate,
    service: TournamentService = Depends(get_tournament_service),
):
    tournament = await service.update_tournament(tournament_id, payload)
    if tournament is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tournament not found"
        )
    return {"data": tournament}


@router.patch(
    "/tournament/{tournament_id}",
    response_model=TournamentDetailResponse,
    dependencies=[Depends(require_admin)],
)
async def patch_tournament(
    tournament_id: str,
    payload: TournamentPatch,
    service: TournamentService = Depends(get_tournament_service),
):
    tournament = await service.patch_tournament(tournament_id, payload)
    if tournament is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tournament not found"
        )
    return {"data": tournament}


@router.delete(
    "/tournament/{tournament_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def delete_tournament(
    tournament_id: str,
    service: TournamentService = Depends(get_tournament_service),
):
    deleted = await service.delete_tournament(tournament_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tournament not found"
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/tournament/{tournament_id}/activate",
    response_model=TournamentDetailResponse,
    dependencies=[Depends(require_admin)],
)
async def activate_tournament(
    tournament_id: str,
    service: TournamentService = Depends(get_tournament_service),
):
    """Activate this tournament; every other active tournament is demoted to
    ``inactive`` so exactly one tournament is active at a time."""
    tournament = await service.activate_tournament(tournament_id)
    if tournament is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tournament not found"
        )
    return {"data": tournament}
