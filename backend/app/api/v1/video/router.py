"""Phase 17 (W4) — video feed status API.

Mounted at /api/v1 by app/main.py. Endpoints:

  * GET  /video/status              — any authenticated user
  * GET  /video/thumbnail/{slot}    — any authenticated user (JPEG, 404 = no feed)
  * POST /video/on-air              — operator+ (director picks the on-air slot)
  * POST /video/capture/start       — operator+ (start 16-device capture)
  * POST /video/capture/stop        — operator+ (stop capture)
  * POST /video/discover            — operator+ (re-run ADB discovery now)

W2 coordination: this router is READ-ONLY with respect to the capture
pipeline -- it never touches FrameGrabber sources directly; all writes go
through ``MultiDeviceCaptureManager`` so the slot map stays consistent.

Phase 17 (W2) additions:
  * GET  /video/feed/{slot}            — public MJPEG program feed
    (multipart/x-mixed-replace) for the broadcast output video layer.
    Unauthenticated ON PURPOSE: the /broadcast/output page is captured by
    an unauthenticated headless Chromium (same trust model as /ws/live).
    Real frames come from the manager; ?sim=1 (or AEGIS_VIDEO_SIMULATOR=1)
    streams a watermarked test pattern; slots without a live frame stream
    an honest labelled "NO FEED" slate.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import TokenPayload, get_current_user, require_operator
from app.db.base import get_db_context
from app.services.observer_mapping.service import ObserverMappingService
from app.services.video_feed_service import (
    MJPEG_BOUNDARY,
    mjpeg_chunk,
    render_no_feed_slate,
    render_simulator_frame,
    simulator_enabled,
)
from app.vision.capture.multi_device import MultiDeviceCaptureManager

router = APIRouter(prefix="/video", tags=["Video"])


# ────────────────────────────────────────────────────────────────
# Phase 17 (W4): label provider + manager singleton
# ────────────────────────────────────────────────────────────────

async def _db_label_provider() -> Dict[int, Tuple[Optional[str], Optional[str]]]:
    """Read slot -> (team_id, team_name) from the Phase 12/16 DB mapping.

    Team names are NEVER hardcoded here -- they come from the
    observer_slots table (assigned via PUT /api/v1/observers/{n}/assign).
    Without a DB the manager falls back to "Unassigned" labels; the
    supervisor retries on its next tick.
    """
    async with get_db_context() as db:
        service = ObserverMappingService(db)
        slots = await service.list_slots()
        labels: Dict[int, Tuple[Optional[str], Optional[str]]] = {}
        for slot in slots:
            labels[slot.slot_number] = (
                slot.team_id,
                await service.team_name(slot.team_id),
            )
        return labels


# Phase 17 (W4): single process-wide manager. app/main.py wires
# ConnectionManager.broadcast as a listener in lifespan().
multi_device_manager = MultiDeviceCaptureManager(
    label_provider=_db_label_provider,
)


# ────────────────────────────────────────────────────────────────
# Schemas
# ────────────────────────────────────────────────────────────────

class SlotFeedStatus(BaseModel):
    slot_number: int
    team_id: Optional[str] = None
    team_name: Optional[str] = None
    label: str
    status: str  # connected | disconnected | reconnecting | no_feed
    serial: Optional[str] = None
    model: Optional[str] = None
    connection_type: Optional[str] = None
    on_air: bool = False
    thumbnail_url: Optional[str] = None
    fps_target: Optional[int] = None
    frames_captured: int = 0
    last_frame_age_s: Optional[float] = None
    last_error: Optional[str] = None
    adb_misses: int = 0


class VideoStatusResponse(BaseModel):
    capture_running: bool
    capture_backend: str
    on_air_slot: Optional[int] = None
    generated_at: str
    counts: Dict[str, int]
    slots: List[SlotFeedStatus]


class OnAirRequest(BaseModel):
    slot_number: int = Field(..., ge=1, le=16, description="Observer slot 1..16")


class OnAirResponse(BaseModel):
    slot_number: int
    team_id: Optional[str] = None
    team_name: Optional[str] = None
    label: str
    feed_status: str
    set_by: str
    at: str


class DiscoverResponse(BaseModel):
    devices_seen: int
    slots_mapped: int
    newly_mapped: List[Dict[str, Any]]


# ────────────────────────────────────────────────────────────────
# Endpoints
# ────────────────────────────────────────────────────────────────

@router.get("/status", response_model=VideoStatusResponse)
async def video_status(
    _user: TokenPayload = Depends(get_current_user),
):
    """Per-slot feed status for the director wall (16 thumbnails).

    Any authenticated user. ``thumbnail_url`` is present only for slots
    with a live feed; missing URLs mean honest "no feed", never a
    placeholder image.
    """
    return await multi_device_manager.get_status()


@router.get("/thumbnail/{slot_number}")
async def video_thumbnail(
    slot_number: int,
    _user: TokenPayload = Depends(get_current_user),
):
    """Latest frame of a slot as JPEG, downscaled for the director wall.

    404 when the slot has no live frame -- the director UI must render an
    honest "NO FEED" tile, not a fake image.
    """
    if not 1 <= slot_number <= 16:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="slot_number must be 1..16",
        )
    jpeg = await multi_device_manager.thumbnail_jpeg(slot_number)
    if jpeg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no live frame for slot {slot_number}",
        )
    return StreamingResponse(
        iter([jpeg]),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/on-air", response_model=OnAirResponse)
async def set_on_air(
    payload: OnAirRequest,
    user: TokenPayload = Depends(require_operator),
):
    """Director picks which observer slot is on air.

    Operator+ only. Broadcasts ``video.on_air`` over /ws/live (see the
    contract at the bottom of multi_device.py) so W2's video layer can
    switch the program feed. A slot may be selected even while its feed is
    down -- the response reports the live ``feed_status`` so the UI warns.
    """
    try:
        result = await multi_device_manager.set_on_air(
            payload.slot_number, set_by=user.username
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    return result


@router.post("/capture/start", response_model=VideoStatusResponse)
async def start_capture(
    _user: TokenPayload = Depends(require_operator),
):
    """Start 16-device capture (ADB discovery + supervisor). Operator+.

    NOT started automatically at boot -- an operator starts it on
    tournament day after the phones are connected (see
    docs/OBSERVER_SETUP.md).
    """
    return await multi_device_manager.start()


@router.post("/capture/stop")
async def stop_capture(
    _user: TokenPayload = Depends(require_operator),
):
    """Stop capture and the supervisor. Operator+."""
    await multi_device_manager.stop()
    return {"stopped": True}


@router.post("/discover", response_model=DiscoverResponse)
async def discover_devices(
    _user: TokenPayload = Depends(require_operator),
):
    """Re-run ADB discovery and slot mapping immediately. Operator+.

    The supervisor already does this every tick; this endpoint is for the
    tournament-day checklist ("plug the last phone in, hit rediscover").
    """
    return await multi_device_manager.discover_and_map()


# ────────────────────────────────────────────────────────────────
# Phase 17 (W2): MJPEG program feed for the broadcast video layer
# ────────────────────────────────────────────────────────────────

def _no_feed_reason(slot_info: Dict[str, Any]) -> str:
    """Human-readable honest reason for a slot without a live frame."""
    status_value = slot_info.get("status", "no_feed")
    last_error = slot_info.get("last_error")
    if last_error:
        return str(last_error)[:120]
    reasons = {
        "disconnected": "device not seen by ADB — reconnect the observer phone",
        "reconnecting": "capture retrying — device may be waking up",
        "no_feed": "capture not started — operator: POST /api/v1/video/capture/start",
    }
    return reasons.get(status_value, "no live frame from device")


async def _mjpeg_program_stream(
    slot_number: int,
    sim: bool,
    fps: float,
    quality: int,
) -> Any:
    """Async generator of MJPEG multipart chunks for one slot."""
    if sim:
        while True:
            yield mjpeg_chunk(render_simulator_frame(slot_number, quality))
            await asyncio.sleep(1.0 / fps)
        return  # pragma: no cover - infinite stream

    if not multi_device_manager.running:
        reason = "capture not started — operator: POST /api/v1/video/capture/start"
        while True:
            yield mjpeg_chunk(render_no_feed_slate(slot_number, reason, quality))
            await asyncio.sleep(1.0 / fps)
        return  # pragma: no cover - infinite stream

    while True:
        try:
            jpeg = await multi_device_manager.program_jpeg(
                slot_number, quality=quality
            )
        except ValueError:
            jpeg = None  # unknown slot handled below via slot_status
        if jpeg is None:
            try:
                info = await multi_device_manager.slot_status(slot_number)
            except ValueError:
                info = {"status": "no_feed"}
            reason = _no_feed_reason(info)
            yield mjpeg_chunk(render_no_feed_slate(slot_number, reason, quality))
        else:
            yield mjpeg_chunk(jpeg)
        await asyncio.sleep(1.0 / fps)


@router.get("/feed/{slot_number}")
async def video_feed(
    slot_number: int,
    sim: bool = Query(
        default=False,
        description="Simulator mode: stream a watermarked test pattern "
        "instead of the real feed (also forced by AEGIS_VIDEO_SIMULATOR=1).",
    ),
    fps: float = Query(
        default=5.0, ge=1.0, le=10.0,
        description="Stream frame rate (1-10).",
    ),
    quality: int = Query(
        default=80, ge=30, le=95,
        description="JPEG quality (30-95).",
    ),
):
    """MJPEG program feed for one observer slot (Phase 17 W2).

    ``multipart/x-mixed-replace`` — the broadcast output video layer
    displays it with a plain ``<img>`` tag; no WebRTC needed.

    UNAUTHENTICATED on purpose: the /broadcast/output page is captured by
    an unauthenticated headless Chromium (same trust model as /ws/live).
    This is broadcast program content, not device internals.

    Modes:
      * REAL (default): frames from the W4 capture manager (ADB observer
        phones). A slot with no live frame streams an honest labelled
        "NO FEED" slate — never stale or fake footage.
      * SIMULATOR (?sim=1 or AEGIS_VIDEO_SIMULATOR=1): a generated test
        pattern with a big "SIMULATED FEED" watermark.
    """
    if not 1 <= slot_number <= multi_device_manager.max_slots:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"slot_number must be 1..{multi_device_manager.max_slots}",
        )
    use_sim = sim or simulator_enabled()
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    return StreamingResponse(
        _mjpeg_program_stream(slot_number, use_sim, fps, quality),
        media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}",
        headers={
            "Cache-Control": "no-store",
            "X-Aegis-Feed-Slot": str(slot_number),
            "X-Aegis-Feed-Mode": "simulator" if use_sim else "real",
            "X-Aegis-Feed-Started": started_at,
        },
    )
