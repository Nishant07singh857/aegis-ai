"""Pydantic v2 schemas for the teams endpoints."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    tag: Optional[str] = Field(default=None, max_length=32)
    logo_url: Optional[str] = Field(default=None, max_length=500)
    colors: dict = Field(default_factory=dict)


class TeamUpdate(BaseModel):
    """Full replacement payload — all fields required."""

    name: str = Field(min_length=1, max_length=200)
    tag: Optional[str] = Field(default=None, max_length=32)
    logo_url: Optional[str] = Field(default=None, max_length=500)
    colors: dict = Field(default_factory=dict)


class TeamPatch(BaseModel):
    """Partial update payload — every field optional."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    tag: Optional[str] = Field(default=None, max_length=32)
    logo_url: Optional[str] = Field(default=None, max_length=500)
    colors: Optional[dict] = None


class TeamResponse(BaseModel):
    """One team. ``source`` tells the caller where it came from.

    Demo-sourced teams carry the rich simulation fields (rank, kills, ...).
    DB-sourced teams honestly report ``None`` for stats columns that do not
    exist on the ``teams`` table.
    """

    id: str
    name: str
    tag: Optional[str] = None
    logo: Optional[str] = None       # demo dataset's emoji logo
    logo_url: Optional[str] = None   # DB column
    color: Optional[str] = None      # demo dataset's hex color
    colors: dict = Field(default_factory=dict)  # DB JSONB column
    rank: Optional[int] = None
    kills: Optional[int] = None
    placement_pts: Optional[int] = None
    total_pts: Optional[int] = None
    wwcd: Optional[int] = None
    alive: Optional[bool] = None
    players: Optional[list] = None
    source: Literal["db", "demo"]


class TeamListResponse(BaseModel):
    """Success envelope preserved from the original inline handlers."""

    data: List[TeamResponse]


class TeamDetailResponse(BaseModel):
    """Success envelope preserved from the original inline handlers."""

    data: TeamResponse
