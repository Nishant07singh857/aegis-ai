from __future__ import annotations

from typing import Generic, Optional, Sequence, Type, TypeVar

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    def __init__(self, session: AsyncSession, model: Type[ModelT]):
        self.session = session
        self.model = model

    async def get(self, id: str) -> Optional[ModelT]:
        result = await self.session.execute(select(self.model).where(self.model.id == id))
        return result.scalar_one_or_none()

    async def list(self, *, limit: int = 100, offset: int = 0) -> Sequence[ModelT]:
        result = await self.session.execute(select(self.model).limit(limit).offset(offset))
        return result.scalars().all()

    async def add(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def remove(self, obj: ModelT) -> None:
        await self.session.delete(obj)

    async def delete_by_id(self, id: str) -> int:
        result = await self.session.execute(delete(self.model).where(self.model.id == id))
        return int(result.rowcount or 0)
