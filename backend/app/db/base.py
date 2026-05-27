# backend/app/db/base.py
"""
AEGIS AI - Database Foundation
Async SQLAlchemy engine, session management, base model
Production-grade with connection pooling, retry logic, health checks
"""

import asyncio
import time
import logging
from typing import AsyncGenerator, Optional, Any, Dict
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
    AsyncEngine,
)
from sqlalchemy.orm import DeclarativeBase, declared_attr
from sqlalchemy import (
    Column, DateTime, Boolean, func, text, MetaData,
)
from sqlalchemy.exc import SQLAlchemyError, OperationalError, DisconnectionError
from sqlalchemy import event as sa_event

from ..config import settings

logger = logging.getLogger("aegis.db")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NAMING CONVENTIONS FOR CONSTRAINTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """
    Base model class with common columns and utilities.
    All database models inherit from this.
    """
    
    metadata = metadata
    
    @declared_attr
    def __tablename__(cls) -> str:
        """Auto-generate table name from class name"""
        import re
        name = cls.__name__
        # Convert CamelCase to snake_case
        name = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
        name = re.sub('([a-z0-9])([A-Z])', r'\1_\2', name).lower()
        return name
    
    # Common columns for ALL models
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="Record creation timestamp"
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="Record last update timestamp"
    )
    is_deleted = Column(
        Boolean,
        default=False,
        nullable=False,
        comment="Soft delete flag"
    )
    deleted_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Soft delete timestamp"
    )
    
    def to_dict(self, exclude: Optional[set] = None) -> Dict[str, Any]:
        """Convert model instance to dictionary"""
        exclude = exclude or {"_sa_instance_state"}
        result = {}
        for column in self.__table__.columns:
            if column.name not in exclude:
                value = getattr(self, column.name)
                # Handle datetime serialization
                if hasattr(value, 'isoformat'):
                    value = value.isoformat()
                result[column.name] = value
        return result
    
    def update(self, **kwargs) -> None:
        """Update model attributes"""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
    
    def soft_delete(self) -> None:
        """Soft delete the record"""
        self.is_deleted = True
        self.deleted_at = func.now()
    
    def __repr__(self) -> str:
        """String representation"""
        attrs = []
        for col in self.__table__.columns:
            if col.primary_key:
                attrs.append(f"{col.name}={getattr(self, col.name)}")
        return f"<{self.__class__.__name__}({', '.join(attrs)})>"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ASYNC ENGINE FACTORY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def create_engine() -> AsyncEngine:
    """
    Create async SQLAlchemy engine with production-optimized settings.
    """
    
    # Base engine configuration
    engine_kwargs = {
        "echo": settings.DATABASE_ECHO,
        "echo_pool": settings.DEBUG,
        "pool_size": settings.DATABASE_POOL_SIZE,
        "max_overflow": settings.DATABASE_MAX_OVERFLOW,
        "pool_recycle": settings.DATABASE_POOL_RECYCLE,
        "pool_timeout": settings.DATABASE_POOL_TIMEOUT,
        "pool_pre_ping": True,  # Verify connections before use
        "connect_args": {
            "command_timeout": 30,
            "server_settings": {
                "application_name": "aegis_ai",
                "timezone": "UTC",
                "statement_timeout": "30000",  # 30 second query timeout
            },
        },
    }
    
    # Environment-specific configurations
    if settings.ENVIRONMENT == "production":
        engine_kwargs.update({
            "pool_size": 50,
            "max_overflow": 25,
            "pool_timeout": 60,
            "pool_recycle": 1800,  # 30 minutes
            "connect_args": {
                **engine_kwargs["connect_args"],
                "ssl": settings.DATABASE_SSL,
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 5,
            },
        })
    elif settings.ENVIRONMENT == "development":
        engine_kwargs.update({
            "echo": settings.DEBUG,
            "pool_size": 10,
            "max_overflow": 5,
        })
    else:  # staging
        engine_kwargs.update({
            "pool_size": 20,
            "max_overflow": 10,
        })
    
    # Create engine
    engine = create_async_engine(
        settings.DATABASE_URL,
        **engine_kwargs
    )
    
    # Register event listeners
    @sa_event.listens_for(engine.sync_engine, "connect")
    def receive_connect(dbapi_connection, connection_record):
        """Set connection parameters on connect"""
        logger.debug("Database connection established")
    
    @sa_event.listens_for(engine.sync_engine, "checkout")
    def receive_checkout(dbapi_connection, connection_record, connection_proxy):
        """Log when connection is checked out from pool"""
        if settings.DEBUG:
            logger.debug(
                f"Connection checked out from pool. "
                f"Pool size: {engine.pool.size()}, "
                f"Checked in: {engine.pool.checkedin()}"
            )
    
    @sa_event.listens_for(engine.sync_engine, "close")
    def receive_close(dbapi_connection, connection_record):
        """Log when connection is returned to pool"""
        if settings.DEBUG:
            logger.debug("Database connection closed")
    
    logger.info(
        f"Database engine created | "
        f"Pool Size: {engine_kwargs['pool_size']} | "
        f"Max Overflow: {engine_kwargs['max_overflow']}"
    )
    
    return engine


# Global engine instance
engine = create_engine()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ASYNC SESSION FACTORY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SESSION MANAGEMENT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency for database sessions.
    
    Usage:
        @app.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error(f"Database session error: {e}", exc_info=True)
            raise
        except Exception as e:
            await session.rollback()
            logger.error(f"Unexpected session error: {e}", exc_info=True)
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager for database sessions.
    
    Usage:
        async with get_db_context() as db:
            result = await db.execute(query)
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"Database context error: {e}", exc_info=True)
            raise
        finally:
            await session.close()


async def get_db_with_retry(
    max_retries: int = 3,
    retry_delay: float = 0.5
) -> AsyncGenerator[AsyncSession, None]:
    """
    Database session with retry logic for transient failures.
    """
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            async with AsyncSessionLocal() as session:
                yield session
                await session.commit()
                return  # Success
        except (OperationalError, DisconnectionError) as e:
            last_exception = e
            logger.warning(
                f"Database connection attempt {attempt + 1}/{max_retries} failed: {e}"
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay * (2 ** attempt))  # Exponential backoff
            await session.rollback()
        except Exception as e:
            last_exception = e
            await session.rollback()
            logger.error(f"Database error (non-retryable): {e}", exc_info=True)
            raise
        finally:
            await session.close()
    
    logger.error(f"All {max_retries} database connection attempts failed")
    raise last_exception


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATABASE INITIALIZATION & HEALTH CHECKS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def init_db() -> None:
    """
    Initialize database - create all tables and extensions.
    """
    logger.info("Initializing database...")
    
    try:
        async with engine.begin() as conn:
            # Enable PostgreSQL extensions
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\""))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS \"pgcrypto\""))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS \"pg_trgm\""))
            
            # Create all tables
            await conn.run_sync(Base.metadata.create_all)
            
        logger.info("✅ Database initialized successfully")
        logger.info(f"Tables created: {len(Base.metadata.tables)}")
        
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}", exc_info=True)
        raise


async def close_db() -> None:
    """
    Close all database connections.
    """
    logger.info("Closing database connections...")
    try:
        await engine.dispose()
        logger.info("✅ Database connections closed")
    except Exception as e:
        logger.error(f"Error closing database: {e}", exc_info=True)


async def check_db_health() -> Dict[str, Any]:
    """
    Check database health and return status.
    """
    try:
        async with engine.connect() as conn:
            start_time = time.time()
            result = await conn.execute(text("SELECT 1"))
            await result.fetchone()
            latency = (time.time() - start_time) * 1000  # ms
            
            # Get pool status
            pool = engine.pool
            pool_status = {
                "size": pool.size() if hasattr(pool, 'size') else "N/A",
                "checked_in": pool.checkedin() if hasattr(pool, 'checkedin') else "N/A",
                "overflow": pool.overflow() if hasattr(pool, 'overflow') else "N/A",
            }
            
            return {
                "status": "healthy",
                "latency_ms": round(latency, 2),
                "pool": pool_status,
                "timestamp": time.time(),
            }
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": time.time(),
        }


async def get_db_stats() -> Dict[str, Any]:
    """
    Get database statistics.
    """
    try:
        async with engine.connect() as conn:
            # Table sizes
            result = await conn.execute(text("""
                SELECT 
                    schemaname,
                    tablename,
                    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size,
                    n_live_tup as row_count
                FROM pg_stat_user_tables
                ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
                LIMIT 20
            """))
            tables = [
                {
                    "schema": row[0],
                    "table": row[1],
                    "size": row[2],
                    "rows": row[3],
                }
                for row in await result.fetchall()
            ]
            
            # Database size
            result = await conn.execute(text(
                "SELECT pg_size_pretty(pg_database_size(current_database()))"
            ))
            db_size = (await result.fetchone())[0]
            
            return {
                "database_size": db_size,
                "tables": tables,
                "timestamp": time.time(),
            }
    except Exception as e:
        logger.error(f"Failed to get DB stats: {e}")
        return {"error": str(e)}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BASE REPOSITORY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from typing import TypeVar, Generic, Type, List, Optional, Union, Dict, Any
from uuid import UUID
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """
    Generic repository with common CRUD operations.
    
    Usage:
        class TournamentRepository(BaseRepository[Tournament]):
            def __init__(self, session: AsyncSession):
                super().__init__(Tournament, session)
    """
    
    def __init__(self, model: Type[ModelType], session: AsyncSession):
        self.model = model
        self.session = session
    
    async def create(self, **kwargs) -> ModelType:
        """Create a new record"""
        instance = self.model(**kwargs)
        self.session.add(instance)
        await self.session.flush()
        await self.session.refresh(instance)
        logger.debug(f"Created {self.model.__name__}: {instance}")
        return instance
    
    async def get(self, id: Union[str, UUID, int]) -> Optional[ModelType]:
        """Get record by ID"""
        query = select(self.model).where(
            and_(
                self.model.id == str(id),
                self.model.is_deleted == False
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
    
    async def get_or_404(self, id: Union[str, UUID, int]) -> ModelType:
        """Get record or raise exception"""
        instance = await self.get(id)
        if not instance:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=404,
                detail=f"{self.model.__name__} not found"
            )
        return instance
    
    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
        order_by: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[ModelType]:
        """Get all records with pagination and filtering"""
        query = select(self.model).where(self.model.is_deleted == False)
        
        if filters:
            for field, value in filters.items():
                if hasattr(self.model, field):
                    query = query.where(getattr(self.model, field) == value)
        
        if order_by:
            if hasattr(self.model, order_by):
                query = query.order_by(getattr(self.model, order_by).desc())
        else:
            query = query.order_by(self.model.created_at.desc())
        
        query = query.offset(skip).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())
    
    async def update(self, id: Union[str, UUID, int], **kwargs) -> Optional[ModelType]:
        """Update a record"""
        instance = await self.get(id)
        if instance:
            for key, value in kwargs.items():
                if hasattr(instance, key):
                    setattr(instance, key, value)
            await self.session.flush()
            await self.session.refresh(instance)
            logger.debug(f"Updated {self.model.__name__} {id}")
        return instance
    
    async def delete(self, id: Union[str, UUID, int], soft: bool = True) -> bool:
        """Delete a record (soft or hard delete)"""
        instance = await self.get(id)
        if instance:
            if soft:
                instance.soft_delete()
            else:
                await self.session.delete(instance)
            await self.session.flush()
            logger.debug(f"Deleted {self.model.__name__} {id} (soft={soft})")
            return True
        return False
    
    async def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        """Count records"""
        query = select(func.count()).select_from(self.model).where(
            self.model.is_deleted == False
        )
        if filters:
            for field, value in filters.items():
                if hasattr(self.model, field):
                    query = query.where(getattr(self.model, field) == value)
        result = await self.session.execute(query)
        return result.scalar()
    
    async def exists(self, id: Union[str, UUID, int]) -> bool:
        """Check if record exists"""
        query = select(self.model.id).where(
            and_(
                self.model.id == str(id),
                self.model.is_deleted == False
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none() is not None
    
    async def bulk_create(self, items: List[Dict[str, Any]]) -> List[ModelType]:
        """Bulk create records"""
        instances = [self.model(**item) for item in items]
        self.session.add_all(instances)
        await self.session.flush()
        logger.debug(f"Bulk created {len(instances)} {self.model.__name__} records")
        return instances
    
    async def bulk_update(self, updates: List[Dict[str, Any]]) -> int:
        """Bulk update records"""
        count = 0
        for update_data in updates:
            id = update_data.pop("id", None)
            if id:
                instance = await self.update(id, **update_data)
                if instance:
                    count += 1
        return count