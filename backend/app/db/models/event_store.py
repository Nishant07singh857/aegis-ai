# backend/app/db/models/event_store.py
"""AEGIS AI - Event Store model.

Durable log of every event that flows through the central event bus
(``app.core.event_bus``). Unlike ``match_events`` (which requires a
``matches.id`` foreign key), this table deliberately has **no foreign
keys** so event persistence can never fail on referential integrity —
the event bus is failure-safe by design: a persist error is logged and
the event flow continues.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class EventStore(Base):
    """One row per bus event, written by ``EventBus._persist_event``."""

    __tablename__ = "event_store"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Bus-level event identity (unique — dedupes double-persisted retries).
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")

    # Lower value == higher priority (matches EventPriority enum).
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="system")

    match_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    tournament_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    # When the bus processed the event (may differ from event timestamp).
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    # Full event payload. sa.JSON (not JSONB) so the table is portable
    # across Postgres (json) and SQLite (TEXT) — tests run on SQLite.
    data: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    meta: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
