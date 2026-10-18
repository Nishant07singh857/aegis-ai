"""Replay API router — mounted at /api/v1 by app/main.py.

Event replay (not video): re-renders buffered broadcast-event segments
and re-emits them as WS ``replay.play`` frames.

Auth model:
  * GET  /replay/marks                 — any authenticated user
  * GET  /replay/clip?from=&to=        — any authenticated user
  * GET  /replay/highlights?limit=     — any authenticated user
  * POST /replay/capture {label?}      — operator+ (bookmark "now")
  * POST /replay/play {mark_id|from,to}— operator+ (WS replay.play fan-out)
  * DELETE /replay/marks/{id}          — operator+

Timestamps on GET /replay/clip are ISO-8601 query params.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import TokenPayload, get_current_user, require_operator
from app.services.replay import replay_service
from app.services.replay.schemas import (
    CaptureRequest,
    Clip,
    Highlight,
    Mark,
    PlayRequest,
)

router = APIRouter(prefix="/replay", tags=["Replay"])


def _parse_iso(value: str, name: str) -> float:
    """Parse an ISO-8601 query param to epoch seconds. 422 on garbage."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Query param '{name}' must be ISO-8601, got {value!r}.",
        )
    return dt.timestamp()


@router.get("/marks", response_model=List[Mark])
async def list_marks(
    _user: TokenPayload = Depends(get_current_user),
) -> List[Mark]:
    return replay_service.list_marks()


@router.post("/capture", response_model=Mark, status_code=status.HTTP_201_CREATED)
async def capture_mark(
    body: CaptureRequest,
    _user: TokenPayload = Depends(require_operator),
) -> Mark:
    return replay_service.capture_mark(label=body.label)


@router.delete("/marks/{mark_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mark(
    mark_id: str,
    _user: TokenPayload = Depends(require_operator),
) -> None:
    if not replay_service.delete_mark(mark_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No replay mark with id {mark_id!r}.",
        )


@router.get("/clip", response_model=Clip)
async def get_clip(
    from_: str = Query(..., alias="from", description="ISO-8601 window start"),
    to: str = Query(..., description="ISO-8601 window end"),
    _user: TokenPayload = Depends(get_current_user),
) -> Clip:
    from_ts = _parse_iso(from_, "from")
    to_ts = _parse_iso(to, "to")
    try:
        return replay_service.get_clip(from_ts, to_ts)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        )


@router.post("/play", response_model=Clip)
async def play_clip(
    body: PlayRequest,
    _user: TokenPayload = Depends(require_operator),
) -> Clip:
    """Re-emit a clip's events as WS replay.play (event replay, not video)."""
    if body.mark_id:
        try:
            return await replay_service.play_mark(body.mark_id)
        except KeyError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No replay mark with id {body.mark_id!r}.",
            )
    if body.from_ts is not None and body.to_ts is not None:
        from_ts, to_ts = body.from_ts.timestamp(), body.to_ts.timestamp()
        if from_ts > to_ts:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="from_ts must be <= to_ts.",
            )
        return await replay_service.play_clip(from_ts, to_ts)
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="Provide either mark_id or both from_ts and to_ts.",
    )


@router.get("/highlights", response_model=List[Highlight])
async def get_highlights(
    limit: int = Query(10, ge=1, le=50),
    _user: TokenPayload = Depends(get_current_user),
) -> List[Highlight]:
    """Top-N scored highlight moments from the current replay window.

    Honestly empty when the simulator/feed has produced no qualifying
    kill events — moments are never synthesised.
    """
    return replay_service.get_highlights(limit=limit)
