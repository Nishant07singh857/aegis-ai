from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from .enums import UserRole


class User(Base):
    """System users - admins, organizers, casters, analysts"""

    # Table name comes from Base's auto-tablename: User -> "user".
    # NOTE: the legacy monolith had a `tournaments` relationship here, but the
    # package Tournament model has no `created_by` column, so it is dropped.

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500))

    # Phase 16 tournament/team scoping: a user is bound to at most one
    # tournament (null = global account) and, for observers, exactly one team.
    tournament_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("tournaments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    team_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Username of the admin who created this account (null for legacy rows).
    created_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Account status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Phase 6 role-based access control. Plain String(20) (not a native PG
    # enum) so the column stays portable across postgres/sqlite; the allowed
    # values are enforced by UserRole on the Python side and by the
    # 005_add_user_role migration's server default for raw SQL inserts.
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default=UserRole.VIEWER.value
    )

    # Security
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_login_ip: Mapped[Optional[str]] = mapped_column(String(45))
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Profile
    bio: Mapped[Optional[str]] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")
    language: Mapped[str] = mapped_column(String(10), default="en")

    # Relationships
    api_keys = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_user_email_active", "email", "is_active"),
        Index("ix_user_username_active", "username", "is_active"),
    )
