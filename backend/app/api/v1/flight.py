"""Flight-path + match-clock + phase API — mounted at /api/v1 by app/main.py.

Phase 24 (W3). Auth model (matching existing gate patterns):

  * GET  /flight/path            — any authenticated user
  * POST /flight/path            — operator+ (require_operator: the
                                   broadcast-control roles, same gate as
                                   feed ingest / director writes)
  * GET  /flight/predicted-drops — any authenticated user
  * GET  /flight/clock           — any authenticated user (match clock;
                                   elapsed is FEED-elapsed, spectator
                                   delayed — see match_clock docstring)
  * GET  /flight/phase           — any authenticated user

The flight path is director-set only; nothing here detects or invents
one. Predicted drops come from recorded drop_events history only — no
history -> an honest empty list.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TokenPayload, get_current_user, require_operator
from app.db.session import get_db
from app.services.flight_path import flight_path_service
from app.services.match_clock import match_clock
from app.services.phase_tracker import phase_tracker

router = APIRouter(prefix="/flight", tags=["Flight"])


class FlightPathRequest(BaseModel):
    map_id: str = Field(..., min_length=1, description="Map id, e.g. 'erangel'.")
    start_poi: str = Field(..., min_length=1, description="Start POI name (POI db).")
    end_poi: str = Field(..., min_length=1, description="End POI name (POI db).")
    match_id: Optional[str] = None


@router.get("/path")
async def get_flight_path(
    _user: TokenPayload = Depends(get_current_user),
) -> Dict[str, Any]:
    """Current director-set flight path (+ live clock / phase context)."""
    return {
        "flight_path": flight_path_service.snapshot(),
        "clock": match_clock.snapshot(),
        "phase": phase_tracker.snapshot(),
    }


@router.post("/path")
async def set_flight_path(
    body: FlightPathRequest,
    user: TokenPayload = Depends(require_operator),
) -> Dict[str, Any]:
    """Set the flight path (director-set, operator+)."""
    try:
        snapshot = await flight_path_service.set_path(
            map_id=body.map_id,
            start_poi=body.start_poi,
            end_poi=body.end_poi,
            updated_by=user.username,
            match_id=body.match_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return {"flight_path": snapshot}


@router.get("/clock")
async def get_match_clock(
    _user: TokenPayload = Depends(get_current_user),
) -> Dict[str, Any]:
    """Match clock: state + feed-elapsed seconds (null before start)."""
    return match_clock.snapshot()


@router.get("/phase")
async def get_match_phase(
    _user: TokenPayload = Depends(get_current_user),
) -> Dict[str, Any]:
    """Current match phase from the PhaseTracker."""
    return phase_tracker.snapshot()


@router.get("/predicted-drops")
async def get_predicted_drops(
    corridor: float = Query(
        default=0.12, gt=0, le=0.5,
        description="Corridor half-width in normalized map units.",
    ),
    db: AsyncSession = Depends(get_db),
    _user: TokenPayload = Depends(get_current_user),
) -> Dict[str, Any]:
    """Teams whose recorded drop POIs lie on the set flight path.

    Reads recorded ``drop_events`` (the same history the analysis
    dossiers use), scoped to the flight path's map when the recorded
    rows carry a map name. No path / no history -> empty list.
    """
    path = flight_path_service.snapshot()
    if not path.get("is_set"):
        return {"flight_path": path, "predicted_drops": [], "corridor": corridor}
    rows: List[Dict[str, Any]] = []
    try:
        from app.analysis.models import DropEvent
        from app.db.models.team import Team

        stmt = select(DropEvent)
        rows_db = (await db.execute(stmt)).scalars().all()
        team_names: Dict[str, str] = {}
        team_ids = {str(r.team_id) for r in rows_db}
        if team_ids:
            teams = (
                await db.execute(select(Team).where(Team.id.in_(team_ids)))
            ).scalars().all()
            team_names = {str(t.id): t.name for t in teams}
        for r in rows_db:
            # Scope to the path's map when both sides name one.
            if r.map_name and path.get("map_id"):
                if str(r.map_name).strip().lower() != str(path["map_id"]).strip().lower():
                    continue
            rows.append(
                {
                    "team_id": str(r.team_id),
                    "team_name": team_names.get(str(r.team_id)),
                    "named_location": r.named_location,
                    "location_x": r.location_x,
                    "location_y": r.location_y,
                    "contested_by": list(r.contested_by or []),
                }
            )
    except Exception:
        # No DB / no history in this environment: honest empty, never fake.
        rows = []
    predicted = flight_path_service.predicted_drops(rows, corridor_half_width=corridor)
    return {"flight_path": path, "predicted_drops": predicted, "corridor": corridor}
