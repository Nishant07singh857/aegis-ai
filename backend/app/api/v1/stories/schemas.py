"""Pydantic schemas for the AI storyteller API (Phase 19)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    match_id: str = Field(..., min_length=1, description="Match to generate stories for")
    types: List[str] = Field(
        ...,
        min_length=1,
        description=(
            "Story types to generate: head_to_head, win_probability, "
            "player_comparison, team_form, zone_advantage, milestone, comeback. "
            "Unknown types are ignored."
        ),
    )
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Entity ids each type needs: entity_a_id/entity_b_id/kind "
            "(head_to_head), player_a_id/player_b_id (player_comparison), "
            "team_id (+last_n, tournament_id) (team_form, comeback), "
            "player_id (milestone), teams_state/match_context (win_probability)."
        ),
    )


class StoryResponse(BaseModel):
    id: str
    match_id: Optional[str] = None
    tournament_id: Optional[str] = None
    story_type: str
    title: str
    body: str
    data: Dict[str, Any] = Field(default_factory=dict)
    confidence: Optional[float] = None
    status: str
    generated_at: datetime
    triggered_by: Optional[str] = None


class StorySettingItem(BaseModel):
    story_type: str = Field(..., min_length=1)
    template: str = Field(..., min_length=1, description="Graphics template id")
    animation_style: str = Field(..., min_length=1, description="Animation style id")


class TriggerRequest(BaseModel):
    """Optional per-trigger graphics overrides.

    Carried on the story.trigger event payload so the broadcast output
    renders this trigger with the director's chosen template/animation.
    Tournament defaults (story_settings) apply when omitted.
    """

    template_id: Optional[str] = Field(
        default=None, description="Graphics template id override"
    )
    animation_style: Optional[str] = Field(
        default=None, description="Animation style override: slide|fade|scale|none"
    )


class StorySettingsUpdate(BaseModel):
    tournament_id: Optional[str] = Field(
        default=None,
        description="Tournament to scope settings to; null = global default",
    )
    settings: List[StorySettingItem] = Field(..., min_length=1)


class StorySettingsResponse(BaseModel):
    tournament_id: Optional[str] = None
    settings: List[StorySettingItem]
