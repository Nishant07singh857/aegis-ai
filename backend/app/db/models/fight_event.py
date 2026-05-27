from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import ForeignKey, Float, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class FightEvent(Base):
    __tablename__ = "fight_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    match_id: Mapped[str] = mapped_column(String(36), ForeignKey("matches.id"), nullable=False)

    start_timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_timestamp_ms: Mapped[Optional[int]] = mapped_column(Integer)

    team_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    player_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    location_x: Mapped[Optional[float]] = mapped_column(Float)
    location_y: Mapped[Optional[float]] = mapped_column(Float)
    map_name: Mapped[Optional[str]] = mapped_column(String(32))
    intensity_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    winner_team_id: Mapped[Optional[str]] = mapped_column(String(36))
    total_kills: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_knocks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    utility_used: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
