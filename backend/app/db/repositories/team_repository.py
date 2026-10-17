"""Team repository: SQLAlchemy access for the Team model."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import BaseRepository
from app.db.models.team import Team


class TeamRepository(BaseRepository[Team]):
    """CRUD for teams, plus name lookup for uniqueness checks."""

    def __init__(self, session: AsyncSession):
        super().__init__(Team, session)

    async def get_by_name(self, name: str) -> Optional[Team]:
        """Return the non-deleted team with this exact name, if any."""
        result = await self.session.execute(
            select(Team).where(Team.name == name, Team.is_deleted == False)  # noqa: E712
        )
        return result.scalar_one_or_none()
