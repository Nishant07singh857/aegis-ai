"""Pydantic schemas for the /api/v1/director-learning endpoints (Phase 27, W4).

Every number served here comes from a real query over the
director-learning tables (journal stats, suggestion fates, decision
labels, adjustments) or from the knob provider's cache — nothing is
computed for show. Adjustment rows mirror
``DirectorLearningAdjustment.to_dict()`` (the engine's ``_row_dict``),
minus the soft-delete audit fields the engine already strips.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

#: Learning modes (engine.LEARNING_MODES — kept in sync by the engine's
#: own validation on write; this schema validates first for a clean 422).
LEARNING_MODES = ("off", "suggest", "auto")


class AdjustmentResponse(BaseModel):
    """One learning adjustment / proposal row."""

    id: str
    ts: str = Field(description="ISO timestamp the row was written.")
    status: str = Field(
        description='"proposed" | "applied" | "rejected" | "rolled_back".'
    )
    knob: str
    bucket: Dict[str, Any] = Field(
        description="Justifying bucket: {phase, engagement_state, rule_family}."
    )
    before_value: float
    after_value: float
    reason: str = Field(
        description="Plain-language reason with the numbers in it "
        "(written by the engine, or the rollback note)."
    )
    evidence: Dict[str, Any] = Field(
        description="{rule, n, rates, sample ids, watermark}."
    )
    learning_mode: str
    applied_by: Optional[str] = None
    parent_id: Optional[str] = Field(
        default=None,
        description="For a rollback compensating row: the adjustment "
        "it rolls back.",
    )
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class Contributor(BaseModel):
    """One human who decided suggestion fates (from decided_by)."""

    username: str
    decided_count: int


class LearnedFrom(BaseModel):
    """What the learned profile is built from — real row counts."""

    labeled_decisions: int = Field(
        description="Rows in director_decision_labels."
    )
    decided_fates: int = Field(
        description="Suggestion fates a human actually decided "
        "(approved + rejected + modified; expiries excluded)."
    )
    divergences: int = Field(
        description="Manual-mode shadow divergences recorded."
    )


class ProfileInfo(BaseModel):
    exists: bool = Field(
        description="True when >= 1 adjustment row is 'applied' — "
        "the learned profile exists only then (cold start otherwise)."
    )
    applied_count: int
    contributors: List[Contributor]
    learned_from: LearnedFrom


class JournalStats(BaseModel):
    decisions_total: int
    decisions_by_mode: Dict[str, int]
    decisions_by_outcome: Dict[str, int]
    fates_by_fate: Dict[str, int]
    divergences_by_kind: Dict[str, int]
    labels_total: int


class JournalStatus(BaseModel):
    """The in-process journal's own status (drop counters included).

    Null on the status response when no journal is running in this
    process — reported as null, never fabricated.
    """

    running: bool
    stats: Dict[str, int] = Field(
        description="Journal counters, incl. decisions_dropped / "
        "fates_dropped / divergences_dropped."
    )
    ring_size: int
    suggestion_index_size: int
    open_modified_watches: int
    writes_observed: int


class KnobState(BaseModel):
    """One learnable knob: registry bounds + both profiles' values."""

    name: str
    default: float
    effective_learned: float = Field(
        description="Value clone mode would use (default while the "
        "learning mode is off or no adjustment is applied)."
    )
    effective_factory: float = Field(
        description="Value the factory modes use — always the default."
    )
    min_value: float
    max_value: float
    step: float
    unit: str
    why: str


class LearningStatusResponse(BaseModel):
    learning_mode: str = Field(description='"off" | "suggest" | "auto".')
    profile: ProfileInfo
    journal_stats: JournalStats
    journal: Optional[JournalStatus] = None
    knobs: List[KnobState]


class LearningModeResponse(BaseModel):
    mode: str = Field(description='"off" | "suggest" | "auto".')


class LearningModeUpdate(BaseModel):
    mode: str = Field(..., description='"off" | "suggest" | "auto".')

    @field_validator("mode")
    @classmethod
    def _validate(cls, value: str) -> str:
        normalised = (value or "").strip().lower()
        if normalised not in LEARNING_MODES:
            raise ValueError(
                f'Invalid learning mode {value!r}: must be "off", '
                '"suggest" or "auto".'
            )
        return normalised
