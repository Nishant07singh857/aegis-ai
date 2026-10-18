"""AEGIS AI — analysis API schemas (Phase 11).

Request/response models for rotation, drop and zone tracking. Every read
response is wrapped in ``AnalysisEnvelope`` so clients can distinguish
real recorded data from an honest empty state.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

IngestSource = Literal["simulator", "feed"]


# ── envelope ──────────────────────────────────────────────────────────


class AnalysisMeta(BaseModel):
    source: str = Field(
        ...,
        description=(
            "'simulator' | 'feed' | 'mixed' when rows came from different "
            "producers, 'none' when no rows exist."
        ),
    )
    coverage: str = Field(
        ...,
        description="Human-readable, honest statement of what data exists.",
    )


class AnalysisEnvelope(BaseModel):
    data: Any
    meta: AnalysisMeta


# ── rotations ─────────────────────────────────────────────────────────


class RotationIngestRequest(BaseModel):
    match_id: str
    team_id: str
    timestamp_ms: int = Field(..., ge=0)
    from_zone: Optional[str] = None
    to_zone: Optional[str] = None
    game_time_ms: Optional[int] = Field(default=None, ge=0)
    waypoints: List[List[float]] = Field(
        default_factory=list,
        description="Polyline [[x, y], ...] in map metres.",
    )
    vehicle_used: bool = False
    fights_encountered: List[str] = Field(
        default_factory=list,
        description="fight_events ids the team crossed during the rotation.",
    )
    rotation_order: Optional[int] = Field(default=None, ge=1)
    distance_m: Optional[float] = Field(
        default=None, ge=0.0,
        description="Feed-measured distance; computed from waypoints when omitted.",
    )
    source: IngestSource = "simulator"


class RotationResponse(BaseModel):
    id: str
    match_id: str
    team_id: str
    timestamp_ms: int
    from_zone: Optional[str] = None
    to_zone: Optional[str] = None
    game_time_ms: Optional[int] = None
    waypoints: List[List[float]] = Field(default_factory=list)
    vehicle_used: bool = False
    fights_encountered: List[str] = Field(default_factory=list)
    rotation_order: Optional[int] = None
    distance_m: Optional[float] = None
    waypoint_count: int = 0
    computed_distance_m: float = 0.0
    source: str = "unknown"


# ── drops ─────────────────────────────────────────────────────────────


class DropIngestRequest(BaseModel):
    match_id: str
    team_id: str
    map_name: Optional[str] = Field(default=None, max_length=32)
    location_x: Optional[float] = None
    location_y: Optional[float] = None
    named_location: Optional[str] = Field(default=None, max_length=128)
    timestamp_ms: int = Field(..., ge=0)
    contested_by: List[str] = Field(
        default_factory=list,
        description="team_ids that landed at the same spot.",
    )
    survived_drop: Optional[bool] = Field(
        default=None,
        description="True/False when known; omit when the feed never reported it.",
    )
    source: IngestSource = "simulator"


class DropResponse(BaseModel):
    id: str
    match_id: str
    team_id: str
    map_name: Optional[str] = None
    location_x: Optional[float] = None
    location_y: Optional[float] = None
    named_location: Optional[str] = None
    timestamp_ms: int
    contested: bool = False
    contested_by: List[str] = Field(default_factory=list)
    survived_drop: Optional[bool] = None
    source: str = "unknown"


class DropLocationStat(BaseModel):
    named_location: str
    map_name: Optional[str] = None
    drops: int
    contested: int


class DropTeamStats(BaseModel):
    team_id: str
    total_drops: int
    contested_drops: int
    known_outcomes: int
    survived: int
    survival_rate: Optional[float] = Field(
        default=None,
        description="survived / known outcomes; None when no outcome is known.",
    )
    favorite_locations: List[DropLocationStat] = Field(default_factory=list)


class DropsPayload(BaseModel):
    events: List[DropResponse] = Field(default_factory=list)
    team_stats: List[DropTeamStats] = Field(default_factory=list)


# ── zones ─────────────────────────────────────────────────────────────


class ZoneIngestRequest(BaseModel):
    match_id: str
    phase_number: int = Field(..., ge=1)
    center_x: float
    center_y: float
    radius: float = Field(..., gt=0.0)
    timestamp_ms: int = Field(..., ge=0)
    extra: Dict[str, Any] = Field(
        default_factory=dict,
        description="Extra feed fields merged into the phase meta.",
    )
    source: IngestSource = "simulator"


class ZonePhaseResponse(BaseModel):
    id: str
    match_id: str
    phase_number: int
    center_x: float
    center_y: float
    radius: float
    timestamp_ms: int
    source: str = "unknown"


class ZoneTendency(BaseModel):
    team_id: str
    match_id: str
    phases_recorded: int
    phases_with_position: int
    avg_normalized_distance: Optional[float] = Field(
        default=None,
        description=(
            "Mean distance from zone centre at phase end, divided by zone "
            "radius. <1 inside the circle, ~1 on the edge, >1 outside."
        ),
    )
    tendency: str = Field(
        ...,
        description="center | mid-zone | edge | outside | unknown",
    )
    avg_distance_traveled_per_phase_m: Optional[float] = None
    notes: List[str] = Field(default_factory=list)


class ZonesPayload(BaseModel):
    phases: List[ZonePhaseResponse] = Field(default_factory=list)
    tendency: List[ZoneTendency] = Field(default_factory=list)
