# backend/app/services/live_director/__init__.py
"""Live Director package (Phase 25, P0-0).

The ONE unified director decision service. See ``service.py``'s module
docstring for the full merge map (ObserverDirector ported, DirectorAIEngine
demoted to DEMO_MODE-only, DirectorService kept as the only state store),
the verified bus topic names, and the grammar rules it enforces.
"""

from __future__ import annotations

from typing import Optional

from .config import LiveDirectorConfig  # noqa: F401
from .engagement import (  # noqa: F401
    EngagementState,
    EngagementTracker,
    build_engagement_tracker,
)
from .service import (  # noqa: F401
    LIVE_DIRECTOR_EVENT_TYPES,
    REPLAY_CANDIDATE_TOPIC,
    LiveDirector,
    min_fight_hold_s,
)
from .storyline import compute_storyline  # noqa: F401

_service: Optional[LiveDirector] = None


def get_live_director() -> Optional[LiveDirector]:
    """The process-wide live director, or None when not wired (honest)."""
    return _service


def set_live_director(service: Optional[LiveDirector]) -> None:
    global _service
    _service = service


def build_live_director(bus, director_service=None, pov_state_provider=None) -> LiveDirector:
    """Production wiring: DB slot resolver + PhaseTracker phase provider.

    Slot attribution goes through the same ``observer_slots`` DB mapping
    VisionIntelligenceService uses (5 s TTL cache); an unmapped slot/team
    resolves to None and the director drops the event — never guesses.
    """
    from app.db.session import AsyncSessionLocal
    from app.services.director_service import (
        director_service as _default_director_service,
    )
    from app.services.phase_tracker import phase_tracker
    from app.services.vision_intelligence.attribution import DbSlotResolver

    service = LiveDirector(
        bus=bus,
        director_service=director_service or _default_director_service,
        slot_resolver=DbSlotResolver(AsyncSessionLocal),
        phase_provider=phase_tracker.current_phase,
        pov_state_provider=pov_state_provider,
    )
    set_live_director(service)
    return service
