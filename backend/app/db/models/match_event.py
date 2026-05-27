from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class MatchEvent(Base):
    __tablename__ = "match_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    match_id: Mapped[str] = mapped_column(String(36), ForeignKey("matches.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)

    timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    real_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    event_data: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    team_id: Mapped[Optional[str]] = mapped_column(String(36))
    player_id: Mapped[Optional[str]] = mapped_column(String(36))
    target_team_id: Mapped[Optional[str]] = mapped_column(String(36))
    target_player_id: Mapped[Optional[str]] = mapped_column(String(36))

    match = relationship("Match", back_populates="events")
