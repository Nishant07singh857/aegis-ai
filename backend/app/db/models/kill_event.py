from __future__ import annotations

import uuid
from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class KillEvent(Base):
    __tablename__ = "kill_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    match_id: Mapped[str] = mapped_column(String(36), ForeignKey("matches.id"), nullable=False)
    timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    killer_player_id: Mapped[str] = mapped_column(String(36), nullable=False)
    victim_player_id: Mapped[str] = mapped_column(String(36), nullable=False)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
