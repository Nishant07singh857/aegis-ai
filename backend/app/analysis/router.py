"""Analysis API router — mounted at /api/v1 by app/main.py.

Paths below carry no /api/v1 prefix. Auth model:

  * GET  /analysis/rotations  — any authenticated user (viewer included)
  * GET  /analysis/drops      — any authenticated user (viewer included)
  * GET  /analysis/zones      — any authenticated user (viewer included)
  * POST /analysis/ingest/rotation — operator or admin
  * POST /analysis/ingest/drop     — operator or admin
  * POST /analysis/ingest/zone     — operator or admin

Every response is an ``AnalysisEnvelope``: when no position data exists
the data payload is honestly empty and ``meta.coverage`` says so — this
API never invents rotations, drops or zone phases.
"""

from __future__ import annotations

from typing import Iterable, Optional

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TokenPayload, get_current_user, require_role
from app.db.session import get_db

from .schemas import (
    AnalysisEnvelope,
    AnalysisMeta,
    DropIngestRequest,
    DropsPayload,
    RotationIngestRequest,
    ZoneIngestRequest,
    ZonesPayload,
)
from .service import DropService, RotationService, ZoneService

router = APIRouter(prefix="/analysis", tags=["Analysis"])

require_operator_or_admin = require_role("admin", "operator")


def _envelope_meta(
    sources: Iterable[str], empty_coverage: str, filled_coverage: str
) -> AnalysisMeta:
    src = {s for s in sources if s and s != "unknown"}
    if not src:
        return AnalysisMeta(source="none", coverage=empty_coverage)
    source = next(iter(src)) if len(src) == 1 else "mixed"
    return AnalysisMeta(source=source, coverage=filled_coverage)


# ── rotations ─────────────────────────────────────────────────────────


@router.get("/rotations", response_model=AnalysisEnvelope)
async def get_rotations(
    team_id: Optional[str] = None,
    match_id: Optional[str] = None,
    _user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List rotation events, optionally filtered by team and/or match."""
    service = RotationService(db)
    events = await service.list_rotations(team_id=team_id, match_id=match_id)
    meta = _envelope_meta(
        (e.source for e in events),
        empty_coverage=(
            "No rotation data recorded for these filters. Rotations are only "
            "shown after the observer feed ingests them via "
            "POST /api/v1/analysis/ingest/rotation."
        ),
        filled_coverage=f"Ingested data: {len(events)} rotation event(s) recorded.",
    )
    return AnalysisEnvelope(data=[e.model_dump() for e in events], meta=meta)


@router.post("/ingest/rotation", status_code=status.HTTP_201_CREATED,
             response_model=AnalysisEnvelope)
async def ingest_rotation(
    payload: RotationIngestRequest,
    _user: TokenPayload = Depends(require_operator_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """Record a rotation event (operator or admin). Lets the simulator or
    live feed write rotation data."""
    service = RotationService(db)
    event = await service.record_rotation(payload)
    return AnalysisEnvelope(
        data=event.model_dump(),
        meta=AnalysisMeta(
            source=event.source,
            coverage=f"Rotation recorded for team {payload.team_id}.",
        ),
    )


# ── grenade trajectories (Phase 23, W4) ───────────────────────────────


@router.get("/grenade-trajectories", response_model=AnalysisEnvelope)
async def get_grenade_trajectories(
    match_id: Optional[str] = None,
    _user: TokenPayload = Depends(get_current_user),
):
    """ESTIMATED grenade arcs for a match — endpoint-based, never measured.

    Records come from the grenade-trajectory bus consumer
    (app/services/grenade_trajectory), which creates a record ONLY when
    a grenade kill/knock or grenade utility event carried BOTH the
    thrower position and an explosion position in the same declared
    coordinate space. Every record carries ``estimated: true``,
    ``method: "endpoint_arc_estimate"`` and
    ``confidence_basis: "endpoints_only"``; when the explosion endpoint
    is the victim's position, ``explosion.position_basis`` says
    ``victim_position_proxy...``. An empty list is the honest state for
    any match whose feed reported no paired endpoints — the current
    production vision feed never does (utility flashes have no thrower;
    minimap markers are anonymous), so in production this fills only
    when a feed reports named, paired endpoints. See
    docs/GRENADE_TRAJECTORY.md.
    """
    from app.services.grenade_trajectory import (
        get_grenade_trajectory_service,
        trajectory_store,
    )

    records = trajectory_store.list(match_id=match_id)
    service = get_grenade_trajectory_service()
    skipped = (
        service.stats.skipped_missing_endpoint if service is not None else 0
    )
    if records:
        coverage = (
            f"{len(records)} ESTIMATED grenade arc(s) — every one is an "
            "endpoint-based estimate (method endpoint_arc_estimate), not a "
            "tracked projectile path."
        )
        source = "feed"
    else:
        coverage = (
            "No estimated grenade arcs for these filters. An arc is only "
            "produced when a grenade event reports BOTH the thrower "
            "position and an explosion position in the same coordinate "
            f"space; {skipped} grenade observation(s) so far lacked a "
            "paired endpoint and produced no arc."
        )
        source = "none"
    return AnalysisEnvelope(
        data=records,
        meta=AnalysisMeta(source=source, coverage=coverage),
    )


# ── drops ─────────────────────────────────────────────────────────────


@router.get("/drops", response_model=AnalysisEnvelope)
async def get_drops(
    team_id: Optional[str] = None,
    match_id: Optional[str] = None,
    _user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Drop events plus per-team drop stats (survival rate over known
    outcomes only, top-5 favourite locations)."""
    service = DropService(db)
    events = await service.list_drops(team_id=team_id, match_id=match_id)

    if team_id:
        team_ids = [team_id]
    else:
        team_ids = await service.distinct_teams(match_id=match_id)
    stats = [await service.team_drop_stats(t, match_id=match_id) for t in team_ids[:50]]

    meta = _envelope_meta(
        (e.source for e in events),
        empty_coverage=(
            "No drop data recorded for these filters. Drops are only shown "
            "after the observer feed ingests them via "
            "POST /api/v1/analysis/ingest/drop."
        ),
        filled_coverage=(
            f"Ingested data: {len(events)} drop event(s), "
            f"{len(stats)} team stat row(s)."
        ),
    )
    payload = DropsPayload(events=events, team_stats=stats)
    return AnalysisEnvelope(data=payload.model_dump(), meta=meta)


@router.post("/ingest/drop", status_code=status.HTTP_201_CREATED,
             response_model=AnalysisEnvelope)
async def ingest_drop(
    payload: DropIngestRequest,
    _user: TokenPayload = Depends(require_operator_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """Record a drop event (operator or admin)."""
    service = DropService(db)
    event = await service.record_drop(payload)
    return AnalysisEnvelope(
        data=event.model_dump(),
        meta=AnalysisMeta(
            source=event.source,
            coverage=f"Drop recorded for team {payload.team_id}.",
        ),
    )


# ── zones ─────────────────────────────────────────────────────────────


@router.get("/zones", response_model=AnalysisEnvelope)
async def get_zones(
    team_id: Optional[str] = None,
    match_id: Optional[str] = None,
    _user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Zone phases plus per-team edge-vs-centre tendency."""
    service = ZoneService(db)
    phases = await service.list_zone_phases(match_id=match_id)
    tendency = await service.tendency(team_id=team_id, match_id=match_id)

    if not team_id and not match_id:
        coverage_empty_note = (
            "Pass team_id and/or match_id: tendency needs a team and a match. "
            "Zone phases are only shown after the observer feed ingests them via "
            "POST /api/v1/analysis/ingest/zone."
        )
    else:
        coverage_empty_note = (
            "No zone data recorded for these filters. Zone phases are only "
            "shown after the observer feed ingests them via "
            "POST /api/v1/analysis/ingest/zone."
        )
    meta = _envelope_meta(
        (p.source for p in phases),
        empty_coverage=coverage_empty_note,
        filled_coverage=(
            f"Ingested data: {len(phases)} zone phase(s), "
            f"{len(tendency)} tendency row(s)."
        ),
    )
    payload = ZonesPayload(phases=phases, tendency=tendency)
    return AnalysisEnvelope(data=payload.model_dump(), meta=meta)


@router.post("/ingest/zone", status_code=status.HTTP_201_CREATED,
             response_model=AnalysisEnvelope)
async def ingest_zone(
    payload: ZoneIngestRequest,
    _user: TokenPayload = Depends(require_operator_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """Record a zone phase (operator or admin)."""
    service = ZoneService(db)
    phase = await service.record_zone_phase(payload)
    return AnalysisEnvelope(
        data=phase.model_dump(),
        meta=AnalysisMeta(
            source=phase.source,
            coverage=f"Zone phase {payload.phase_number} recorded for match {payload.match_id}.",
        ),
    )
