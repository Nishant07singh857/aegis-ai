"""Tournament repository: SQLAlchemy access for the Tournament model."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import BaseRepository
from app.db.models.tournament import Tournament

STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"


class TournamentRepository(BaseRepository[Tournament]):
    """CRUD for tournaments, plus slug/active lookups."""

    def __init__(self, session: AsyncSession):
        super().__init__(Tournament, session)

    async def get_by_slug(self, slug: str) -> Optional[Tournament]:
        """Return the non-deleted tournament with this exact slug, if any."""
        result = await self.session.execute(
            select(Tournament).where(
                Tournament.slug == slug, Tournament.is_deleted == False  # noqa: E712
            )
        )
        return result.scalar_one_or_none()

    async def get_active(self) -> Optional[Tournament]:
        """Return the currently active tournament (at most one), if any."""
        result = await self.session.execute(
            select(Tournament).where(
                Tournament.status == STATUS_ACTIVE,
                Tournament.is_deleted == False,  # noqa: E712
            )
        )
        return result.scalar_one_or_none()

    async def deactivate_others(self, keep_id: str) -> int:
        """Set every other active tournament to ``inactive``.

        Returns the number of tournaments deactivated. This is what keeps
        the "exactly one active tournament" invariant true on activate.
        """
        result = await self.session.execute(
            update(Tournament)
            .where(
                Tournament.id != keep_id,
                Tournament.status == STATUS_ACTIVE,
                Tournament.is_deleted == False,  # noqa: E712
            )
            .values(status=STATUS_INACTIVE)
        )
        await self.session.flush()
        return result.rowcount or 0

    async def list_all(
        self, skip: int = 0, limit: int = 100, order_by: str = "created_at"
    ) -> List[Tournament]:
        """All non-deleted tournaments, newest first."""
        return await self.get_all(skip=skip, limit=limit, order_by=order_by)
