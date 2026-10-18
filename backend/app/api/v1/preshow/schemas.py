"""Pydantic schemas for the pre-show API."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class PreshowState(BaseModel):
    step: str
    steps: List[str]
    countdown_target: Optional[str]
    updated_at: str


class StepRequest(BaseModel):
    step: str = Field(
        ..., description="One of: countdown, desk_intro, match_intro, starting"
    )


class CountdownRequest(BaseModel):
    target_iso: str = Field(
        ..., description="ISO-8601 timestamp the countdown runs to"
    )
