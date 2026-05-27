from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class TeamIntelligence(Base):
    __tablename__ = "team_intelligence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    team_id: Mapped[str] = mapped_column(String(36), ForeignKey("teams.id"), nullable=False)

    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    aggression_score: Mapped[Optional[float]] = mapped_column(Float)
    chemistry_score: Mapped[Optional[float]] = mapped_column(Float)
    profile: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
