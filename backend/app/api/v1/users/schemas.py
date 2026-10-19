"""Users API schemas (Phase 16: tournament-scoped user management).

Roles are validated against ``app.db.models.enums.UserRole`` — never a
hardcoded list. ``slot_number`` is resolved from the observer_slots table
(team_id → slot_number) by the service, never hardcoded.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.db.models.enums import UserRole

ROLE_VALUES = tuple(r.value for r in UserRole)


class UserCreate(BaseModel):
    """Admin creates an account.

    ``password`` is optional: when omitted and the role is anything but
    ``admin``, the service generates a secure random password and returns it
    ONCE in the create response (it is never stored in plaintext).
    ``admin`` accounts always require an explicit password.
    """

    username: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_.\-]+$")
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)
    role: str = Field(description="One of: " + ", ".join(ROLE_VALUES))
    tournament_id: Optional[str] = Field(default=None, max_length=36)
    team_id: Optional[str] = Field(default=None, max_length=36)
    email: Optional[str] = Field(default=None, max_length=255)
    full_name: Optional[str] = Field(default=None, max_length=200)

    @field_validator("role")
    @classmethod
    def _normalise_role(cls, value: str) -> str:
        normalised = (value or "").strip().lower()
        if normalised not in ROLE_VALUES:
            raise ValueError(f"Unknown role {value!r}: must be one of {', '.join(ROLE_VALUES)}.")
        return normalised


class UserPatch(BaseModel):
    """Admin updates an account: password reset and/or (de)activation."""

    password: Optional[str] = Field(default=None, min_length=8, max_length=128)
    is_active: Optional[bool] = None


class UserResponse(BaseModel):
    """One user. Never carries any password material."""

    id: str
    username: str
    email: str
    role: str
    tournament_id: Optional[str] = None
    team_id: Optional[str] = None
    slot_number: Optional[int] = None
    is_active: bool
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None


class UserCreateResponse(UserResponse):
    """Create response. ``generated_password`` is present ONLY when the
    service generated the password — this is the single moment it is ever
    visible. It is never stored in plaintext."""

    generated_password: Optional[str] = None


class UserListResponse(BaseModel):
    data: List[UserResponse]
    total: int


class BulkObserversRequest(BaseModel):
    """Generate N observer accounts for a tournament and assign them to
    active teams round-robin, wiring the matching observer_slots."""

    tournament_id: str = Field(max_length=36)
    count: int = Field(default=16, ge=1, le=16)


class ObserverCredential(BaseModel):
    """One row of the credential sheet. The plaintext password appears ONLY
    in the bulk-create response — never in storage, never again via the API."""

    username: str
    password: str
    team_id: str
    team_name: str
    slot_number: int


class BulkObserversResponse(BaseModel):
    data: List[ObserverCredential]
    count: int


class DeactivateTournamentResponse(BaseModel):
    tournament_id: str
    deactivated: int
