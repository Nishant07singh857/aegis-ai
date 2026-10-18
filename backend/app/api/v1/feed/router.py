"""Game feed API router — mounted at /api/v1 by app/main.py.

The ingest endpoint for ANY game-data source: it validates the
NORMALIZED event schema (see app/services/feed_adapter/schemas.py) and
pushes events into the real event bus, where the replay buffer,
highlight detector, and kill feed consume them.

Auth model:
  * POST /feed/ingest   — operator+ (mutates broadcast state)
  * GET  /feed/status   — any authenticated user (which adapter is live)

Ingesting an event requires the backend process to have attached a live
event bus (done in app/main.py lifespan); otherwise 503.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import TokenPayload, get_current_user, require_operator
from app.services.feed_adapter import NormalizedEvent, feed_service

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


@router.post("/ingest", response_model=IngestResponse)
async def ingest_events(
    body: IngestRequest,
    _user: TokenPayload = Depends(require_operator),
) -> IngestResponse:
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
