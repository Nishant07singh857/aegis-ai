# backend/app/services/director_learning/models.py
"""Director Learning Loop — ORM models (Phase 27, W1 foundation).

Six tables, created by migration ``015_director_learning``:

* ``director_decisions``           — every LiveDirector decision, persisted
  from the ``_log_decision`` funnel (the journal).
* ``director_suggestion_fates``    — what a human actually did with each
  semi_auto suggestion (approved / rejected / expired / modified).
* ``director_shadow_divergences``  — manual-mode shadow reconciliation:
  where the human director and the AI's shadow evaluation disagreed.
* ``director_decision_labels``     — CREATED NOW, written by W2's labeler.
* ``director_learning_adjustments``— CREATED NOW, written by W3's engine.
* ``director_learning_state``      — CREATED NOW, single-row learning mode
  (``suggest`` by default); W3 owns writes. Row id convention:
  :data:`LEARNING_STATE_ID`.

JSON columns use ``JSON().with_variant(JSONB(), "postgresql")``: on
Postgres the DDL is exactly JSONB (same as the anti-cheat model's plain
JSONB), and the variant keeps the tables creatable on sqlite so unit
tests need no global metadata surgery (cf. the portable-JSON precedent
in ``app/db/models/event_store.py``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, DateTime, Float, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: Portable JSON: JSONB on Postgres, plain JSON elsewhere (tests).
JsonColumn = JSON().with_variant(JSONB(), "postgresql")

#: Single-row id for ``director_learning_state`` (W3 upserts this id).
LEARNING_STATE_ID = "singleton"

#: Fate vocabulary for ``director_suggestion_fates.fate``.
SUGGESTION_FATES = ("approved", "rejected", "expired", "modified")

#: Divergence vocabulary for ``director_shadow_divergences.kind``.
DIVERGENCE_KINDS = ("human_switched_ai_held", "ai_wanted_human_stayed")


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DirectorDecision(Base):
    """One persisted LiveDirector decision (journal row).

    Written by :class:`~.journal.DecisionJournal` from the exact dict
    ``LiveDirector._log_decision`` received — no field is recomputed or
    invented at write time. ``ts`` is the decision's own timestamp (the
    entry's ``timestamp``), not the insert time.
    """

    __tablename__ = "director_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    mode: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    phase: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    trigger: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    outcome: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    rule: Mapped[Optional[str]] = mapped_column(String(96), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    margin_required: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    margin_actual: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    incumbent: Mapped[Optional[dict]] = mapped_column(JsonColumn, nullable=True)
    challenger: Mapped[Optional[dict]] = mapped_column(JsonColumn, nullable=True)
    candidates: Mapped[Optional[list]] = mapped_column(JsonColumn, nullable=True)
    suggestion_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )


class DirectorSuggestionFate(Base):
    """The observed fate of one semi_auto suggestion.

    One row per observed fate event. ``modified`` — when it happens — is
    a SECOND row for the same ``suggestion_id`` (the original
    rejected/expired row is never mutated; consumers take the LATEST
    row per suggestion, see ``queries.fate_for_suggestion``).
    ``decision_id`` links back to the journal decision that produced
    the suggestion when the journal saw it (else NULL — honest).
    """

    __tablename__ = "director_suggestion_fates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    suggestion_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    decision_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # approved | rejected | expired | modified
    fate: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    decided_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    #: For ``modified``: what the human actually put on air instead —
    #: only fields actually observed (scene / active_camera / team_id /
    #: via / via_suggestion_id). NULL for plain approved/rejected/expired.
    chosen_alternative: Mapped[Optional[dict]] = mapped_column(
        JsonColumn, nullable=True
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class DirectorShadowDivergence(Base):
    """One manual-mode divergence between the human and the AI shadow.

    ``human_switched_ai_held`` — the human cut to a target the shadow
    evaluation would not have cut to (different challenger, or the
    challenger's margin would not have cleared the full_auto ladder).
    ``ai_wanted_human_stayed`` — a shadow challenger WOULD have won
    under full_auto rules and no human switch followed within 15 s.
    """

    __tablename__ = "director_shadow_divergences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    phase: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    human_action: Mapped[Optional[dict]] = mapped_column(JsonColumn, nullable=True)
    ai_candidate: Mapped[Optional[dict]] = mapped_column(JsonColumn, nullable=True)
    shadow_decision_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True, index=True
    )
    detail: Mapped[Optional[dict]] = mapped_column(JsonColumn, nullable=True)


class DirectorDecisionLabel(Base):
    """A label on one journal decision. CREATED NOW — W2 writes these."""

    __tablename__ = "director_decision_labels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    decision_id: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # low | medium | high
    confidence_band: Mapped[str] = mapped_column(
        String(8), nullable=False, default="low"
    )
    evidence: Mapped[dict] = mapped_column(JsonColumn, default=dict, nullable=False)
    labeler_version: Mapped[str] = mapped_column(String(32), nullable=False)
    labeled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class DirectorLearningAdjustment(Base):
    """One learning adjustment. CREATED NOW — W3 writes these."""

    __tablename__ = "director_learning_adjustments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    knob: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    bucket: Mapped[dict] = mapped_column(JsonColumn, default=dict, nullable=False)
    before_value: Mapped[float] = mapped_column(Float, nullable=False)
    after_value: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict] = mapped_column(JsonColumn, default=dict, nullable=False)
    learning_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    applied_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    parent_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)


class DirectorLearningState(Base):
    """Single-row learning state. CREATED NOW — W3 writes it.

    Single-row semantics: the row with ``id == LEARNING_STATE_ID``
    (``"singleton"``) is THE state row; ``learning_mode`` defaults to
    ``"suggest"`` (adjustments are proposed, never auto-applied, until
    a human flips the mode). ``updated_at`` is the Base audit column.
    """

    __tablename__ = "director_learning_state"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    learning_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="suggest"
    )
    updated_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
