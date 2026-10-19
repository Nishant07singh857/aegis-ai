from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class Story(Base):
    """A generated AI-storyteller story (Phase 19).

    Stories are generated on demand, listed for the director, and can be
    triggered onto the broadcast output or dismissed. ``data`` holds the
    structured payload the animated-graphics frontend renders; ``body`` is
    the Hinglish caster line. Nothing here is ever fabricated — a story is
    only persisted when its generator found real data.
    """

    __tablename__ = "stories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    match_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("matches.id"), nullable=True)
    tournament_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("tournaments.id"), nullable=True)

    story_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Float)

    # generated | triggered | dismissed
    status: Mapped[str] = mapped_column(String(32), default="generated", nullable=False)
    triggered_by: Mapped[Optional[str]] = mapped_column(String(100))

    generated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    triggered_at_utc: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class StorySetting(Base):
    """Per-tournament default template + animation style per story type.

    ``tournament_id`` NULL = global default used when a tournament has no
    override. Upserts are delete-then-insert in the service so the nullable
    key never produces duplicate rows.
    """

    __tablename__ = "story_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tournament_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("tournaments.id"), nullable=True)
    story_type: Mapped[str] = mapped_column(String(64), nullable=False)
    template: Mapped[str] = mapped_column(String(128), nullable=False)
    animation_style: Mapped[str] = mapped_column(String(128), nullable=False)

    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
