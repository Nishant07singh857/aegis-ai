# backend/app/api/v1/predictions/schemas.py
"""Schemas for the Global Predictive API (Phase 20, W1).

Honest-data contract: ``win_probability`` is always computed from real
match state by the live scorer; when the backend cannot compute it the
endpoint returns 404 and the frontend renders "—" — never invented
percentages.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class TeamWinProbability(BaseModel):
    team_id: str
    team_name: str
    #: 0-100, computed from real match state. Never invented.
    win_probability: float = Field(..., ge=0, le=100)
    #: Change vs the previous 30s snapshot; null on first sighting.
    delta: Optional[float] = None
    alive_count: int = Field(..., ge=0)
    kills: int = Field(..., ge=0)
    in_zone: bool = True


class WinProbabilityResponse(BaseModel):
    match_id: str
    updated_at_utc: str
    teams_alive: int
    #: Current zone phase, when the feed has reported one.
    zone_phase: Optional[int] = None
    #: Ordered by win_probability descending.
    teams: List[TeamWinProbability]
