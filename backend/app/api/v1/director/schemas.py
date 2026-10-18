"""Pydantic schemas for the /api/v1/director endpoints.

Invalid enum values (bad mode/scene) are rejected by FastAPI with 422 and a
clear message. Operator writes attempted while the director is in AUTO mode
are rejected with 409 (see router) — 422 stays reserved for validation.
"""

from __future__ import annotations

import enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class DirectorMode(str, enum.Enum):
    AUTO = "auto"
    MANUAL = "manual"


class DirectorScene(str, enum.Enum):
    MAIN = "main"
    STANDINGS = "standings"
    KILLFEED_FOCUS = "killfeed_focus"
    COMMENTARY = "commentary"


class ModeRequest(BaseModel):
    mode: DirectorMode = Field(
        ..., description='Director mode: "auto" or "manual".'
    )


class SceneRequest(BaseModel):
    scene: DirectorScene = Field(
        ...,
        description='Output scene: "main", "standings", "killfeed_focus" '
        'or "commentary". MANUAL mode only.',
    )


class OverlaysRequest(BaseModel):
    scorebug: Optional[bool] = Field(
        default=None, description="Show/hide the scorebug overlay."
    )
    killfeed: Optional[bool] = Field(
        default=None, description="Show/hide the killfeed overlay."
    )

    @model_validator(mode="after")
    def _at_least_one(self) -> "OverlaysRequest":
        if self.scorebug is None and self.killfeed is None:
            raise ValueError(
                "Provide at least one of 'scorebug' or 'killfeed'."
            )
        return self


class LowerThirdRequest(BaseModel):
    visible: bool = Field(..., description="Show or hide the lower third.")
    title: str = Field(default="", description="Lower-third headline.")
    subtitle: str = Field(default="", description="Lower-third sub-line.")


class DirectorOverlays(BaseModel):
    scorebug: bool
    killfeed: bool


class DirectorLowerThird(BaseModel):
    visible: bool
    title: str
    subtitle: str


class DirectorState(BaseModel):
    mode: str
    scene: str
    overlays: DirectorOverlays
    lower_third: DirectorLowerThird
    updated_by: Optional[str] = None
    updated_at: str
