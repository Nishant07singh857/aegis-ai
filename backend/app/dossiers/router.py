"""Dossiers API router — mounted at /api/v1 by the coordinator (app/main.py).

Reads (fight log, dossiers, PDF reports) are public, matching the existing
read-only match-state surface (/teams, /matches, ...). The fight ingest
endpoint mutates state and requires operator or admin role.
"""

from __future__ import annotations

import io
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_operator
from app.db.session import get_db

from .pdf import match_report_pdf, team_dossier_pdf
from .schemas import (
    DossierResponse,
    FightIngest,
    FightIngestResponse,
    FightListResponse,
)
from .service import DossierService, FightService, ReportService

router = APIRouter()


def get_fight_service(db: AsyncSession = Depends(get_db)) -> FightService:
    return FightService(db)


def get_dossier_service(db: AsyncSession = Depends(get_db)) -> DossierService:
    return DossierService(db)


def get_report_service(db: AsyncSession = Depends(get_db)) -> ReportService:
    return ReportService(db)


# ---------- public reads ----------


@router.get("/dossiers/fights", response_model=FightListResponse)
async def list_fights(
    team_id: Optional[str] = Query(default=None),
    match_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    service: FightService = Depends(get_fight_service),
):
    """Fight log filtered by team and/or match, enriched with participants,
    outcome and third-party evidence. Newest first."""
    details, total = await service.list_fights(
        team_id=team_id, match_id=match_id, limit=limit, offset=offset
    )
    return {
        "data": details,
        "meta": {
            "total": total,
            "returned": len(details),
            "team_id": team_id,
            "match_id": match_id,
        },
    }


@router.get("/dossiers/dossier", response_model=DossierResponse)
async def get_dossier(
    team_id: str = Query(...),
    refresh: bool = Query(default=False, description="Force recompute, bypassing the 1h cache."),
    service: DossierService = Depends(get_dossier_service),
):
    """Team dossier computed from real fight/kill/rotation data.

    The result is cached in team_intelligence.profile under "dossier_v1" and
    recomputed when stale (>1h), missing, or when refresh=true. Thin data is
    reported honestly — never fabricated.
    """
    try:
        dossier = await service.get_dossier(team_id, force_recompute=refresh)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return {"data": dossier}


@router.get("/dossiers/report/match/{match_id}.pdf")
async def match_report_pdf_download(
    match_id: str,
    service: ReportService = Depends(get_report_service),
):
    """Downloadable PDF match report (summary, key fights, rotations)."""
    try:
        data = await service.get_match_report_data(match_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")
    pdf_bytes = match_report_pdf(data.model_dump())
    filename = f"aegis-match-report-{match_id[:8]}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/dossiers/report/team/{team_id}.pdf")
async def team_dossier_pdf_download(
    team_id: str,
    service: DossierService = Depends(get_dossier_service),
):
    """Downloadable PDF team dossier (playstyle, edges, tendencies, h2h)."""
    try:
        dossier = await service.get_dossier(team_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    pdf_bytes = team_dossier_pdf(dossier.team_name, dossier.model_dump())
    safe_name = "".join(c if c.isalnum() else "-" for c in dossier.team_name)[:32]
    filename = f"aegis-dossier-{safe_name or team_id[:8]}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------- operator+ writes ----------


@router.post(
    "/dossiers/ingest/fight",
    response_model=FightIngestResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_operator)],
)
async def ingest_fight(
    payload: FightIngest,
    service: FightService = Depends(get_fight_service),
):
    """Record a fight event (operator or admin)."""
    try:
        detail = await service.ingest(payload)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return {"data": detail}
