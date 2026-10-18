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
