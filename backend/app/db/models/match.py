from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tournament_id: Mapped[str] = mapped_column(String(36), ForeignKey("tournaments.id"), nullable=False)

    match_number: Mapped[int] = mapped_column(Integer, nullable=False)
    map_name: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="scheduled", nullable=False)

    scheduled_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    actual_start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    actual_end_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer)

    custom_room_id: Mapped[Optional[str]] = mapped_column(String(50))
    custom_room_password: Mapped[Optional[str]] = mapped_column(String(50))

    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    tournament = relationship("Tournament", back_populates="matches")
    events = relationship("MatchEvent", back_populates="match", cascade="all, delete-orphan")
