"""Observer Auto-Pilot (Phase 29): per-feed POV state + pilot control.

W1 surface: the shared types and the per-feed POV state tracker.
W2: the stateless rules engine (``rules.py``).
W4: the pilot service (mode policy + execution wiring), the durable
POV journal, and the DB-backed roster provider.
W3 (executor/config) is imported lazily by the service builder so
this package stays importable while that module is in flight.
"""

from .pov_journal import POVJournal, get_pov_journal, set_pov_journal
from .roster import CachedRosterProvider
from .service import (
    ObserverPilotService,
    PilotServiceConfig,
    build_observer_pilot,
    get_pilot_service,
    set_pilot_service,
)
from .state import POVStateTracker, RosterProvider, match_name_to_roster
from .types import FeedPOVState, PlayerPOV, POVPlayerStatus

__all__ = [
    "FeedPOVState",
    "PlayerPOV",
    "POVPlayerStatus",
    "POVStateTracker",
    "RosterProvider",
    "match_name_to_roster",
    "CachedRosterProvider",
    "POVJournal",
    "get_pov_journal",
    "set_pov_journal",
    "ObserverPilotService",
    "PilotServiceConfig",
    "build_observer_pilot",
    "get_pilot_service",
    "set_pilot_service",
]
