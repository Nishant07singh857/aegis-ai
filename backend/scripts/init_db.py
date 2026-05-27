# backend/scripts/init_db.py
"""
AEGIS AI - Database Initialization Script
Creates all database tables, indexes, and extensions.
Handles migration execution and initial setup.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog
from sqlalchemy import text

from app.db.base import engine, Base, init_db

logger = structlog.get_logger("aegis.scripts.init_db")


async def create_extensions():
    """Create PostgreSQL extensions"""
    async with engine.begin() as conn:
        extensions = [
            "uuid-ossp",
            "pgcrypto", 
            "pg_trgm",
            "btree_gin",
        ]
        
        for ext in extensions:
            try:
                await conn.execute(text(f"CREATE EXTENSION IF NOT EXISTS \"{ext}\""))
                logger.info(f"Extension created: {ext}")
            except Exception as e:
                logger.warning(f"Extension {ext} not available: {e}")


async def create_tables():
    """Create all database tables"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Count tables
    table_count = len(Base.metadata.tables)
    logger.info(f"Created {table_count} database tables")
    
    # List tables
    for table_name in sorted(Base.metadata.tables.keys()):
        logger.info(f"  - {table_name}")


async def create_indexes():
    """Create additional performance indexes"""
    async with engine.begin() as conn:
        indexes = [
            # Match events indexes
            "CREATE INDEX IF NOT EXISTS ix_match_events_match_time ON match_events(match_id, timestamp_ms)",
            "CREATE INDEX IF NOT EXISTS ix_match_events_type_time ON match_events(event_type, timestamp_ms)",
            
            # Player performance indexes
            "CREATE INDEX IF NOT EXISTS ix_player_stats_kills ON match_player_stats(player_id, kills DESC)",
            "CREATE INDEX IF NOT EXISTS ix_player_stats_damage ON match_player_stats(player_id, damage_dealt DESC)",
            
            # Team performance indexes  
            "CREATE INDEX IF NOT EXISTS ix_team_stats_points ON match_team_stats(team_id, total_points DESC)",
            "CREATE INDEX IF NOT EXISTS ix_team_stats_match ON match_team_stats(match_id, placement)",
            
            # Fight indexes
            "CREATE INDEX IF NOT EXISTS ix_fights_intensity ON fight_events(match_id, intensity_score DESC)",
            "CREATE INDEX IF NOT EXISTS ix_fights_timestamp ON fight_events(match_id, start_timestamp_ms)",
            
            # Tournament indexes
            "CREATE INDEX IF NOT EXISTS ix_tournament_status_date ON tournaments(status, start_date)",
            
            # Highlight indexes
            "CREATE INDEX IF NOT EXISTS ix_highlights_match_type ON highlights(match_id, highlight_type)",
            "CREATE INDEX IF NOT EXISTS ix_highlights_excitement ON highlights(excitement_score DESC)",
        ]
        
        for idx_sql in indexes:
            try:
                await conn.execute(text(idx_sql))
            except Exception as e:
                logger.warning(f"Index creation warning: {e}")
        
        logger.info(f"Created {len(indexes)} performance indexes")


async def verify_connection():
    """Verify database connection"""
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version()"))
            version = (await result.fetchone())[0]
            logger.info(f"Database connected: {version[:50]}...")
            
            result = await conn.execute(text("SELECT current_database()"))
            db_name = (await result.fetchone())[0]
            logger.info(f"Database name: {db_name}")
            
            return True
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return False


async def main():
    """Main initialization function"""
    logger.info("=" * 60)
    logger.info("AEGIS AI - Database Initialization")
    logger.info("=" * 60)
    
    # Verify connection
    if not await verify_connection():
        logger.error("Cannot proceed without database connection")
        sys.exit(1)
    
    try:
        # Step 1: Create extensions
        logger.info("Step 1/4: Creating extensions...")
        await create_extensions()
        
        # Step 2: Create tables
        logger.info("Step 2/4: Creating tables...")
        await create_tables()
        
        # Step 3: Create indexes
        logger.info("Step 3/4: Creating indexes...")
        await create_indexes()
        
        # Step 4: Initialize with base data
        logger.info("Step 4/4: Initializing base data...")
        await init_db()
        
        logger.info("=" * 60)
        logger.info("✅ Database initialization complete!")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        sys.exit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())