from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class Highlight(Base):
    __tablename__ = "highlights"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    match_id: Mapped[str] = mapped_column(String(36), ForeignKey("matches.id"), nullable=False)

    title: Mapped[Optional[str]] = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text)

    start_timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    video_path: Mapped[Optional[str]] = mapped_column(String(500))
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
