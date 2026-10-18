# backend/app/db/models/observer_slot.py
"""Observer slot model (Phase 12).

A tournament runs 16 observer slots. Each slot is a real DB row,
assigned to at most one team at a time, and reports its feed status
via heartbeats. Mid-match reassignment is a plain UPDATE — no history
is kept on purpose (assignments are transient tournament-ops state).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class ObserverSlot(Base):
    __tablename__ = "observer_slots"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Tournament observer slot number, 1..16, one row per slot.
    slot_number: Mapped[int] = mapped_column(
        Integer, unique=True, nullable=False, index=True
    )
    # Nullable: an unassigned slot simply has no team.
    team_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
    )
    # idle | connected | feeding — kept as a plain string so the frontend
    # and heartbeat payloads never need an enum round-trip.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")
    last_heartbeat: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
