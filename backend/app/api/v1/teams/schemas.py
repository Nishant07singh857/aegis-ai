"""Pydantic v2 schemas for the teams endpoints."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    tag: Optional[str] = Field(default=None, max_length=32)
    logo_url: Optional[str] = Field(default=None, max_length=500)
    colors: dict = Field(default_factory=dict)
    region: Optional[str] = Field(default=None, max_length=100)
    is_active: bool = True


class TeamUpdate(BaseModel):
    """Full replacement payload — all fields required."""

    name: str = Field(min_length=1, max_length=200)
    tag: Optional[str] = Field(default=None, max_length=32)
    logo_url: Optional[str] = Field(default=None, max_length=500)
    colors: dict = Field(default_factory=dict)
    region: Optional[str] = Field(default=None, max_length=100)
    is_active: bool = True


class TeamPatch(BaseModel):
    """Partial update payload — every field optional."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    tag: Optional[str] = Field(default=None, max_length=32)
    logo_url: Optional[str] = Field(default=None, max_length=500)
    colors: Optional[dict] = None
    region: Optional[str] = Field(default=None, max_length=100)
    is_active: Optional[bool] = None


class TeamResponse(BaseModel):
    """One team. ``source`` is always ``"db"`` — demo data is never served
    from this API (the demo dataset lives only in the simulator).

    The teams table has no stats columns, so stats are honestly reported
    as ``None``.
    """

    id: str
    name: str
    tag: Optional[str] = None
    logo: Optional[str] = None       # reserved for future demo/dataset use
    logo_url: Optional[str] = None   # DB column
    color: Optional[str] = None      # convenience alias of colors.primary
    colors: dict = Field(default_factory=dict)  # DB JSONB column
    region: Optional[str] = None
    is_active: bool = True
    rank: Optional[int] = None
    kills: Optional[int] = None
    placement_pts: Optional[int] = None
    total_pts: Optional[int] = None
    wwcd: Optional[int] = None
    alive: Optional[bool] = None
    players: Optional[list] = None
    source: Literal["db", "demo"] = "db"


class TeamListResponse(BaseModel):
    """Success envelope preserved from the original inline handlers."""

    data: List[TeamResponse]


class TeamDetailResponse(BaseModel):
    """Success envelope preserved from the original inline handlers."""

    data: TeamResponse
