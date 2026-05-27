from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import ForeignKey, Float, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class Player(Base):
    __tablename__ = "players"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    team_id: Mapped[str] = mapped_column(String(36), ForeignKey("teams.id"), nullable=False)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    ign: Mapped[str] = mapped_column(String(50), nullable=False)
    role: Mapped[Optional[str]] = mapped_column(String(50))
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500))

    playstyle: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    preferred_weapons: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    clutch_score: Mapped[Optional[float]] = mapped_column(Float)
    avg_damage: Mapped[Optional[float]] = mapped_column(Float)
    avg_kills: Mapped[Optional[float]] = mapped_column(Float)

    team = relationship("Team", back_populates="players")
