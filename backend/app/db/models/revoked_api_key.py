from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class RevokedApiKey(Base):
    """Durable record of a revoked static ADMIN_API_KEYS entry.

    Phase 36 (N-4): the Phase 35 revocation registry was in-memory
    only, so a process restart resurrected every revoked key. This
    table is the persistent half of revocation — ``app/api/deps.py``
    seeds its in-memory cache from here at startup and writes a row
    on every persistent revoke, so a revoked key stays revoked
    across restarts.

    Digest-only storage: ``key_digest`` is the SHA-256 hex digest of
    the raw key. The raw key value is NEVER stored here (nor logged
    anywhere on the revoke path) — a digest cannot be turned back
    into a working key, and listing this table leaks nothing usable.
    There is deliberately no un-revoke: restoring a key requires
    removing its row (operator action, outside this service) AND a
    restart with the key present in ADMIN_API_KEYS.

    Table name comes from Base's auto-tablename: RevokedApiKey ->
    "revoked_api_key". Schema is mirrored by alembic migration 018
    (inspector-guarded create, portable DDL); keep the two in sync.
    """

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    key_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        Index("ix_revoked_api_key_digest", "key_digest"),
    )
