"""AEGIS AI — POI database package (Phase 24, Worker W4).

Positions become names: ``POIDatabase.nearest("erangel", x, y)``
answers "Pochinki" instead of "(0.44, 0.53)". See
``poi_database.py`` for the full contract and honesty rules, and
``docs/SIGNAL_REGISTRY.md`` for how POI naming plugs into the
signal pipeline.
"""

from .poi_database import (
    DATA_DIR,
    POI_TYPES,
    VALID_TIERS,
    POIDatabase,
    get_poi_database,
)

__all__ = [
    "DATA_DIR",
    "POI_TYPES",
    "VALID_TIERS",
    "POIDatabase",
    "get_poi_database",
]
