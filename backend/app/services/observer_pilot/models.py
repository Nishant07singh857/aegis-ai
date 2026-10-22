# backend/app/services/observer_pilot/models.py
"""Observer Auto-Pilot — POV journal ORM model (Phase 29, W4).

One table, created by migration ``016_pov_pilot``:

* ``pov_pilot_decisions`` — every POV pilot event, durable: an executed
  tap (with its TapResult), a semi_auto suggestion and its fate
  (approved / rejected / expired), a manual/clone-grabbed SHADOW
  decision, a manual override, and failures. This is the data the
  Phase 27-style learning loop will read later, so it is a real DB
  table — an in-memory ring alone would lose it on restart.

Why NOT ``director_learning``'s tables with ``kind="pov"``:
``DirectorDecision`` has no slot / source / from-to roster index /
verification fields, its ``outcome`` vocabulary is the LiveDirector's,
and mixing POV rows into it would silently pollute the director
journal stats the Phase 27 engine learns from. The POV journal is a
sibling table with the same portable-JSON convention
(``JSON().with_variant(JSONB(), "postgresql")`` — JSONB on Postgres,
plain JSON on sqlite so unit tests need no metadata surgery).

``fate`` vocabulary (the row's lifecycle outcome):
  executed | suggested | approved | rejected | expired | shadow |
  override | failed | suppressed | dead_unconfirmed
  (``suppressed`` = a decision held by the D-2 failed-tap cooldown;
  the exact ``suppressed_failed_cooldown`` marker lives in the
  row's reason/evidence — it is 26 chars and this column is
  String(16). ``dead_unconfirmed`` = an R1 decision held by the
  D-1 DEAD-confirmation gate; both gate rows have taps_sent=0 and
  no verification — no tap was attempted.)
``verification`` carries the executor's TapResult status verbatim
(verified / failed_stay / blocked_kill_switch / no_device / no_frame /
identity_unknown) when a tap was attempted, else NULL — never invented.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: Portable JSON: JSONB on Postgres, plain JSON elsewhere (tests).
JsonColumn = JSON().with_variant(JSONB(), "postgresql")

#: Fate vocabulary for ``pov_pilot_decisions.fate``.
POV_FATES = (
    "executed",
    "suggested",
    "approved",
    "rejected",
    "expired",
    "shadow",
    "override",
    "failed",
    "suppressed",
    "dead_unconfirmed",
)


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class POVPilotDecision(Base):
    """One persisted POV pilot event (see module docstring)."""

    __tablename__ = "pov_pilot_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # The event's own timestamp (decision / fate time), not insert time.
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    slot_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    team_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    from_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    to_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Stable rule string (R1_SPECTATED_DEAD ...) or "manual_override".
    rule: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence: Mapped[Optional[dict]] = mapped_column(JsonColumn, nullable=True)
    # Director mode in force when the event happened.
    mode: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    fate: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # Suggestion id for suggested/approved/rejected/expired rows.
    suggestion_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
    # TapResult.status verbatim when a tap was attempted, else NULL.
    verification: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    taps_sent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    decided_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    decided_at_epoch: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


__all__ = ["POVPilotDecision", "POV_FATES", "JsonColumn"]
