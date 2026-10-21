# backend/app/services/vision_intelligence/__init__.py
"""Vision -> intelligence loop (Phase 22).

Closes the loop Phase 21 left open: the vision pipeline published
hp_update / utility / position_update / zone_update / healing / loot
events to the bus and nobody consumed them. This package holds the
subscriber service plus its three collaborators:

  * ``attribution``   — observer-slot -> team resolution (never guesses)
  * ``zone_state``    — analytics zone state from zone/position events
  * ``story_flagger`` — narrative auto-flagging with director-mode rules
  * ``anti_cheat``    — the vision-derivable anti-cheat checks
"""

from .anti_cheat import (  # noqa: F401
    DISABLED_CHECKS,
    LIVE_CHECKS,
    VisionAntiCheatAnalyzer,
    checks_registry,
)
from .attribution import (  # noqa: F401
    DbSlotResolver,
    SlotResolver,
    StaticSlotResolver,
    TeamAttribution,
    extract_slot_number,
)
from .derivations import (  # noqa: F401
    AirdropTracker,
    DirectorDerivations,
    FightForecaster,
    KnockSessionTracker,
    RotationTracker,
    ZoneClock,
    compute_team_shape,
)
from .service import (  # noqa: F401
    VISION_EVENT_TYPES,
    VisionIntelligenceService,
    build_vision_intelligence_service,
    get_vision_intelligence_service,
)
from .story_flagger import StoryAutoFlagger  # noqa: F401
from .zone_state import ZoneAnalyticsState  # noqa: F401
