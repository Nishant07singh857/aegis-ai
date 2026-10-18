"""Pydantic schemas for the dossiers API (Phase 11, Worker W2)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Fight log ─────────────────────────────────────────────────────────


class FightPlayerOut(BaseModel):
    player_id: str
    ign: str
    team_id: Optional[str] = None
    team_name: Optional[str] = None


class FightTeamOut(BaseModel):
    team_id: str
    team_name: str
    players: list[FightPlayerOut] = Field(default_factory=list)


class FightWinnerOut(BaseModel):
    team_id: str
    team_name: str


class ThirdPartyOut(BaseModel):
    team_id: str
    team_name: str
    first_action_at_ms: Optional[int] = None
    delay_after_start_ms: Optional[int] = None
    evidence: str = Field(
        description="How the join time was derived, or why it is unknown."
    )


class FightDetailOut(BaseModel):
    id: str
    match_id: str
    map_name: Optional[str] = None
    start_timestamp_ms: int
    end_timestamp_ms: Optional[int] = None
    duration_ms: Optional[int] = None
    location_x: Optional[float] = None
    location_y: Optional[float] = None
    intensity_score: float = 0.0
    teams: list[FightTeamOut] = Field(default_factory=list)
    winner: Optional[FightWinnerOut] = None
    outcome: str = Field(
        description="'<team name> won' | 'disengaged' | 'ongoing' | 'unknown'."
    )
    third_parties: list[ThirdPartyOut] = Field(default_factory=list)
    total_kills: int = 0
    total_knocks: int = 0
    utility_used: dict[str, Any] = Field(default_factory=dict)


class FightListMeta(BaseModel):
    total: int
    returned: int
    team_id: Optional[str] = None
    match_id: Optional[str] = None


class FightListResponse(BaseModel):
    data: list[FightDetailOut]
    meta: FightListMeta


class FightIngest(BaseModel):
    """Payload for recording a fight event (operator+ only)."""

    match_id: str = Field(min_length=1)
    start_timestamp_ms: int = Field(ge=0)
    end_timestamp_ms: Optional[int] = Field(default=None, ge=0)
    team_ids: list[str] = Field(min_length=1)
    player_ids: list[str] = Field(default_factory=list)
    location_x: Optional[float] = None
    location_y: Optional[float] = None
    map_name: Optional[str] = None
    intensity_score: float = Field(default=0.0, ge=0.0)
    winner_team_id: Optional[str] = None
    total_kills: int = Field(default=0, ge=0)
    total_knocks: int = Field(default=0, ge=0)
    utility_used: dict[str, Any] = Field(default_factory=dict)


class FightIngestResponse(BaseModel):
    data: FightDetailOut


# ── Team dossier ──────────────────────────────────────────────────────


class PlaystyleOut(BaseModel):
    label: str = Field(description="aggressive | balanced | passive | insufficient_data")
    evidence: str = Field(description="Human-readable evidence, e.g. '2.8 fights/match, aggression_score 0.72'.")
    fights_per_match: Optional[float] = None
    aggression_score: Optional[float] = None
    matches_sampled: int = 0


class StatEdgeOut(BaseModel):
    metric: str
    label: str = Field(description="Human-readable, e.g. 'fight win rate 62% vs 41% tournament avg'.")
    team_value: Optional[float] = None
    tournament_avg: Optional[float] = None
    delta_pct: Optional[float] = Field(
        default=None,
        description="Relative delta vs average in percent points of the average, None when not computable.",
    )


class TendenciesOut(BaseModel):
    drop_locations: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Top-3 first-rotation (drop proxy) locations per match.",
    )
    drop_location_note: str = ""
    rotation_timing: dict[str, Any] = Field(default_factory=dict)
    centrality: dict[str, Any] = Field(default_factory=dict)


class HeadToHeadOut(BaseModel):
    opponent_team_id: str
    opponent_name: str
    fights: int = 0
    won: int = 0
    lost: int = 0
    disengaged: int = 0
    kills_for: int = 0
    kills_against: int = 0


class DossierOut(BaseModel):
    team_id: str
    team_name: str
    computed_at: str
    data_freshness: str = Field(description="'fresh' | 'recomputed' | 'insufficient_data'.")
    matches_sampled: int = 0
    fights_sampled: int = 0
    playstyle: PlaystyleOut
    strengths: list[StatEdgeOut] = Field(default_factory=list)
    weaknesses: list[StatEdgeOut] = Field(default_factory=list)
    comparison_note: str = ""
    tendencies: TendenciesOut
    head_to_head: list[HeadToHeadOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DossierResponse(BaseModel):
    data: DossierOut


# ── Match report data (assembled by ReportService, fed to pdf.py) ─────


class MatchTeamSummary(BaseModel):
    team_id: str
    team_name: str
    placement: Optional[int] = None
    placement_note: str = ""
    finishes: int = 0
    kills: int = 0


class MatchReportData(BaseModel):
    match_id: str
    tournament_name: str = "Unknown tournament"
    match_number: Optional[int] = None
    map_name: str = "Unknown map"
    played_at: str = ""
    team_summaries: list[MatchTeamSummary] = Field(default_factory=list)
    fights: list[FightDetailOut] = Field(default_factory=list)
    rotation_highlights: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
