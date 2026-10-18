"""Pydantic schemas for the /api/v1/director endpoints.

Director modes: "full_auto" | "semi_auto" | "manual". The legacy Phase 6
value "auto" is accepted by POST /director/mode and normalised to
"full_auto" — it is never stored or returned.

Invalid values (bad mode/scene) are rejected by FastAPI with 422 and a
clear message. Operator writes attempted while the director is not in
MANUAL mode are rejected with 409 (see router) — 422 stays reserved for
validation.
"""

from __future__ import annotations

import enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

DIRECTOR_MODES = ("full_auto", "semi_auto", "manual")


class DirectorScene(str, enum.Enum):
    MAIN = "main"
    STANDINGS = "standings"
    KILLFEED_FOCUS = "killfeed_focus"
    COMMENTARY = "commentary"


class SuggestionType(str, enum.Enum):
    SCENE = "scene"
    OVERLAY = "overlay"
    LOWER_THIRD = "lower_third"


class ModeRequest(BaseModel):
    mode: str = Field(
        ...,
        description='Director mode: "full_auto", "semi_auto" or "manual". '
        'The legacy value "auto" is accepted and normalised to "full_auto".',
    )

    @field_validator("mode")
    @classmethod
    def _normalise_and_validate(cls, value: str) -> str:
        normalised = (value or "").strip().lower()
        if normalised == "auto":
            # Phase 6 compatibility: "auto" was the old name of full_auto.
            # Never stored as "auto".
            normalised = "full_auto"
        if normalised not in DIRECTOR_MODES:
            raise ValueError(
                f'Invalid director mode {value!r}: must be "full_auto", '
                '"semi_auto" or "manual".'
            )
        return normalised


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


class DirectorSuggestion(BaseModel):
    """An AI suggestion awaiting operator approval (SEMI_AUTO mode)."""

    id: str
    type: str
    payload: Dict[str, Any] = Field(
        description='e.g. {"scene": "standings"}, '
        '{"overlay": "scorebug", "visible": false} or '
        '{"visible": true, "title": "...", "subtitle": "..."}.'
    )
    reason: str = Field(description="Human-readable reason for the proposal.")
    confidence: float = Field(ge=0.0, le=1.0)
    created_at: str
    expires_at: str
    status: str = Field(description='"pending" for queue entries.')


class SuggestionListResponse(BaseModel):
    suggestions: List[DirectorSuggestion]
