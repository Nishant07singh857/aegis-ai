"""Game feed API router — mounted at /api/v1 by app/main.py.

The ingest endpoint for ANY game-data source: it validates the
NORMALIZED event schema (see app/services/feed_adapter/schemas.py) and
pushes events into the real event bus, where the replay buffer,
highlight detector, and kill feed consume them.

Auth model:
  * POST /feed/ingest   — broadcast-control roles (operator+), plus
    SCOPED observer ingest (Integrity H31b): an observer may ingest
    only events whose killer team is their own assigned team
    (server-side check against the observer's team assignment).
  * GET  /feed/status   — any authenticated user (which adapter is live)

Provenance (Integrity C2): every event accepted here is human console
entry, so the endpoint stamps ``event.source`` SERVER-SIDE from the
caller's role ("spotter" / "manual" / "observer") — the trusted
headshot producers. A client-supplied source is always overwritten:
trust is never taken from the request body.

Ingesting an event requires the backend process to have attached a live
event bus (done in app/main.py lifespan); otherwise 503.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    BROADCAST_CONTROL_ROLES,
    TokenPayload,
    get_current_user,
    principal_role,
    require_operator,
)
from app.db.session import get_db
from app.services.feed_adapter import FeedEventType, NormalizedEvent, feed_service

router = APIRouter(prefix="/feed", tags=["Feed"])


class IngestRequest(BaseModel):
    events: List[NormalizedEvent] = Field(
        ..., min_length=1, max_length=500,
        description="Normalized events to publish to the event bus.",
    )


class IngestResponse(BaseModel):
    ingested: int
    event_ids: List[str]


@router.get("/status")
async def feed_status(
    _user: TokenPayload = Depends(get_current_user),
) -> dict:
    return feed_service.status()


async def _assigned_team_name(db: AsyncSession, team_id: str) -> Optional[str]:
    """Resolve an observer's assigned team id to its name.

    Observer clients identify teams by NAME (the console locks the
    killer side to the team name it loaded), while the assignment is a
    team id — the scope check must accept either form. Returns None
    when the team cannot be resolved (the id form still applies).
    """
    try:
        from sqlalchemy import select

        from app.db.models.team import Team

        result = await db.execute(select(Team.name).where(Team.id == team_id))
        name = result.scalars().first()
        return str(name) if name else None
    except Exception:
        return None


def _observer_scope_violation(
    events: List[NormalizedEvent],
    assigned_team_id: Optional[str],
    assigned_team_name: Optional[str],
) -> Optional[str]:
    """First out-of-scope event description, or None when all pass.

    Route-local scoped check (H31b) — the global role constants in
    api/deps.py are untouched. An observer's events are in scope ONLY
    when the killer side (``event.team``) is their own assigned team,
    by id or by resolved name. Kill/knock events with no killer team
    cannot be verified and are out of scope.
    """
    if not assigned_team_id:
        return "observer account has no team assignment"
    allowed = {str(assigned_team_id)}
    if assigned_team_name:
        allowed.add(str(assigned_team_name))
    for event in events:
        killer_team = str(event.team) if event.team else None
        if event.event_type in (FeedEventType.KILL, FeedEventType.KNOCK):
            if killer_team is None or killer_team not in allowed:
                return (
                    f"{event.event_type.value} event killer team "
                    f"{killer_team!r} is not the observer's assigned team"
                )
        elif killer_team is not None and killer_team not in allowed:
            return (
                f"{event.event_type.value} event team {killer_team!r} "
                "is not the observer's assigned team"
            )
    return None


@router.post("/ingest", response_model=IngestResponse)
async def ingest_events(
    body: IngestRequest,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> IngestResponse:
    role = principal_role(user)
    if role in BROADCAST_CONTROL_ROLES:
        # Human console entry by a broadcast-control principal: the
        # spotter toggle is the trusted headshot producer (C2); other
        # control roles are manual-confirmed entry.
        stamp = "spotter" if role == "spotter" else "manual"
    elif role == "observer":
        # H31b scoped ingest: own-team events only, checked server-side
        # against the observer's assignment (JWT team_id claim, backed
        # by the User.team_id assignment; name resolved from teams).
        team_name = None
        if user.team_id:
            team_name = await _assigned_team_name(db, str(user.team_id))
        violation = _observer_scope_violation(
            body.events, user.team_id, team_name
        )
        if violation is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Observer ingest out of scope: {violation}.",
            )
        stamp = "observer"
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Insufficient role: feed ingest requires a broadcast-"
                "control role, or an observer ingesting for their own "
                "assigned team."
            ),
        )
    # Server-side provenance stamp (C2) — overwrites any client claim.
    for event in body.events:
        event.source = stamp
    adapter = feed_service.active
    if adapter is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No feed adapter is active: the backend process has not "
                "attached the event bus yet (app/main.py lifespan wiring)."
            ),
        )
    event_ids: List[str] = []
    for event in body.events:
        try:
            event_ids.append(await adapter.ingest(event))
        except RuntimeError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(e),
            )
    return IngestResponse(ingested=len(event_ids), event_ids=event_ids)


# ---------------------------------------------------------------------------
# Phase 14: vision-pipeline feed source (operator-gated)
# ---------------------------------------------------------------------------


class VisionStartRequest(BaseModel):
    match_id: Optional[str] = Field(
        default=None,
        description=(
            "Match id attached to every vision event. "
            "Defaults to AEGIS_VISION_MATCH_ID when omitted."
        ),
    )


def _vision_adapter():
    """Return the registered vision adapter or 503 when unavailable."""
    adapter = feed_service.get_adapter("vision")
    if adapter is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Vision feed adapter is not registered on this machine "
                "(vision stack unavailable)."
            ),
        )
    return adapter


@router.get("/vision/status")
async def vision_status(
    _user: TokenPayload = Depends(get_current_user),
) -> dict:
    """Vision pipeline status: capture state, FPS, events emitted, OCR."""
    return _vision_adapter().status()


@router.post("/vision/start")
async def vision_start(
    body: VisionStartRequest,
    _user: TokenPayload = Depends(require_operator),
) -> dict:
    """Start vision capture (operator only).

    Begins the device capture -> OCR/HUD detector loop; detected events
    flow into the event bus as the ``vision`` feed source, which becomes
    the active adapter. Honest when there is no feed: the loop idles and
    emits nothing until frames arrive.
    """
    adapter = _vision_adapter()
    try:
        await adapter.connect(match_id=body.match_id)
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)
        )
    feed_service.set_active("vision")
    return {"started": True, "active_adapter": "vision",
            "status": adapter.status()}


@router.post("/vision/stop")
async def vision_stop(
    _user: TokenPayload = Depends(require_operator),
) -> dict:
    """Stop vision capture (operator only); falls back to the simulator."""
    adapter = _vision_adapter()
    await adapter.disconnect()
    if feed_service.get_adapter("simulator") is not None:
        feed_service.set_active("simulator")
    active = feed_service.active
    return {
        "started": False,
        "active_adapter": active.adapter_id if active is not None else None,
        "status": adapter.status(),
    }
