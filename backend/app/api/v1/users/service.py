"""User service: tournament-scoped accounts (Phase 16).

Security rules (enforced here, not just in the router):
  * Passwords are bcrypt-hashed via ``security_manager`` before storage.
    Plaintext passwords exist only in the create/bulk-create return values
    and are never persisted, logged or returned again.
  * ``observer`` accounts REQUIRE a real ``team_id``.
  * ``admin`` accounts REQUIRE an explicitly supplied password (no
    auto-generation — an admin must always be deliberately credentialed).
  * Usernames are unique case-insensitively.
  * Roles come from ``UserRole`` (via ``VALID_ROLES``) — never hardcoded.

Writes only touch the database; the surrounding ``get_db`` dependency
commits on success (same convention as the teams/players services).
"""

from __future__ import annotations

import logging
import secrets
from typing import Dict, List, Optional, Sequence, Tuple

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import VALID_ROLES, security_manager
from app.db.models.enums import UserRole
from app.db.models.observer_slot import ObserverSlot
from app.db.models.team import Team
from app.db.models.tournament import Tournament
from app.db.models.user import User
from .schemas import (
    BulkObserversResponse,
    ObserverCredential,
    UserCreateResponse,
    UserResponse,
)

logger = logging.getLogger("aegis.users")

#: Synthetic email domain for accounts created without an explicit email.
#: The User table requires a unique non-null email; observer/spotter/director
#: accounts are operational (username login) so the service derives a
#: deterministic, clearly non-routable address from the username.
SYNTHETIC_EMAIL_DOMAIN = "aegis.local"


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── lookups ──────────────────────────────────────────────────────

    async def _get_team(self, team_id: str) -> Optional[Team]:
        return (await self.db.execute(select(Team).where(Team.id == team_id))).scalars().first()

    async def _get_tournament(self, tournament_id: str) -> Optional[Tournament]:
        return (
            await self.db.execute(select(Tournament).where(Tournament.id == tournament_id))
        ).scalars().first()

    async def get_user(self, user_id: str) -> Optional[User]:
        return (await self.db.execute(select(User).where(User.id == user_id))).scalars().first()

    async def find_by_identity(self, ident: str) -> Optional[User]:
        """Find a user by username or email, case-insensitively."""
        ident = (ident or "").strip().lower()
        if not ident:
            return None
        stmt = select(User).where(
            or_(func.lower(User.username) == ident, func.lower(User.email) == ident)
        ).limit(1)
        return (await self.db.execute(stmt)).scalars().first()

    async def username_taken(self, username: str) -> bool:
        stmt = select(User.id).where(func.lower(User.username) == username.strip().lower()).limit(1)
        return (await self.db.execute(stmt)).scalars().first() is not None

    async def authenticate(self, username: str, password: str) -> Optional[User]:
        """Credential check for username/email + password.

        Returns the User on success, None otherwise — including when the
        account is deactivated (the login route maps that case to its
        explicit 401 "account deactivated" response before calling this).
        No lockout side effects here; those live in main.py's login flow.
        """
        user = await self.find_by_identity(username)
        if user is None or not user.is_active or not password:
            return None
        if not security_manager.verify_password(password, user.hashed_password):
            return None
        return user

    async def _slot_numbers(self, team_ids: Sequence[Optional[str]]) -> Dict[str, int]:
        """Map team_id → observer slot_number (Phase 12 slots), batch query."""
        ids = [t for t in team_ids if t]
        if not ids:
            return {}
        rows = (
            await self.db.execute(
                select(ObserverSlot.team_id, ObserverSlot.slot_number).where(
                    ObserverSlot.team_id.in_(ids)
                )
            )
        ).all()
        # A team can hold several slots (round-robin reuse); report the
        # lowest slot number as the canonical one.
        slot_map: Dict[str, int] = {}
        for team_id, slot_number in rows:
            if team_id and (team_id not in slot_map or slot_number < slot_map[team_id]):
                slot_map[team_id] = slot_number
        return slot_map

    # ── response building ────────────────────────────────────────────

    async def user_response(self, user: User, slot_map: Optional[Dict[str, int]] = None) -> UserResponse:
        if slot_map is None:
            slot_map = await self._slot_numbers([user.team_id])
        return UserResponse(
            id=user.id,
            username=user.username,
            email=user.email,
            role=user.role,
            tournament_id=user.tournament_id,
            team_id=user.team_id,
            slot_number=slot_map.get(user.team_id) if user.team_id else None,
            is_active=user.is_active,
            created_by=user.created_by,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )

    # ── create ───────────────────────────────────────────────────────

    def _build_user_row(
        self,
        *,
        username: str,
        password_hash: str,
        role: str,
        tournament_id: Optional[str],
        team_id: Optional[str],
        email: Optional[str],
        full_name: Optional[str],
        created_by: Optional[str],
    ) -> User:
        username = username.strip()
        return User(
            username=username,
            hashed_password=password_hash,
            email=email.strip() if email else f"{username.lower()}@{SYNTHETIC_EMAIL_DOMAIN}",
            full_name=(full_name.strip() if full_name else username),
            role=role,
            tournament_id=tournament_id,
            team_id=team_id,
            created_by=created_by,
            is_active=True,
            is_verified=False,
            is_superuser=False,
        )

    async def create_user(
        self,
        *,
        username: str,
        password: Optional[str],
        role: str,
        tournament_id: Optional[str] = None,
        team_id: Optional[str] = None,
        email: Optional[str] = None,
        full_name: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> Tuple[UserCreateResponse, Optional[str]]:
        """Create one user.

        Returns (response, plaintext_password_or_None). The plaintext is the
        caller-supplied password echoed back for convenience ONLY when it was
        generated by the service; it is never stored. ``generated_password``
        in the response is set exactly in that case.
        """
        role = (role or "").strip().lower()
        if role not in VALID_ROLES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown role {role!r}: must be one of {', '.join(VALID_ROLES)}.",
            )
        username = (username or "").strip()
        if not username:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="username is required")
        if await self.username_taken(username):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"username {username!r} is already taken",
            )
        if email and await self.find_by_identity(email.strip()):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"email {email.strip()!r} is already taken",
            )
        if tournament_id and await self._get_tournament(tournament_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tournament not found")
        if team_id and await self._get_team(team_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="team not found")
        if role == UserRole.OBSERVER.value and not team_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="observer accounts require a team_id",
            )

        generated: Optional[str] = None
        if password:
            plaintext = password
        elif role == UserRole.ADMIN.value:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="admin accounts require an explicit password (never auto-generated)",
            )
        else:
            generated = secrets.token_urlsafe(16)
            plaintext = generated

        user = self._build_user_row(
            username=username,
            password_hash=security_manager.hash_password(plaintext),
            role=role,
            tournament_id=tournament_id,
            team_id=team_id,
            email=email,
            full_name=full_name,
            created_by=created_by,
        )
        self.db.add(user)
        await self.db.flush()  # surface integrity errors inside the request

        response = await self.user_response(user)
        return (
            UserCreateResponse(**response.model_dump(), generated_password=generated),
            generated,
        )

    # ── list / patch / delete ────────────────────────────────────────

    async def list_users(
        self,
        *,
        role: Optional[str] = None,
        tournament_id: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> Tuple[List[User], int]:
        stmt = select(User)
        if role:
            role = role.strip().lower()
            if role not in VALID_ROLES:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Unknown role {role!r}: must be one of {', '.join(VALID_ROLES)}.",
                )
            stmt = stmt.where(User.role == role)
        if tournament_id:
            stmt = stmt.where(User.tournament_id == tournament_id)
        if is_active is not None:
            stmt = stmt.where(User.is_active == is_active)
        stmt = stmt.order_by(User.username)
        users = (await self.db.execute(stmt)).scalars().all()
        return list(users), len(users)

    async def list_user_responses(
        self,
        *,
        role: Optional[str] = None,
        tournament_id: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> Tuple[List[UserResponse], int]:
        users, total = await self.list_users(role=role, tournament_id=tournament_id, is_active=is_active)
        slot_map = await self._slot_numbers([u.team_id for u in users])
        return [await self.user_response(u, slot_map) for u in users], total

    async def patch_user(
        self,
        user_id: str,
        *,
        password: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> User:
        user = await self.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
        if password is not None:
            user.hashed_password = security_manager.hash_password(password)
            user.failed_login_attempts = 0
            user.locked_until = None
        if is_active is not None:
            user.is_active = is_active
        await self.db.flush()
        return user

    async def delete_user(self, user_id: str) -> None:
        """Hard-delete a user.

        Refused with 409 when the account has ever logged in
        (``last_login_at`` set) — deactivation is the safe path for those,
        because audit trails and created_by references may point at them.
        """
        user = await self.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
        if user.last_login_at is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="user has logged in before: deactivate instead of deleting",
            )
        await self.db.delete(user)
        await self.db.flush()

    # ── bulk observers ───────────────────────────────────────────────

    async def _ensure_slots(self, count: int) -> List[ObserverSlot]:
        slots: List[ObserverSlot] = []
        for n in range(1, count + 1):
            slot = (
                await self.db.execute(select(ObserverSlot).where(ObserverSlot.slot_number == n))
            ).scalars().first()
            if slot is None:
                slot = ObserverSlot(slot_number=n, status="idle")
                self.db.add(slot)
            slots.append(slot)
        await self.db.flush()
        return slots

    async def _unique_username(self, base: str) -> str:
        candidate, suffix = base, 2
        while await self.username_taken(candidate):
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    async def bulk_create_observers(
        self,
        *,
        tournament_id: str,
        count: int = 16,
        created_by: Optional[str] = None,
    ) -> BulkObserversResponse:
        """Create ``count`` observer accounts for a tournament.

        Usernames are ``obs01``..``obsNN`` (suffixed on collision). ACTIVE
        teams are assigned round-robin in name order, and the matching
        observer_slots (slot_number = index+1) are pointed at the same teams.
        Returns the credential sheet — the ONLY place plaintext passwords
        for these accounts ever appear.
        """
        if await self._get_tournament(tournament_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tournament not found")
        teams = (
            await self.db.execute(
                select(Team).where(Team.is_active == True).order_by(Team.name)  # noqa: E712
            )
        ).scalars().all()
        if not teams:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="no active teams to assign observers to",
            )
        slots = await self._ensure_slots(count)

        credentials: List[ObserverCredential] = []
        for i in range(count):
            team = teams[i % len(teams)]
            username = await self._unique_username(f"obs{i + 1:02d}")
            plaintext = secrets.token_urlsafe(16)
            user = self._build_user_row(
                username=username,
                password_hash=security_manager.hash_password(plaintext),
                role=UserRole.OBSERVER.value,
                tournament_id=tournament_id,
                team_id=team.id,
                email=None,
                full_name=username,
                created_by=created_by,
            )
            self.db.add(user)
            slots[i].team_id = team.id
            credentials.append(
                ObserverCredential(
                    username=username,
                    password=plaintext,
                    team_id=team.id,
                    team_name=team.name,
                    slot_number=slots[i].slot_number,
                )
            )
        await self.db.flush()
        logger.info("bulk observers created: tournament_id=%s count=%d", tournament_id, len(credentials))
        return BulkObserversResponse(data=credentials, count=len(credentials))

    # ── kill switch ──────────────────────────────────────────────────

    async def deactivate_tournament(self, tournament_id: str) -> int:
        """Deactivate EVERY user bound to a tournament. Returns the count.

        This is the kill switch: observer/spotter/director/operator accounts
        for a finished (or compromised) tournament are shut off in one call.
        Global accounts (tournament_id NULL) are never touched.
        """
        if await self._get_tournament(tournament_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tournament not found")
        result = await self.db.execute(
            update(User)
            .where(User.tournament_id == tournament_id, User.is_active == True)  # noqa: E712
            .values(is_active=False)
        )
        await self.db.flush()
        count = result.rowcount or 0
        logger.info("tournament users deactivated: tournament_id=%s count=%d", tournament_id, count)
        return count
