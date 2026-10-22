# backend/app/services/replay/schemas.py
"""Pydantic schemas for the replay API.

All timestamps crossing the HTTP boundary are ISO-8601 strings
(``datetime`` parsed server-side) so query params stay URL-safe.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class Mark(BaseModel):
    """An operator-saved timestamp bookmark in the replay buffer."""

    id: str
    label: str
    ts: float = Field(description="Epoch seconds of the mark.")
    ts_iso: str = Field(description="ISO-8601 of the mark.")
    event_count: int = Field(description="Events captured in the default clip window at mark time.")


class CaptureRequest(BaseModel):
    label: Optional[str] = Field(default=None, max_length=120)


class ClipRequest(BaseModel):
    from_ts: datetime
    to_ts: datetime


class PlayRequest(BaseModel):
    """Play a clip: either by saved mark id, or by explicit window."""

    mark_id: Optional[str] = None
    from_ts: Optional[datetime] = None
    to_ts: Optional[datetime] = None


class Clip(BaseModel):
    """A time window of replayed broadcast events.

    This is an EVENT replay: the events are re-rendered by the output
    page/PiP from their payloads. There is no video payload here.
    """

    from_ts: float
    to_ts: float
    from_iso: str
    to_iso: str
    event_count: int
    events: List[Dict[str, Any]] = Field(
        description="BufferedEvent payloads: {event_id, ts, type, payload}."
    )
    is_video: bool = Field(
        default=False,
        description="Always False. Honest marker: AEGIS event replay is not video DVR.",
    )


RenderStatus = Literal[
    "not_requested", "pending", "rendered", "no_source", "failed"
]


class Highlight(BaseModel):
    """One scored highlight moment."""

    moment_id: str
    # Phase 22 (W3, M11): final_kill is wired; grenade_multi_kill /
    # sniper_triple / vehicle_kill are plug-ready kinds from
    # highlight_triggers.py (fire only with truthful weapon data).
    kind: Literal[
        "multi_kill", "squad_wipe", "clutch", "final_kill",
        "grenade_multi_kill", "sniper_triple", "vehicle_kill",
    ]
    score: int = Field(ge=0, le=100)
    reason: str
    player: Optional[str] = None
    team: Optional[str] = None
    opponents: List[str] = Field(default_factory=list)
    window_start: float
    window_end: float
    window_start_iso: str
    window_end_iso: str
    event_ids: List[str] = Field(default_factory=list)
    # Phase 21 (T4): video render state for this moment. ``not_requested``
    # until an operator asks for a render; ``no_source`` is the honest
    # state when no recorded match video exists (no clip is fabricated).
    render_status: RenderStatus = "not_requested"
    render_files: Dict[str, str] = Field(
        default_factory=dict,
        description="Rendered file names by kind: landscape | portrait | gif.",
    )
    render_error: Optional[str] = None


class RenderRequest(BaseModel):
    """Ask for a video render of one highlight moment."""

    match_id: Optional[str] = Field(
        default=None,
        description="Match the moment belongs to; scopes source-video lookup.",
    )
    source_path: Optional[str] = Field(
        default=None,
        description="Explicit recorded-video path (operator override).",
    )
    source_started_at: Optional[datetime] = Field(
        default=None,
        description="When the source recording started (maps epoch windows).",
    )


class HighlightRender(BaseModel):
    """Render status + outputs for one highlight moment."""

    moment_id: str
    match_id: Optional[str] = None
    status: RenderStatus
    source_path: Optional[str] = None
    files: Dict[str, str] = Field(default_factory=dict)
    file_urls: Dict[str, str] = Field(
        default_factory=dict,
        description="Download URLs by kind, present only when rendered.",
    )
    error: Optional[str] = None
    window_start: Optional[float] = None
    window_end: Optional[float] = None


class DayReelSegment(BaseModel):
    """One ordered slot in the whole-day reel."""

    rank: int = Field(description="1-based position in the reel.")
    moment_id: str
    match_id: Optional[str] = None
    match_number: Optional[int] = None
    title: str
    kind: str = Field(description="Detector kind: multi_kill | squad_wipe | clutch.")
    score: int = Field(ge=0, le=100)
    duration_ms: int = Field(description="Allocated reel time for this segment.")
    window_start_iso: Optional[str] = None
    window_end_iso: Optional[str] = None
    player: Optional[str] = None
    team: Optional[str] = None


QueueState = Literal[
    "preparing", "queued", "ready", "taken", "expired", "dismissed"
]


class ReplayQueueEntry(BaseModel):
    """One offer/take queue entry (Phase 25, P0-2).

    ``is_video`` is the honesty marker: True ONLY when the renderer
    really cut a clip from an ISO recording for this moment; False
    means the take re-emits buffered events (event replay).
    """

    id: str
    moment_id: Optional[str] = None
    kind: str
    score: int = Field(ge=0, le=100)
    reason: str
    player: Optional[str] = None
    team: Optional[str] = None
    opponents: List[str] = Field(default_factory=list)
    window_start: float
    window_end: float
    window_start_iso: Optional[str] = None
    window_end_iso: Optional[str] = None
    feed_id: Optional[str] = None
    slot_number: Optional[int] = None
    source: str
    state: QueueState
    state_history: List[str] = Field(default_factory=list)
    is_video: bool = False
    render_status: str = "not_requested"
    render_files: Dict[str, str] = Field(default_factory=dict)
    render_error: Optional[str] = None
    recording_path: Optional[str] = None
    slot_label: Optional[str] = Field(
        default=None, description="'A' or 'B' while the entry holds a READY offer slot."
    )
    verified: Optional[bool] = None
    created_at: float
    created_at_iso: Optional[str] = None
    ready_at: Optional[float] = None
    taken_at: Optional[float] = None
    taken_at_iso: Optional[str] = None
    taken_by: Optional[str] = None
    expires_at: float
    status_note: Optional[str] = None


class ReplayQueueStatus(BaseModel):
    """Queue list/status surface: entries + the live take gate."""

    counts: Dict[str, int] = Field(default_factory=dict)
    ready_slots: Dict[str, str] = Field(default_factory=dict)
    can_take_now: Dict[str, Any]
    ttl_seconds: float
    max_entries: int
    min_score: int
    max_ready: int
    subscribed_bus: Optional[str] = None
    entries: List[ReplayQueueEntry] = Field(default_factory=list)


class RecorderFeedStatus(BaseModel):
    feed_id: str
    source_id: Optional[str] = None
    state: Literal["recording", "no_source", "disk_full_paused", "stopped"]
    frames_written: int = 0
    segments: int = 0
    open_segment: Optional[str] = None
    last_frame_at: Optional[float] = None
    last_error: Optional[str] = None


class RecorderStatus(BaseModel):
    """ISO recorder status (Phase 25, P0-2), incl. storage math."""

    running: bool
    recordings_dir: str
    segment_seconds: float
    total_cap_bytes: int
    total_bytes: int
    fps: int
    bitrate: str
    storage_math: Dict[str, float] = Field(default_factory=dict)
    feeds: Dict[str, RecorderFeedStatus] = Field(default_factory=dict)


class DayReel(BaseModel):
    """Whole-day highlight reel: scored moments ordered by excitement,
    duration-capped. Honestly empty when nothing was recorded."""

    day: str = Field(description="ISO date label (UTC) this reel covers.")
    segments: List[DayReelSegment] = Field(default_factory=list)
    total_duration_ms: int = 0
    moment_count: int = Field(description="Moments considered before capping.")
    trimmed_count: int = Field(
        default=0, description="Moments dropped by the duration/segment caps."
    )
    match_ids: List[str] = Field(default_factory=list)
    max_duration_ms: int
    max_segments: int
    is_empty: bool = Field(
        description="True when no highlights were recorded — never synthesised."
    )
