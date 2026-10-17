from __future__ import annotations

"""
AEGIS AI - Alembic environment.

Schema-change story (see also 001_initial_schema.py docstring):
* ``alembic upgrade head`` is the versioned deploy path for schema changes.
* ``app.db.base.init_db()`` (``Base.metadata.create_all(checkfirst=True)``)
  stays as the idempotent local-dev bootstrap; it also enables the
  ``uuid-ossp``/``pgcrypto``/``pg_trgm`` extensions, which migrations do not
  manage. Both derive from the same ``Base.metadata``, so they agree.
* Online mode always takes the URL from ``settings.DATABASE_URL`` (converted
  to a sync driver URL); the ``sqlalchemy.url`` key in ``alembic.ini`` is
  intentionally absent so a stale hardcoded URL can never win.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Allow `import app.*` by adding `backend/` to sys.path
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db import models  # noqa: F401,E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_database_url(url: str) -> str:
    # Alembic runs synchronously; convert common async SQLAlchemy URLs.
    return (
        url.replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgresql+psycopg://", "postgresql://")
        .replace("sqlite+aiosqlite://", "sqlite://")
    )


def run_migrations_offline() -> None:
    url = _sync_database_url(settings.DATABASE_URL)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _sync_database_url(settings.DATABASE_URL)

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
