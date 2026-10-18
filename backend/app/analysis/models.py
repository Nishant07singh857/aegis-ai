"""AEGIS AI — analysis ORM models (Phase 11).

New tables: ``drop_events`` and ``zone_phases`` (created by alembic
migration 006). Rotation events reuse the existing
``app.db.models.rotation_event.RotationEvent`` model — it is NOT
redefined here.
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DropEvent(Base):
    """One team's landing at match start.

    ``contested_by`` is a JSONB list of team_ids that landed at the same
    named location. ``survived_drop`` is True/False when the outcome is
    known, None when the feed never reported it (survival rate is then
    computed over known outcomes only — never guessed).
    """

    __tablename__ = "drop_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    match_id: Mapped[str] = mapped_column(String(36), ForeignKey("matches.id"), nullable=False)
    team_id: Mapped[str] = mapped_column(String(36), nullable=False)

    map_name: Mapped[Optional[str]] = mapped_column(String(32))
    location_x: Mapped[Optional[float]] = mapped_column(Float)
    location_y: Mapped[Optional[float]] = mapped_column(Float)
    named_location: Mapped[Optional[str]] = mapped_column(String(128))
    timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    contested_by: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    survived_drop: Mapped[Optional[bool]] = mapped_column(Boolean)

    # ingest provenance and any extra feed fields, e.g. {"source": "simulator"}
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class ZonePhase(Base):
    """One zone phase of a match: the circle the zone shrank to and when."""

    __tablename__ = "zone_phases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    match_id: Mapped[str] = mapped_column(String(36), ForeignKey("matches.id"), nullable=False)
    phase_number: Mapped[int] = mapped_column(Integer, nullable=False)

    center_x: Mapped[float] = mapped_column(Float, nullable=False)
    center_y: Mapped[float] = mapped_column(Float, nullable=False)
    radius: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    # ingest provenance, e.g. {"source": "feed"}
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
