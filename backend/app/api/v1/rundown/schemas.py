"""Pydantic schemas for the /api/v1/rundown endpoints.

Run-of-show: an ordered list of broadcast segments with planned
durations, a current-segment pointer and start timestamps.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class SegmentCreate(BaseModel):
    title: str = Field(..., description="Segment title, e.g. 'Map 2 — Erangel'.")
    planned_duration_s: int = Field(
        ..., gt=0, description="Planned length in seconds (must be > 0)."
    )
    notes: Optional[str] = Field(
        default=None, description="Producer notes for this segment."
    )
    order: Optional[int] = Field(
        default=None,
        ge=0,
        description="0-based insert position; omitted = append at the end.",
    )


class SegmentUpdate(BaseModel):
    title: Optional[str] = Field(default=None)
    planned_duration_s: Optional[int] = Field(default=None, gt=0)
    notes: Optional[str] = Field(default=None)

    def has_changes(self) -> bool:
        return any(
            getattr(self, field) is not None
            for field in ("title", "planned_duration_s", "notes")
        )


class SegmentReorder(BaseModel):
    new_order: int = Field(
        ...,
        ge=0,
        description="0-based position to move the segment to "
        "(clamped to the list bounds).",
    )


class RundownSegment(BaseModel):
    id: str
    title: str
    planned_duration_s: int
    notes: str
    order: int


class RundownState(BaseModel):
    segments: List[RundownSegment]
    current_index: int = Field(
        description="-1 = rundown not started yet; otherwise the index of "
        "the segment currently on air."
    )
    started_at: Optional[str] = None
    segment_started_at: Optional[str] = None
    updated_by: Optional[str] = None
    updated_at: Optional[str] = None
