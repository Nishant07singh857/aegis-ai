"""Pydantic v2 schemas for the players endpoints."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

# Roles the admin UI and broadcast graphics understand. Stored as a plain
# string on the Player model; the API validates against this literal set.
PlayerRole = Literal["IGL", "assault", "support", "sniper"]


class PlayerCreate(BaseModel):
    team_id: Optional[str] = Field(
        default=None,
        max_length=36,
        description="Owning team id; null/omitted = unassigned roster",
    )
    name: str = Field(min_length=1, max_length=100)
    ign: str = Field(min_length=1, max_length=50)
    role: Optional[PlayerRole] = None
    avatar_url: Optional[str] = Field(default=None, max_length=500)
    is_active: bool = True


class PlayerUpdate(BaseModel):
    """Full replacement payload — every field except id required."""

    team_id: Optional[str] = Field(default=None, max_length=36)
    name: str = Field(min_length=1, max_length=100)
    ign: str = Field(min_length=1, max_length=50)
    role: Optional[PlayerRole] = None
    avatar_url: Optional[str] = Field(default=None, max_length=500)
    is_active: bool = True


class PlayerPatch(BaseModel):
    """Partial update payload — every field optional."""

    team_id: Optional[str] = Field(default=None, max_length=36)
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    ign: Optional[str] = Field(default=None, min_length=1, max_length=50)
    role: Optional[PlayerRole] = None
    avatar_url: Optional[str] = Field(default=None, max_length=500)
    is_active: Optional[bool] = None


class PlayerResponse(BaseModel):
    id: str
    team_id: Optional[str] = None
    name: str
    ign: str
    role: Optional[PlayerRole] = None
    avatar_url: Optional[str] = None
    is_active: bool = True


class PlayerListResponse(BaseModel):
    """Success envelope (same shape as the teams endpoints)."""

    data: List[PlayerResponse]


class PlayerDetailResponse(BaseModel):
    """Success envelope (same shape as the teams endpoints)."""

    data: PlayerResponse
