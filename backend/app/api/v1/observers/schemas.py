# backend/app/api/v1/observers/schemas.py
"""Pydantic v2 schemas for the observer-slot endpoints."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class SlotStatus(str, Enum):
    """Observer client feed status."""

    IDLE = "idle"
    CONNECTED = "connected"
    FEEDING = "feeding"


class ObserverSlotResponse(BaseModel):
    """One observer slot. ``team_id`` is None when unassigned."""

    slot_number: int = Field(ge=1, le=16)
    team_id: Optional[str] = None
    team_name: Optional[str] = None
    status: SlotStatus
    last_heartbeat: Optional[datetime] = None


class ObserverSlotListResponse(BaseModel):
    """Success envelope matching the teams API's {data: [...]} shape."""

    data: List[ObserverSlotResponse]


class AssignSlotRequest(BaseModel):
    team_id: str = Field(min_length=1, max_length=36)


class HeartbeatRequest(BaseModel):
    status: SlotStatus
