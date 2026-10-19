"""Pydantic schemas for the match-point API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MatchPointConfig(BaseModel):
    enabled: bool
    mode: str
    points_threshold: int
    final_day_number: int
    current_day: int


class MatchPointConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    mode: Optional[str] = Field(
        default=None, description="points_threshold | final_day_chicken"
    )
    points_threshold: Optional[int] = Field(default=None, ge=1)
    final_day_number: Optional[int] = Field(default=None, ge=1)
    current_day: Optional[int] = Field(default=None, ge=1)


class MatchPointEntry(BaseModel):
    team_id: Optional[str] = None
    team_name: Optional[str] = None
    total_points: float
    reason: str


class StandingsRow(BaseModel):
    """One row of the standings being shown on the broadcast.

    No tournament-wide standings source exists in the match service, so the
    broadcast driver (operator UI / analytics pipeline) posts the standings
    it is actually showing. ``total_points`` drives the match-point engine;
    ``team_name`` is display-only.
    """

    team_id: str
    team_name: Optional[str] = None
    total_points: float = Field(default=0.0)


class MatchPointEvaluateRequest(BaseModel):
    standings: List[StandingsRow]
    day: Optional[int] = Field(
        default=None,
        ge=1,
        description="Tournament day this evaluation applies to; defaults to "
        "the configured current_day",
    )


class MatchPointEvaluation(BaseModel):
    evaluated_at: str
    day: int
    on_match_point: List[MatchPointEntry]
    note: str


class MatchPointState(BaseModel):
    config: MatchPointConfig
    last_evaluation: Optional[MatchPointEvaluation] = None
    updated_at: str


# ── Smash Rule championship engine (Phase 20) ──────────────────────────


class SmashConfigUpdate(BaseModel):
    """Smash-rule configuration. All values come from the tournament
    ``rules.smash`` JSONB (see ``app/api/v1/tournament/schemas.py``) or
    operator input — nothing is hardcoded by the engine."""

    enabled: Optional[bool] = Field(
        default=None, description="Master switch for the smash-rule variant"
    )
    target_set_after_match: Optional[int] = Field(
        default=None,
        ge=1,
        description="Match after which the target is set (leader score + "
        "bonus). BMSD 2026 used 12.",
    )
    target_bonus_points: Optional[int] = Field(
        default=None,
        ge=0,
        description="Bonus added to the leader's score to set the target. "
        "BMSD 2026 used 10 (116 + 10 = 126).",
    )
    final_match_number: Optional[int] = Field(
        default=None,
        ge=2,
        description="Last match; the no-conversion fallback fires when it "
        "completes. BMSD 2026 used 18.",
    )
    chickens_after_match_point: Optional[int] = Field(
        default=None,
        ge=1,
        description="WWCDs needed after hitting match point (observed: 1, "
        "in a later match).",
    )


class SmashStandingRow(BaseModel):
    """One cumulative standings row after a completed match."""

    team_id: str
    team_name: Optional[str] = None
    total_points: float = Field(default=0.0)
    chickens: int = Field(default=0, ge=0)
    finishes: int = Field(default=0, ge=0)


class SmashMatchResultRequest(BaseModel):
    """Record one completed match into the smash-rule state machine."""

    match_number: int = Field(ge=1, description="1-based; must increase")
    winner_team_id: Optional[str] = Field(
        default=None,
        description="Chicken-dinner winner; omit when not identifiable",
    )
    standings: List[SmashStandingRow] = Field(
        min_length=1,
        description="Cumulative standings AFTER this match",
    )


class SmashTransitionResponse(BaseModel):
    transition: Dict[str, Any]
    smash: Dict[str, Any]
