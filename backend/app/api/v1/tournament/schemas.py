"""Pydantic v2 schemas for the tournament endpoints.

``rules`` JSONB schema (all keys optional; unknown keys are preserved but
ignored by the engine):

.. code-block:: json

    {
      "match_point": {
        "enabled": true,                          // master switch
        "mode": "points_threshold",               // "points_threshold" | "final_day_chicken" | "disabled"
        "points_threshold": 70,                   // required when mode="points_threshold"
        "final_day_number": 3,                    // required when mode="final_day_chicken"
        "current_day": 1
      },
      "smash": {
        "enabled": false,                         // Smash-rule variant master switch
        "chickens_after_match_point": 1           // WWCDs needed after hitting match point
      },
      "tiebreakers": [                            // ordered; first decisive one wins
        "total_kills", "total_damage", "highest_placement"
      ],
      "scoring": {
        "bonus_rules": {                          // overrides PointsCalculator defaults
          "first_blood": {"points": 2},
          "most_kills": {"points": 3},
          "squad_wipe": {"points": 2},
          "flawless_victory": {"points": 5}
        }
      }
    }

``placement_points`` JSONB schema: string keys ``"1"``..``"16"`` mapping to
integer points, e.g. ``{"1": 10}`` for the Krafton BMSD format (chicken =
10 points, no other placement points). Missing placements score 0.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

TournamentStatus = Literal["draft", "active", "completed", "archived", "inactive"]


class TournamentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: Optional[str] = Field(
        default=None,
        max_length=200,
        description="URL slug; auto-derived from name when omitted",
    )
    description: Optional[str] = None
    status: TournamentStatus = "draft"
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    total_matches: int = Field(default=0, ge=0)
    points_per_kill: int = Field(default=1, ge=0)
    placement_points: Dict[str, int] = Field(
        default_factory=dict,
        description='Placement table, e.g. {"1": 10}. Missing placements score 0.',
    )
    rules: dict = Field(default_factory=dict)
    prize_pool: Optional[str] = Field(default=None, max_length=100)
    organizer: Optional[str] = Field(default=None, max_length=200)
    is_public: bool = True


class TournamentUpdate(BaseModel):
    """Full replacement payload — every field except id required."""

    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    status: TournamentStatus = "draft"
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    total_matches: int = Field(default=0, ge=0)
    points_per_kill: int = Field(default=1, ge=0)
    placement_points: Dict[str, int] = Field(default_factory=dict)
    rules: dict = Field(default_factory=dict)
    prize_pool: Optional[str] = Field(default=None, max_length=100)
    organizer: Optional[str] = Field(default=None, max_length=200)
    is_public: bool = True


class TournamentPatch(BaseModel):
    """Partial update payload — every field optional."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    slug: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    status: Optional[TournamentStatus] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    total_matches: Optional[int] = Field(default=None, ge=0)
    points_per_kill: Optional[int] = Field(default=None, ge=0)
    placement_points: Optional[Dict[str, int]] = None
    rules: Optional[dict] = None
    prize_pool: Optional[str] = Field(default=None, max_length=100)
    organizer: Optional[str] = Field(default=None, max_length=200)
    is_public: Optional[bool] = None


class TournamentResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: Optional[str] = None
    status: TournamentStatus
    is_active: bool = False  # derived: status == "active"
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    total_matches: int = 0
    points_per_kill: int = 1
    placement_points: Dict[str, int] = Field(default_factory=dict)
    rules: dict = Field(default_factory=dict)
    prize_pool: Optional[str] = None
    organizer: Optional[str] = None
    is_public: bool = True


class TournamentListResponse(BaseModel):
    """Success envelope (same shape as the teams endpoints)."""

    data: List[TournamentResponse]


class TournamentDetailResponse(BaseModel):
    """Success envelope (same shape as the teams endpoints)."""

    data: TournamentResponse
