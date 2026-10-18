"""AEGIS AI — post-match analysis package (Phase 11).

Rotation / drop / zone tracking. Rotation events reuse the existing
``rotation_events`` table (``app.db.models.rotation_event``); drop and
zone-phase events are new tables introduced in alembic migration 006.

The coordinator mounts ``router`` at ``/api/v1`` in ``app/main.py`` —
route paths below intentionally carry no ``/api/v1`` prefix.
"""

from .models import DropEvent, ZonePhase
from .router import router

__all__ = ["DropEvent", "ZonePhase", "router"]
