from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class AntiCheatFlag(Base):
    """A persisted anti-cheat review flag (Phase 22).

    Written by the vision-derivable checks in
    ``app.services.vision_intelligence.anti_cheat`` (impossible position
    jumps, kill-feed rate anomalies, alive-count inconsistencies).
    ``evidence`` holds the exact observations the check fired on — raw
    values with their units — so a human reviewer (admin/director) can
    confirm or dismiss from the review UI. Flags are NEVER auto-bans:
    ``status`` starts ``pending`` and only a reviewer moves it to
    ``confirmed`` or ``dismissed``.

    Team/player fields are nullable on purpose: attribution comes from
    the observer-slot mapping, and a flag raised on an event whose team
    could not be resolved is not written at all (the subscriber drops
    unmapped events) — but kill-feed flags key on the killer name the
    feed itself reported, which may not resolve to a DB player id.
    """

    __tablename__ = "anti_cheat_flags"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    match_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    tournament_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    # Precise check that fired (e.g. "impossible_position_jump") and the
    # closest CheatType from referee_agents.cheating_detector.
    check_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    cheat_type: Mapped[str] = mapped_column(String(32), nullable=False)

    player_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    player_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    team_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    team_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # Detector confidence vocabulary (certain|high|moderate|low|
    # suspicious|inconclusive) + the 0..100 score behind it.
    confidence: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # pending | confirmed | dismissed
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", index=True
    )
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reviewed_at_utc: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    detected_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
