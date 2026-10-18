"""Pydantic schemas for the WWCD ceremony API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class WinnerInfo(BaseModel):
    team_id: Optional[str] = None
    team_name: Optional[str] = None


class CeremonyState(BaseModel):
    step: str
    steps: List[str]
    winner: WinnerInfo
    stats: Optional[Dict[str, Any]] = None
    interview: Optional[Dict[str, Any]] = None
    updated_at: str


class CeremonyStartRequest(BaseModel):
    winner_team_id: Optional[str] = Field(
        default=None,
        description="Team id — resolved to a name via the real teams service",
    )
    team_name: Optional[str] = Field(
        default=None,
        description="Explicit winner name (used as-is; required when no "
        "winner_team_id is given)",
    )
    stats: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Match stats payload, passed through verbatim "
        "(placement points, finishes, damage, ...). Never invented — "
        "omit for an honest 'pending' state.",
    )
    interview: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Winner interview payload, passed through verbatim "
        "(e.g. {player, team, quote}). Omit for an honest 'pending' state.",
    )
