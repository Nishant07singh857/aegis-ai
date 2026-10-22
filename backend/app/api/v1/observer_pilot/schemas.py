"""Pydantic schemas for the /api/v1/observer-pilot endpoints (Phase 29, W4)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class KillSwitchRequest(BaseModel):
    enabled: bool = Field(
        ...,
        description="True = pilot may tap (per mode policy); False = "
        "kill-switch OFF: no tap in any mode, overrides included.",
    )


class OverrideRequest(BaseModel):
    slot_number: int = Field(..., ge=1, le=16, description="Observer slot 1..16.")
    roster_index: int = Field(
        ..., ge=0, le=3, description="Roster index 0..3 to spectate."
    )
