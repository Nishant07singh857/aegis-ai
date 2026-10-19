from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    tag: Mapped[Optional[str]] = mapped_column(String(32))
    logo_url: Mapped[Optional[str]] = mapped_column(String(500))
    colors: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    region: Mapped[Optional[str]] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    players = relationship("Player", back_populates="team", cascade="all, delete-orphan")
