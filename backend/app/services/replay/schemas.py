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


class Highlight(BaseModel):
    """One scored highlight moment."""

    moment_id: str
    kind: Literal["multi_kill", "squad_wipe", "clutch"]
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
