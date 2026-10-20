"""Pydantic schemas for the anti-cheat review API (Phase 22)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AntiCheatFlagResponse(BaseModel):
    id: str
    match_id: Optional[str] = None
    tournament_id: Optional[str] = None
    check_name: str
    cheat_type: str
    player_id: Optional[str] = None
    player_name: Optional[str] = None
    team_id: Optional[str] = None
    team_name: Optional[str] = None
    confidence: str
    confidence_score: float
    description: str
    evidence: Dict[str, Any] = Field(default_factory=dict)
    status: str
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    detected_at: datetime


class ReviewRequest(BaseModel):
    action: str = Field(
        ...,
        description="confirm | dismiss",
    )


class CheckInfo(BaseModel):
    check: str
    cheat_type: str
    status: str
    reason: str


class ChecksResponse(BaseModel):
    live: List[CheckInfo]
    disabled: List[CheckInfo]
