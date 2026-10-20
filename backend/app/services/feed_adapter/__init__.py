# backend/app/services/feed_adapter/__init__.py
"""AEGIS AI — Game feed adapter package (Phase 10).

Contract for any game-data source (simulator today, real observer feed
tomorrow): adapters push NORMALIZED events (see schemas.py) into the
real event bus. Nothing here synthesises data — the simulator adapter
wraps the output the existing match simulator already publishes.
"""

from .base import BaseFeedAdapter
from .fight_schema import (
    FightEventCanonical,
    canonical_to_bus_payload,
    fight_event_to_canonical,
)
from .schemas import FeedEventType, NormalizedEvent
from .service import FeedService, feed_service
from .simulator import SimulatorFeedAdapter

__all__ = [
    "BaseFeedAdapter",
    "FeedEventType",
    "NormalizedEvent",
    "FeedService",
    "feed_service",
    "SimulatorFeedAdapter",
    # Phase 22 (W3, M4): the canonical FightEvent contract.
    "FightEventCanonical",
    "fight_event_to_canonical",
    "canonical_to_bus_payload",
]
