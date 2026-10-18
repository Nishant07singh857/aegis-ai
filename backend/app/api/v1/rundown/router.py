"""Rundown API router — mounted at /api/v1 by app/main.py.

Auth model:

  * GET    /rundown/state                  — any authenticated user
  * POST   /rundown/segments               — operator+ (add)
  * PATCH  /rundown/segments/{id}          — operator+ (edit)
  * DELETE /rundown/segments/{id}          — operator+ (remove)
  * POST   /rundown/segments/{id}/reorder  — operator+ (move)
  * POST   /rundown/advance                — operator+ (start / next)
  * POST   /rundown/reset                  — operator+ (rewind to "not started")

Every change fans out as a ``rundown.state`` frame on /ws/live and is
published on the event bus by the service. Status codes: 401
unauthenticated, 403 insufficient role, 404 unknown segment id, 409 the
operation is valid but the rundown state forbids it (advance with no
segments / already on the final segment).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import TokenPayload, get_current_user, require_operator
from app.services.rundown import RundownError, rundown_service

from .schemas import (
    RundownSegment,
    RundownState,
    SegmentCreate,
    SegmentReorder,
    SegmentUpdate,
)

router = APIRouter(prefix="/rundown", tags=["Rundown"])


@router.get("/state", response_model=RundownState)
async def get_rundown_state(
    _user: TokenPayload = Depends(get_current_user),
):
    """Current run-of-show. Any authenticated user, viewers included."""
    return rundown_service.snapshot()


@router.post(
    "/segments",
    response_model=RundownSegment,
    status_code=status.HTTP_201_CREATED,
)
async def add_segment(
    payload: SegmentCreate,
    user: TokenPayload = Depends(require_operator),
):
    """Add a segment to the rundown. Operator or admin."""
    try:
        return await rundown_service.add_segment(
            title=payload.title,
            planned_duration_s=payload.planned_duration_s,
            notes=payload.notes,
            order=payload.order,
            updated_by=user.username,
        )
    except RundownError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )


@router.patch("/segments/{segment_id}", response_model=RundownSegment)
async def update_segment(
    segment_id: str,
    payload: SegmentUpdate,
    user: TokenPayload = Depends(require_operator),
):
    """Edit a segment's title / duration / notes. Operator or admin."""
    if not payload.has_changes():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No changes provided: set at least one of 'title', "
            "'planned_duration_s' or 'notes'.",
        )
    try:
        return await rundown_service.update_segment(
            segment_id,
            title=payload.title,
            planned_duration_s=payload.planned_duration_s,
            notes=payload.notes,
            updated_by=user.username,
        )
    except RundownError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "No rundown segment" in message
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        raise HTTPException(status_code=code, detail=message)


@router.delete("/segments/{segment_id}", response_model=RundownState)
async def remove_segment(
    segment_id: str,
    user: TokenPayload = Depends(require_operator),
):
    """Remove a segment. Operator or admin. Returns the new rundown state."""
    try:
        return await rundown_service.remove(
            segment_id, updated_by=user.username
        )
    except RundownError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )


@router.post("/segments/{segment_id}/reorder", response_model=RundownState)
async def reorder_segment(
    segment_id: str,
    payload: SegmentReorder,
    user: TokenPayload = Depends(require_operator),
):
    """Move a segment to a new position. Operator or admin. Returns the
    new rundown state."""
    try:
        return await rundown_service.reorder(
            segment_id,
            payload.new_order,
            updated_by=user.username,
        )
    except RundownError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )


@router.post("/advance", response_model=RundownState)
async def advance_rundown(
    user: TokenPayload = Depends(require_operator),
):
    """Start the rundown (first call) or step to the next segment.
    Operator or admin. 409 when the rundown is empty or already on its
    final segment."""
    try:
        return await rundown_service.advance(updated_by=user.username)
    except RundownError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )


@router.post("/reset", response_model=RundownState)
async def reset_rundown(
    user: TokenPayload = Depends(require_operator),
):
    """Rewind the rundown to 'not started', keeping the segments.
    Operator or admin."""
    return await rundown_service.reset(updated_by=user.username)
