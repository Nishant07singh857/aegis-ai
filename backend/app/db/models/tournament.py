from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class Tournament(Base):
    __tablename__ = "tournaments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)

    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    total_matches: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    points_per_kill: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    placement_points: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    rules: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    prize_pool: Mapped[Optional[str]] = mapped_column(String(100))
    organizer: Mapped[Optional[str]] = mapped_column(String(200))
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    matches = relationship("Match", back_populates="tournament", cascade="all, delete-orphan")
