from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class BroadcastSession(Base):
    __tablename__ = "broadcast_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    match_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("matches.id"))
    status: Mapped[str] = mapped_column(String(32), default="created", nullable=False)

    started_at_utc: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ended_at_utc: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    obs_state: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
