# backend/app/core/event_names.py
"""
Canonical event names + published aliases (Phase 22 / W3, M12).

The PRD names bus topics in dotted form (``kill.occurred``). The code
grew snake_case names (``kill_event``). Phase 22 publishes ONE canonical
name per concept and keeps every legacy name as a *published alias*:
the EventBus resolves aliases at dispatch time, so publishing under
either name reaches subscribers of both, and no existing subscriber
breaks.

Canonical → legacy aliases:

    fight.detected      → fight_detected, fight_event
    kill.occurred       → kill_event
    knock.occurred      → knock_event
    zone.updated        → zone_event, zone.tick, zone_change
    team.eliminated     → team_wipe_event, team_eliminated, TEAM_WIPE
    feed.switch         → director.decision, broadcast_command, director.state
    commentary.trigger  → commentary.line, hype.spike
    highlight.clip      → highlight.clip_ready, highlight_moment
    leaderboard.update  → standings            (NEW emitter, see below)
    insight.generated   → (new; no legacy name existed)

Emitters: :func:`emit_leaderboard_update` and
:func:`emit_insight_generated` are the single producers of the two
topics that did not exist before Phase 22.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

#: canonical name -> set of legacy alias names (published, stable).
CANONICAL_EVENT_ALIASES: Dict[str, Set[str]] = {
    "fight.detected": {"fight_detected", "fight_event"},
    "kill.occurred": {"kill_event"},
    "knock.occurred": {"knock_event"},
    "zone.updated": {"zone_event", "zone.tick", "zone_change"},
    "team.eliminated": {"team_wipe_event", "team_eliminated", "TEAM_WIPE"},
    "feed.switch": {"director.decision", "broadcast_command", "director.state"},
    "commentary.trigger": {"commentary.line", "hype.spike"},
    "highlight.clip": {"highlight.clip_ready", "highlight_moment"},
    "leaderboard.update": {"standings"},
    "insight.generated": set(),
}

_ALIAS_TO_CANONICAL: Dict[str, str] = {
    alias: canonical
    for canonical, aliases in CANONICAL_EVENT_ALIASES.items()
    for alias in aliases
}


def canonical_name(event_type: str) -> str:
    """Canonical name for any published or legacy event type."""
    if event_type in CANONICAL_EVENT_ALIASES:
        return event_type
    return _ALIAS_TO_CANONICAL.get(event_type, event_type)


def equivalent_names(event_type: str) -> Set[str]:
    """All names (canonical + aliases) equivalent to ``event_type``."""
    canonical = canonical_name(event_type)
    names = {canonical} | set(CANONICAL_EVENT_ALIASES.get(canonical, set()))
    names.add(event_type)
    return names


async def emit_leaderboard_update(
    bus: Any,
    *,
    rankings: List[Dict[str, Any]],
    match_id: Optional[str] = None,
    source: str = "points_calculator",
) -> Optional[str]:
    """Publish ``leaderboard.update`` (standings changed).

    ``rankings`` is the ranked table in display order; each row should
    carry at least team_id/team_name/total points. Returns the bus
    event id, or None when the bus is unavailable (never raises into
    the scoring path — standings themselves are already persisted /
    pushed over the overlay channels).
    """
    if bus is None:
        return None
    try:
        from .event_bus import EventPriority

        return await bus.publish(
            event_type="leaderboard.update",
            data={"rankings": rankings, "match_id": match_id},
            priority=EventPriority.MEDIUM,
            match_id=match_id,
            source=source,
        )
    except Exception:  # pragma: no cover - bus down must not break scoring
        return None


async def emit_insight_generated(
    bus: Any,
    *,
    insight: Dict[str, Any],
    match_id: Optional[str] = None,
    source: str = "report_generator",
) -> Optional[str]:
    """Publish ``insight.generated`` for one generated insight.

    ``insight`` carries at least ``text``; richer producers may add
    ``kind``/``team_id``/``confidence``. Never raises (see above).
    """
    if bus is None:
        return None
    try:
        from .event_bus import EventPriority

        return await bus.publish(
            event_type="insight.generated",
            data={"insight": insight, "match_id": match_id},
            priority=EventPriority.LOW,
            match_id=match_id,
            source=source,
        )
    except Exception:  # pragma: no cover - defensive
        return None


def alias_subscriber_types(event_types: Iterable[str]) -> Set[str]:
    """Expand a subscription list with equivalents (helper for callers)."""
    out: Set[str] = set()
    for t in event_types:
        out |= equivalent_names(t)
    return out
