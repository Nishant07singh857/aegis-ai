"""Pydantic schemas for the /api/v1/director endpoints.

Director modes: "full_auto" | "semi_auto" | "manual". The legacy Phase 6
value "auto" is accepted by POST /director/mode and normalised to
"full_auto" — it is never stored or returned.

Invalid values (bad mode/scene/template id) are rejected by FastAPI
with 422 and a clear message. Operator writes attempted while the
director is not in MANUAL mode are rejected with 409 (see router) — 422
stays reserved for validation. Graphics template selection is the
exception: it is a direct operator write allowed in MANUAL and
SEMI-AUTO, rejected with 409 only in FULL_AUTO.
"""

from __future__ import annotations

import enum
from typing import Any, Dict, List, Optional

from pydantic import (
    BaseModel,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.services.director_service import GRAPHICS_TEMPLATE_OPTIONS

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


class GraphicsRequest(BaseModel):
    scorebug_template_id: Optional[str] = Field(
        default=None,
        description='Scorebug template: "scorebug-classic" | '
        '"scorebug-modern" | "scorebug-minimal". MANUAL or SEMI-AUTO mode.',
    )
    lowerthird_template_id: Optional[str] = Field(
        default=None,
        description='Lower-third template: "lowerthird-slide" | '
        '"lowerthird-rise" | "lowerthird-banner". MANUAL or SEMI-AUTO mode.',
    )
    standings_template_id: Optional[str] = Field(
        default=None,
        description='Standings template: "standings-table" | '
        '"standings-cards". MANUAL or SEMI-AUTO mode.',
    )
    killfeed_template_id: Optional[str] = Field(
        default=None,
        description='Killfeed template: "killfeed-ticker" | '
        '"killfeed-stack". MANUAL or SEMI-AUTO mode.',
    )

    @field_validator(
        "scorebug_template_id",
        "lowerthird_template_id",
        "standings_template_id",
        "killfeed_template_id",
    )
    @classmethod
    def _validate_template_id(
        cls, value: Optional[str], info: ValidationInfo
    ) -> Optional[str]:
        if value is None:
            return None
        options = GRAPHICS_TEMPLATE_OPTIONS[info.field_name]
        normalised = value.strip()
        if normalised not in options:
            raise ValueError(
                f"Invalid template id {value!r} for '{info.field_name}': "
                f"must be one of {', '.join(options)}."
            )
        return normalised

    @model_validator(mode="after")
    def _at_least_one(self) -> "GraphicsRequest":
        if all(
            getattr(self, field_name) is None
            for field_name in (
                "scorebug_template_id",
                "lowerthird_template_id",
                "standings_template_id",
                "killfeed_template_id",
            )
        ):
            raise ValueError(
                "Provide at least one of 'scorebug_template_id', "
                "'lowerthird_template_id', 'standings_template_id' or "
                "'killfeed_template_id'."
            )
        return self


class DirectorOverlays(BaseModel):
    scorebug: bool
    killfeed: bool


class DirectorLowerThird(BaseModel):
    visible: bool
    title: str
    subtitle: str


class DirectorGraphics(BaseModel):
    scorebug_template_id: str
    lowerthird_template_id: str
    standings_template_id: str
    killfeed_template_id: str


class DirectorState(BaseModel):
    mode: str
    scene: str
    overlays: DirectorOverlays
    lower_third: DirectorLowerThird
    graphics: DirectorGraphics
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
