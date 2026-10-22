"""Replay API router — mounted at /api/v1 by app/main.py.

Event replay (not video): re-renders buffered broadcast-event segments
and re-emits them as WS ``replay.play`` frames.

Auth model:
  * GET  /replay/marks                 — any authenticated user
  * GET  /replay/clip?from=&to=        — any authenticated user
  * GET  /replay/highlights?limit=     — any authenticated user
  * GET  /replay/day-reel?limit=       — any authenticated user
  * POST /replay/capture {label?}      — operator+ (bookmark "now")
  * POST /replay/play {mark_id|from,to}— operator+ (WS replay.play fan-out)
  * DELETE /replay/marks/{id}          — operator+
  * POST /replay/highlights/{id}/render — operator+ (Phase 21/T4: cut real
    video from a recorded match video with FFmpeg; "no_source" when no
    recording exists — never fabricated)
  * GET  /replay/highlights/{id}/render — any authenticated user (status)
  * GET  /replay/renders               — any authenticated user (all statuses)
  * GET  /replay/highlights/{id}/files/{kind} — any authenticated user
    (download a rendered clip: landscape | portrait | gif)

Timestamps on GET /replay/clip are ISO-8601 query params.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse

from app.api.deps import TokenPayload, get_current_user, require_operator, require_role
from app.services.replay import replay_service
from app.services.replay.queue import ReplayEntryNotFound, ReplayTakeRefused
from app.services.replay.schemas import (
    CaptureRequest,
    Clip,
    DayReel,
    Highlight,
    HighlightRender,
    Mark,
    PlayRequest,
    RecorderStatus,
    RenderRequest,
    ReplayQueueEntry,
    ReplayQueueStatus,
)

#: Take/dismiss are director decisions (blueprint C7): director and
#: operator (and admin above them) only — spotters/observers may not
#: take a replay to air.
require_director_or_operator = require_role("admin", "operator", "director")

router = APIRouter(prefix="/replay", tags=["Replay"])

_RENDER_KINDS = ("landscape", "portrait", "gif")


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


@router.get("/day-reel", response_model=DayReel)
async def get_day_reel(
    limit: int = Query(50, ge=1, le=200),
    _user: TokenPayload = Depends(get_current_user),
) -> DayReel:
    """Whole-day highlight reel: the day's recorded moments ordered by
    excitement score, duration-capped (default 5 min / 24 segments).

    Honest scope: moments come from this process's replay buffer, i.e.
    matches recorded while the server has been running today. When
    nothing was recorded the reel is empty (``is_empty``) — moments are
    never synthesised.
    """
    return replay_service.get_day_reel(limit=limit)


# ── Phase 21 (T4): highlight video rendering ─────────────────────


def _render_to_schema(record, files: bool = True) -> HighlightRender:
    moment_id = record.moment_id
    file_urls = (
        {
            kind: f"/api/v1/replay/highlights/{moment_id}/files/{kind}"
            for kind in _RENDER_KINDS
            if kind in record.files
        }
        if files
        else {}
    )
    return HighlightRender(
        moment_id=moment_id,
        match_id=record.match_id,
        status=record.status,  # type: ignore[arg-type]
        source_path=record.source_path,
        files=dict(record.files),
        file_urls=file_urls,
        error=record.error,
        window_start=record.window_start,
        window_end=record.window_end,
    )


@router.post(
    "/highlights/{moment_id}/render",
    response_model=HighlightRender,
    status_code=status.HTTP_201_CREATED,
)
async def render_highlight(
    moment_id: str,
    body: RenderRequest,
    _user: TokenPayload = Depends(require_operator),
) -> HighlightRender:
    """Cut a highlight moment into real video files (operator+).

    Cuts the window out of a recorded match video with FFmpeg
    (16:9 MP4 + 9:16 MP4 + 3s GIF preview). ``status=no_source`` when no
    recording exists for the match — no clip is ever fabricated. Unknown
    moment ids are a 404: only real scored moments can be rendered.
    """
    try:
        record = await asyncio.to_thread(
            replay_service.request_render,
            moment_id,
            match_id=body.match_id,
            source_path=body.source_path,
            source_started_at=(
                body.source_started_at.timestamp()
                if body.source_started_at is not None
                else None
            ),
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No highlight moment with id {moment_id!r}.",
        )
    return _render_to_schema(record)


@router.get("/highlights/{moment_id}/render", response_model=HighlightRender)
async def get_highlight_render(
    moment_id: str,
    _user: TokenPayload = Depends(get_current_user),
) -> HighlightRender:
    """Render status for one highlight moment."""
    return _render_to_schema(replay_service.render_status(moment_id))


@router.get("/renders", response_model=List[HighlightRender])
async def list_renders(
    _user: TokenPayload = Depends(get_current_user),
) -> List[HighlightRender]:
    """All highlight render records, newest first."""
    return [_render_to_schema(r) for r in replay_service.list_renders()]


# ── Phase 25 (P0-2): replay offer/take queue + ISO recorder ─────


def _queue():
    """The offer/take queue bound to the replay service singleton."""
    return replay_service.get_queue()


def _recorder():
    """The ISO recorder attached to the replay service, built lazily
    (no frame provider -> feeds honestly report no_source until the
    coordinator wires the capture-manager-backed recorder at startup).
    """
    if replay_service.recorder is None:
        from app.services.replay.recorder import ISORecorder

        replay_service.attach_recorder(ISORecorder())
    return replay_service.recorder


@router.get("/queue", response_model=ReplayQueueStatus)
async def get_replay_queue(
    _user: TokenPayload = Depends(get_current_user),
) -> ReplayQueueStatus:
    """Queue entries (newest first) + ready A/B slots + the live
    ``can_take_now`` lull gate. Any authenticated user."""
    return ReplayQueueStatus(**_queue().status())


@router.post("/queue/scan", response_model=ReplayQueueStatus)
async def scan_replay_queue(
    _user: TokenPayload = Depends(require_operator),
) -> ReplayQueueStatus:
    """Scan the highlight reel now and enqueue qualifying off-PGM
    moments (operator+). The same poll runs from the director ticker
    when wired; this endpoint makes it explicit and testable."""
    _queue().poll_highlights()
    return ReplayQueueStatus(**_queue().status())


@router.get("/queue/{entry_id}", response_model=ReplayQueueEntry)
async def get_replay_queue_entry(
    entry_id: str,
    _user: TokenPayload = Depends(get_current_user),
) -> ReplayQueueEntry:
    entry = _queue().get(entry_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No replay queue entry with id {entry_id!r}.",
        )
    return ReplayQueueEntry(**entry.to_dict())


@router.post("/queue/{entry_id}/take", response_model=ReplayQueueEntry)
async def take_replay_queue_entry(
    entry_id: str,
    user: TokenPayload = Depends(require_director_or_operator),
) -> ReplayQueueEntry:
    """Take an offered (READY) replay to air — director/operator only.

    Refused (409) when the lull gate is closed (fight on PGM or the
    late-phase suppression), when the entry is not READY, when it was
    already taken, or for operator takes in full_auto mode (the system
    auto-takes there). A successful take airs exactly once through the
    existing replay.play path with replay stinger/lower-third packaging.
    """
    try:
        entry = await _queue().take(entry_id, taken_by=user.username, via="operator")
    except ReplayEntryNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No replay queue entry with id {entry_id!r}.",
        )
    except ReplayTakeRefused as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "gate": exc.gate},
        )
    return ReplayQueueEntry(**entry.to_dict())


@router.post("/queue/{entry_id}/dismiss", response_model=ReplayQueueEntry)
async def dismiss_replay_queue_entry(
    entry_id: str,
    user: TokenPayload = Depends(require_director_or_operator),
) -> ReplayQueueEntry:
    """Dismiss a queued/offered replay without airing it (director/
    operator only). Frees its A/B slot for the next queued moment."""
    try:
        entry = _queue().dismiss(entry_id, dismissed_by=user.username)
    except ReplayEntryNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No replay queue entry with id {entry_id!r}.",
        )
    except ReplayTakeRefused as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )
    return ReplayQueueEntry(**entry.to_dict())


@router.get("/recorder/status", response_model=RecorderStatus)
async def get_recorder_status(
    _user: TokenPayload = Depends(get_current_user),
) -> RecorderStatus:
    """Per-feed ISO recording state (recording / no_source /
    disk_full_paused / stopped) + storage usage and cap."""
    return RecorderStatus(**_recorder().status())


@router.post("/recorder/start", response_model=RecorderStatus)
async def start_recorder(
    _user: TokenPayload = Depends(require_operator),
) -> RecorderStatus:
    """Start ISO recording (operator+). Feeds with no live frame
    honestly report no_source and write nothing."""
    recorder = _recorder()
    if recorder.frame_provider is not None:
        result = await recorder.start_async()
    else:
        result = recorder.start()
    return RecorderStatus(**result)


@router.post("/recorder/stop", response_model=RecorderStatus)
async def stop_recorder(
    _user: TokenPayload = Depends(require_operator),
) -> RecorderStatus:
    """Stop ISO recording (operator+): open segments are finalised
    with their sidecars, so everything recorded stays cuttable."""
    recorder = _recorder()
    if recorder.frame_provider is not None:
        result = await recorder.stop_async()
    else:
        result = recorder.stop()
    return RecorderStatus(**result)


@router.get("/highlights/{moment_id}/files/{kind}")
async def download_render(
    moment_id: str,
    kind: str,
    _user: TokenPayload = Depends(get_current_user),
):
    """Download one rendered file for a highlight moment."""
    if kind not in _RENDER_KINDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"kind must be one of {', '.join(_RENDER_KINDS)}.",
        )
    path = replay_service.renderer.file_path(moment_id, kind)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No rendered {kind} file for moment {moment_id!r}. "
                "Request a render first (POST …/render)."
            ),
        )
    media = "image/gif" if kind == "gif" else "video/mp4"
    return FileResponse(path, media_type=media, filename=path.name)
