"""AI storyteller API router — mounted at /api/v1 by app/main.py.

Auth model:

  * GET  /stories/available      — director, admin, operator (list stories)
  * POST /stories/generate       — director, admin (trigger generation)
  * GET  /stories/settings       — director, admin
  * PUT  /stories/settings       — director, admin
  * GET  /stories/{id}           — director, admin, operator
  * POST /stories/{id}/trigger   — director, admin ONLY (broadcast output)
  * POST /stories/{id}/dismiss   — director, admin ONLY

Status codes: 401 unauthenticated, 403 insufficient role,
404 unknown story, 422 invalid story type requested.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TokenPayload, require_role
from app.db.session import get_db

from .schemas import (
    GenerateRequest,
    StoryResponse,
    StorySettingsResponse,
    StorySettingsUpdate,
    TriggerRequest,
)
from .service import StoryService

router = APIRouter(prefix="/stories", tags=["Stories"])

#: Read access: director + admin + operator.
require_story_reader = require_role("director", "admin", "operator")
#: Write / broadcast-control access: director + admin ONLY.
require_story_control = require_role("director", "admin")


def get_story_service(db: AsyncSession = Depends(get_db)) -> StoryService:
    return StoryService(db)


def _to_response(story) -> StoryResponse:
    return StoryResponse(
        id=story.id,
        match_id=story.match_id,
        tournament_id=story.tournament_id,
        story_type=story.story_type,
        title=story.title,
        body=story.body,
        data=story.data or {},
        confidence=story.confidence,
        status=story.status,
        generated_at=story.generated_at_utc,
        triggered_by=story.triggered_by,
    )


@router.get("/available", response_model=List[StoryResponse])
async def list_available(
    match_id: Optional[str] = Query(default=None),
    type: Optional[str] = Query(default=None, description="story type filter"),
    status_filter: Optional[str] = Query(
        default=None, alias="status", description="generated | triggered | dismissed"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    service: StoryService = Depends(get_story_service),
    user: TokenPayload = Depends(require_story_reader),
):
    stories = await service.list_stories(
        match_id=match_id, story_type=type, status=status_filter, limit=limit
    )
    return [_to_response(s) for s in stories]


@router.post("/generate", response_model=List[StoryResponse], status_code=201)
async def generate_stories(
    payload: GenerateRequest,
    service: StoryService = Depends(get_story_service),
    user: TokenPayload = Depends(require_story_control),
):
    stories = await service.generate(
        match_id=payload.match_id,
        types=payload.types,
        context=payload.context,
        tournament_id=payload.context.get("tournament_id"),
    )
    return [_to_response(s) for s in stories]


# NOTE: /settings is registered BEFORE /{story_id} so "settings" is never
# captured as a story id.
@router.get("/settings", response_model=StorySettingsResponse)
async def get_settings(
    tournament_id: Optional[str] = Query(
        default=None, description="null = global defaults"
    ),
    service: StoryService = Depends(get_story_service),
    user: TokenPayload = Depends(require_story_control),
):
    return StorySettingsResponse(
        tournament_id=tournament_id,
        settings=await service.get_settings(tournament_id),
    )


@router.put("/settings", response_model=StorySettingsResponse)
async def put_settings(
    payload: StorySettingsUpdate,
    service: StoryService = Depends(get_story_service),
    user: TokenPayload = Depends(require_story_control),
):
    settings = await service.upsert_settings(
        payload.tournament_id, [s.model_dump() for s in payload.settings]
    )
    return StorySettingsResponse(
        tournament_id=payload.tournament_id, settings=settings
    )


@router.get("/{story_id}", response_model=StoryResponse)
async def get_story(
    story_id: str,
    service: StoryService = Depends(get_story_service),
    user: TokenPayload = Depends(require_story_reader),
):
    story = await service.get_story(story_id)
    if story is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown story {story_id!r}.",
        )
    return _to_response(story)


@router.post("/{story_id}/trigger", response_model=StoryResponse)
async def trigger_story(
    story_id: str,
    payload: Optional[TriggerRequest] = Body(default=None),
    service: StoryService = Depends(get_story_service),
    user: TokenPayload = Depends(require_story_control),
):
    story = await service.trigger(
        story_id,
        username=user.username,
        template_id=payload.template_id if payload else None,
        animation_style=payload.animation_style if payload else None,
    )
    if story is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown story {story_id!r}.",
        )
    return _to_response(story)


@router.post("/{story_id}/dismiss", response_model=StoryResponse)
async def dismiss_story(
    story_id: str,
    service: StoryService = Depends(get_story_service),
    user: TokenPayload = Depends(require_story_control),
):
    story = await service.dismiss(story_id, username=user.username)
    if story is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown story {story_id!r}.",
        )
    return _to_response(story)
