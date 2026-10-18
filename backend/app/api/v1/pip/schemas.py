"""Pydantic schemas for the /api/v1/pip endpoints.

PiP = picture-in-picture layout on the broadcast output pages.
`main_source` / `inset_source` are source *keys* (e.g. "output",
"map-cam") — never fabricated video feeds.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.services.pip import INSET_POSITIONS, SOURCES


class PiPStateUpdate(BaseModel):
    """Partial PiP update — omit any field to leave it unchanged."""

    enabled: Optional[bool] = Field(
        default=None, description="Show or hide the inset."
    )
    main_source: Optional[str] = Field(
        default=None,
        description='Main view source key, e.g. "output" or "map-cam".',
    )
    inset_source: Optional[str] = Field(
        default=None, description='Inset view source key, e.g. "map-cam".'
    )
    inset_position: Optional[str] = Field(
        default=None,
        description='Inset corner: "top_left" | "top_right" | '
        '"bottom_left" | "bottom_right".',
    )

    @field_validator("inset_position")
    @classmethod
    def _validate_position(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if value not in INSET_POSITIONS:
            raise ValueError(
                f"Invalid inset_position {value!r}: must be one of "
                f"{', '.join(INSET_POSITIONS)}."
            )
        return value

    @field_validator("main_source", "inset_source")
    @classmethod
    def _validate_source(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if value not in SOURCES:
            raise ValueError(
                f"Invalid source key {value!r}: must be one of "
                f"{', '.join(SOURCES)}."
            )
        return value

    def has_changes(self) -> bool:
        return any(
            getattr(self, field) is not None
            for field in (
                "enabled",
                "main_source",
                "inset_source",
                "inset_position",
            )
        )


class PiPState(BaseModel):
    enabled: bool
    main_source: str
    inset_source: str
    inset_position: str
    updated_by: Optional[str] = None
    updated_at: str
