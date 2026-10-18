"""
AEGIS AI - ORM Models package.

Import modules here so `Base.metadata` is fully populated when the app initializes.
"""

from .enums import (  # noqa: F401
    BroadcastMode,
    EventType,
    FightIntensity,
    HighlightType,
    MatchMap,
    MatchStatus,
    PlayerRole,
    TournamentFormat,
    TournamentStatus,
    UserRole,
)
from .tournament import Tournament  # noqa: F401
from .match import Match  # noqa: F401
from .team import Team  # noqa: F401
from .player import Player  # noqa: F401
from .match_event import MatchEvent  # noqa: F401
from .fight_event import FightEvent  # noqa: F401
from .kill_event import KillEvent  # noqa: F401
from .utility_event import UtilityEvent  # noqa: F401
from .rotation_event import RotationEvent  # noqa: F401
from .highlight import Highlight  # noqa: F401
from .commentary import Commentary  # noqa: F401
from .prediction import Prediction  # noqa: F401
from .team_intelligence import TeamIntelligence  # noqa: F401
from .broadcast_session import BroadcastSession  # noqa: F401
from .user import User  # noqa: F401
from .api_key import ApiKey  # noqa: F401
from .audit_log import AuditLog  # noqa: F401
from .observer_slot import ObserverSlot  # noqa: F401
from .event_store import EventStore  # noqa: F401
