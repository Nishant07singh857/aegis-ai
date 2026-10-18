"""Pydantic schemas for the zone timer API."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ZonePhase(BaseModel):
    name: str = Field(..., min_length=1, description="Phase name, e.g. 'Zone 3'")
    duration_seconds: int = Field(..., gt=0, description="Phase length in seconds")


class ZonePhaseInput(BaseModel):
    name: str = Field(..., min_length=1)
    duration_seconds: int = Field(..., gt=0)


class ZonePhasesRequest(BaseModel):
    phases: List[ZonePhaseInput] = Field(
        ..., min_length=1, description="Ordered phase schedule"
    )


class ZoneCurrentPhase(BaseModel):
    name: str
    duration_seconds: int
    phase_number: int
    total_phases: int


class ZoneState(BaseModel):
    status: str = Field(
        description="no_data | active | complete — no_data is the honest "
        "initial state until an operator configures phases"
    )
    phases: List[ZonePhase]
    current_phase: Optional[ZoneCurrentPhase] = None
    time_remaining_seconds: Optional[float] = None
    updated_at: str
