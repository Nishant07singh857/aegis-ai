# backend/app/db/session.py
"""
AEGIS AI - Database Session (re-export shim).

The canonical engine/session implementation lives in :mod:`app.db.base`.
This module exists only to preserve the historical import surface
(``from app.db.session import init_db`` in ``app/main.py``) and re-exports
the single shared engine and session factory from ``app.db.base``.

Do NOT create a second engine here: there is exactly one connection pool,
owned by ``app.db.base``. The assertion below enforces that at import time.
"""

from __future__ import annotations

from . import base as _base
from .base import (
    AsyncSessionLocal,
    close_db,
    create_engine,
    engine,
    get_db,
    init_db,
)

# Single-pool invariant: session.* must be the very same objects as base.*.
assert _base.engine is engine, "app.db.session.engine must be app.db.base.engine"
assert _base.AsyncSessionLocal is AsyncSessionLocal
assert _base.create_engine is create_engine
assert _base.get_db is get_db
assert _base.init_db is init_db
assert _base.close_db is close_db

__all__ = [
    "create_engine",
    "engine",
    "AsyncSessionLocal",
    "get_db",
    "init_db",
    "close_db",
]
