# backend/app/services/observer_mapping/service.py
"""Observer-team slot mapping (Phase 12).

A tournament runs exactly ``MAX_SLOTS`` (16) observer slots. Each slot is
assigned to at most one team and reports its feed status through
heartbeats. All state is real DB rows — nothing is simulated.

Writes assume the database is reachable and surface failures as 503,
matching the convention of the other Phase 2+ services.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.observer_slot import ObserverSlot
from app.db.models.team import Team

logger = logging.getLogger("aegis.observers")

#: Tournament observer slot count.
MAX_SLOTS = 16

#: Valid slot statuses (kept in one place for the API + heartbeat).
VALID_STATUSES = ("idle", "connected", "feeding")


def _db_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Database unavailable: observer slot state could not be read or written.",
    )


class ObserverMappingService:
    """CRUD over the 16 observer slots, keyed by slot_number."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ---------- internals ----------

    async def _ensure_slots(self) -> None:
        """Insert any missing slot rows 1..MAX_SLOTS (idempotent)."""
        try:
            existing = (
                (
                    await self.db.execute(
                        select(ObserverSlot.slot_number).where(
                            ObserverSlot.slot_number.between(1, MAX_SLOTS)
                        )
                    )
                )
                .scalars()
                .all()
            )
        except Exception:
            raise _db_unavailable()
        have = set(existing)
        missing = [n for n in range(1, MAX_SLOTS + 1) if n not in have]
        if not missing:
            return
        try:
            self.db.add_all(
                ObserverSlot(slot_number=n, status="idle") for n in missing
            )
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise _db_unavailable()
        logger.info("seeded %d observer slot rows", len(missing))

    def _check_slot_number(self, slot_number: int) -> None:
        if not 1 <= slot_number <= MAX_SLOTS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Observer slot {slot_number} does not exist "
                f"(slots are 1..{MAX_SLOTS}).",
            )

    async def _get_slot(self, slot_number: int) -> ObserverSlot:
        await self._ensure_slots()
        try:
            slot = (
                await self.db.execute(
                    select(ObserverSlot).where(
                        ObserverSlot.slot_number == slot_number
                    )
                )
            ).scalar_one_or_none()
        except Exception:
            raise _db_unavailable()
        if slot is None:  # pragma: no cover — _ensure_slots guarantees this
            self._check_slot_number(slot_number)
            raise _db_unavailable()
        return slot

    # ---------- reads ----------

    async def list_slots(self) -> List[ObserverSlot]:
        """All 16 slots, ordered by slot_number, seeding missing rows."""
        await self._ensure_slots()
        try:
            rows = (
                (
                    await self.db.execute(
                        select(ObserverSlot)
                        .where(ObserverSlot.slot_number.between(1, MAX_SLOTS))
                        .order_by(ObserverSlot.slot_number)
                    )
                )
                .scalars()
                .all()
            )
        except Exception:
            raise _db_unavailable()
        return list(rows)

    # ---------- writes ----------

    async def _resolve_team(self, team_id: str) -> Team:
        """Fetch the team being assigned, or 404."""
        try:
            team = (
                await self.db.execute(select(Team).where(Team.id == team_id))
            ).scalar_one_or_none()
        except Exception:
            raise _db_unavailable()
        if team is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Team {team_id} not found; slot assignment unchanged.",
            )
        return team

    async def assign_slot(self, slot_number: int, team_id: str) -> ObserverSlot:
        """Assign (or re-assign) a team to a slot. Mid-match safe."""
        self._check_slot_number(slot_number)
        team = await self._resolve_team(team_id)
        slot = await self._get_slot(slot_number)
        try:
            slot.team_id = team.id
            await self.db.commit()
            await self.db.refresh(slot)
        except Exception:
            await self.db.rollback()
            raise _db_unavailable()
        logger.info("observer slot %d assigned to team %s", slot_number, team.name)
        return slot

    async def unassign_slot(self, slot_number: int) -> ObserverSlot:
        """Clear a slot's team assignment."""
        self._check_slot_number(slot_number)
        slot = await self._get_slot(slot_number)
        try:
            slot.team_id = None
            await self.db.commit()
            await self.db.refresh(slot)
        except Exception:
            await self.db.rollback()
            raise _db_unavailable()
        logger.info("observer slot %d unassigned", slot_number)
        return slot

    async def heartbeat(self, slot_number: int, slot_status: str) -> ObserverSlot:
        """Record an observer client's status heartbeat."""
        self._check_slot_number(slot_number)
        if slot_status not in VALID_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid status {slot_status!r}; "
                f"must be one of {', '.join(VALID_STATUSES)}.",
            )
        slot = await self._get_slot(slot_number)
        try:
            slot.status = slot_status
            slot.last_heartbeat = datetime.now(timezone.utc)
            await self.db.commit()
            await self.db.refresh(slot)
        except Exception:
            await self.db.rollback()
            raise _db_unavailable()
        return slot

    async def team_name(self, team_id: Optional[str]) -> Optional[str]:
        """Resolve a team id to its display name (None when unassigned)."""
        if not team_id:
            return None
        try:
            team = (
                await self.db.execute(select(Team).where(Team.id == team_id))
            ).scalar_one_or_none()
        except Exception:
            return None
        return team.name if team else None
