"""
AEGIS AI - Database Session
Async engine + session factory + lifecycle helpers.
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import QueuePool

from ..config import Environment, settings
from .base import Base

logger = logging.getLogger("aegis.db")


def create_engine() -> AsyncEngine:
    engine_kwargs = {
        "echo": settings.DEBUG,
        "pool_size": settings.DATABASE_POOL_SIZE,
        "max_overflow": settings.DATABASE_MAX_OVERFLOW,
        "pool_recycle": settings.DATABASE_POOL_RECYCLE,
        "pool_pre_ping": True,
    }

    if settings.ENVIRONMENT == Environment.PRODUCTION:
        engine_kwargs.update(
            {
                "pool_size": 50,
                "max_overflow": 25,
                "pool_timeout": 30,
            }
        )

    engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)
    logger.info(f"Database engine created: {settings.DATABASE_URL.split('@')[1]}")
    return engine


engine = create_engine()

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    # Ensure ORM models are imported so they register with Base.metadata.
    from .models import (  # noqa: F401
        broadcast_session,
        commentary,
        fight_event,
        highlight,
        kill_event,
        match,
        match_event,
        player,
        prediction,
        rotation_event,
        team,
        team_intelligence,
        tournament,
        utility_event,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created successfully")


async def close_db() -> None:
    await engine.dispose()
    logger.info("Database connections closed")
