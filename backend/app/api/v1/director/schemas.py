"""Pydantic schemas for the /api/v1/director endpoints.

Director modes: "full_auto" | "semi_auto" | "manual" | "clone"
(Phase 27, W3/W4: "clone" is the Director-Style mode — the AI drives
with the learned knob profile; see docs/DIRECTOR_LEARNING.md). The
legacy Phase 6 value "auto" is accepted by POST /director/mode and
normalised to "full_auto" — it is never stored or returned.

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

DIRECTOR_MODES = ("full_auto", "semi_auto", "manual", "clone")


class DirectorScene(str, enum.Enum):
    MAIN = "main"
    STANDINGS = "standings"
    KILLFEED_FOCUS = "killfeed_focus"
    COMMENTARY = "commentary"
    # Phase 18 (W3): tactical map-cam overview (Visual Broadcast Bible §2).
    # 15-30s holds; the director auto-returns to "main" after 30s max.
    MAP_CAM = "map_cam"


class SuggestionType(str, enum.Enum):
    SCENE = "scene"
    OVERLAY = "overlay"
    LOWER_THIRD = "lower_third"


class ModeRequest(BaseModel):
    mode: str = Field(
        ...,
        description='Director mode: "full_auto", "semi_auto", "manual" '
        'or "clone". The legacy value "auto" is accepted and normalised '
        'to "full_auto".',
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
                '"semi_auto", "manual" or "clone".'
            )
        return normalised


class SceneRequest(BaseModel):
    scene: DirectorScene = Field(
        ...,
        description='Output scene: "main", "standings", "killfeed_focus" '
        'or "commentary". MANUAL mode only.',
    )


class CameraRequest(BaseModel):
    """Phase 17 W2: switch the active observer camera (operator, MANUAL)."""

    slot: int = Field(
        ...,
        ge=1,
        le=16,
        description="Observer camera slot 1..16 shown on the broadcast "
        "output video layer (GET /api/v1/video/feed/{slot}). MANUAL mode "
        "only.",
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
        '"standings-cards" | "standings-mvp" | "standings-eliminator" | '
        '"standings-igl" | "standings-finals-mvp" | '
        '"standings-spotlight". MANUAL or SEMI-AUTO mode.',
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
    # Phase 18 (W3): ISO timestamp of the last scene change; drives the
    # map-cam 30s maximum hold and the frontend auto-return countdown.
    scene_entered_at: str = Field(
        default="",
        description="ISO timestamp of the last scene change.",
    )
    overlays: DirectorOverlays
    lower_third: DirectorLowerThird
    graphics: DirectorGraphics
    # Phase 17 W2: active observer camera slot (1..16) for the broadcast
    # output video layer (GET /api/v1/video/feed/{slot}).
    active_camera: int = Field(
        default=1,
        ge=1,
        le=16,
        description="Active observer camera slot (1..16) shown on the "
        "broadcast output video layer.",
    )
    # Phase 25 (W1): PVW preview candidate from the live director —
    # {slot, scene, team_id, fight_id, reason, score, breakdown,
    # updated_at}; null when there is no candidate.
    preview: Optional[Dict[str, Any]] = Field(
        default=None,
        description="PVW preview: the live director's top candidate "
        "(pre-positioned next feed), or null.",
    )
    # Phase 27 (W3/W4): clone-mode manual grab. Only meaningful when
    # mode == "clone": while grabbed, the human drives (manual
    # semantics) and the AI is silent until release / inactivity.
    clone_grabbed: bool = Field(
        default=False,
        description="True while a human holds manual control inside "
        "CLONE mode (POST /director/clone/grab).",
    )
    clone_grabbed_by: Optional[str] = Field(
        default=None,
        description="Username that grabbed clone control, or null.",
    )
    clone_grabbed_at: Optional[str] = Field(
        default=None,
        description="ISO timestamp of the clone grab, or null.",
    )
    # Phase 29 (W4): per-feed POV summary from the live director's
    # POV provider — [{slot_number, source_id, team_id,
    # spectated_index, spectated_name, identity_known}]. Empty when
    # no POV provider/monitor is wired; never fabricated.
    pov_feeds: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Per-feed spectated-player POV summary (Phase 29).",
    )
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
