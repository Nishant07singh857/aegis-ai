# backend/app/services/grenade_trajectory/__init__.py
"""Grenade trajectory estimation (Phase 23, W4).

Endpoint-based ESTIMATED thrower->explosion arcs for the analyst map.
Not projectile tracking: see docs/GRENADE_TRAJECTORY.md for why a
spectator feed cannot measure a grenade mid-flight, and for the exact
labelling contract (``estimated: true`` on every record, no record
without both endpoints, no invented positions).
"""

from .engine import (
    CONFIDENCE_BASIS,
    METHOD,
    estimate_arc,
)
from .service import (
    GrenadeTrajectoryService,
    PositionBook,
    build_grenade_trajectory_service,
    get_grenade_trajectory_service,
    is_grenade_utility,
    is_grenade_weapon,
)
from .store import TrajectoryStore, trajectory_store

__all__ = [
    "CONFIDENCE_BASIS",
    "METHOD",
    "GrenadeTrajectoryService",
    "PositionBook",
    "TrajectoryStore",
    "build_grenade_trajectory_service",
    "estimate_arc",
    "get_grenade_trajectory_service",
    "is_grenade_utility",
    "is_grenade_weapon",
    "trajectory_store",
]
