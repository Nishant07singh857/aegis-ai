from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Float, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    match_id: Mapped[str] = mapped_column(String(36), ForeignKey("matches.id"), nullable=False)

    timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    prediction_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[Optional[str]] = mapped_column(String(36))

    predicted_value: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
