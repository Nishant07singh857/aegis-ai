# backend/app/services/replay/highlight_triggers.py
"""
Plug-ready highlight triggers (Phase 22 / W3, M11; weapon data Phase 23).

Three PRD highlight kinds depend on kill-event attribution:

* grenade multi-kill — needs a grenade weapon on >=2 kills by one
  player in a window. Phase 23 (W2): the kill-feed icon matcher now
  produces the class-level weapon ``"Frag Grenade"`` from real icon
  template matching, so this trigger fires on real feed data (the
  legacy ``"grenade"`` spelling is also accepted).
* sniper triple — needs a sniper/DMR weapon class on 3 kills. The
  Phase 23 matcher emits class-level ``"Sniper"`` / ``"DMR"``, both
  in ``SNIPER_CLASSES`` below, so this trigger is live on real data.
* vehicle kill — needs a kill↔vehicle linkage; the vehicle detector
  needs the trained model. Still inactive: spectator kill-feed glyphs
  for vehicle kills were never confirmed in the W1 harvest (the
  side-view vehicle family has no library template), so no producer
  emits a truthful vehicle weapon today.

This module registers all three triggers behind ONE interface:
activation is data-driven (``trigger_registry(events)`` activates a
trigger only when the event set actually carries the attribution it
needs) — never a config lie. The registry is the published contract:
it tells the UI/docs exactly which triggers are live and why the
others are not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .buffer import BufferedEvent

SNIPER_CLASSES = {"sniper", "sr", "dmr", "kar98k", "m24", "awm", "mini14", "sks", "mk14"}

#: Grenade weapon spellings accepted by the grenade trigger: the
#: Phase 23 kill-feed class string ("Frag Grenade") plus the legacy /
#: simulator spelling ("grenade"). Compared after normalisation
#: (lowercase, underscores -> spaces).
GRENADE_WEAPONS = {"grenade", "frag grenade", "frag"}


def _is_grenade(weapon: str) -> bool:
    return weapon.strip().lower().replace("_", " ") in GRENADE_WEAPONS


@dataclass
class HighlightTrigger:
    """One registered highlight trigger."""

    name: str
    active: bool
    blocker: Optional[str]
    detector: Optional[Callable[[List[BufferedEvent]], List[Any]]] = None

    def run(self, events: List[BufferedEvent]) -> List[Any]:
        if not self.active or self.detector is None:
            return []
        return self.detector(events)


def _weapon_of(event: BufferedEvent) -> str:
    return str(event.payload.get("weapon") or "").strip().lower()


def _grenade_multi_kill(events: List[BufferedEvent]) -> List[Any]:
    """Active-path detector (only runs when weapon data is truthful)."""
    from .highlights import ScoredMoment

    kills = sorted(
        (e for e in events if e.type == "kill_event" and _is_grenade(_weapon_of(e))),
        key=lambda e: e.ts,
    )
    moments: List[Any] = []
    by_killer: Dict[str, List[BufferedEvent]] = {}
    for k in kills:
        by_killer.setdefault(k.payload.get("killer") or "Unknown", []).append(k)
    for killer, ks in by_killer.items():
        for i, anchor in enumerate(ks):
            window = [k for k in ks[i:] if 0.0 <= k.ts - anchor.ts <= 10.0]
            if len(window) >= 2:
                moments.append(
                    ScoredMoment(
                        kind="grenade_multi_kill",
                        score=85,
                        reason=f"Grenade multi-kill by {killer} — {len(window)} grenade eliminations in {window[-1].ts - window[0].ts:.1f}s",
                        player=killer,
                        team=window[0].payload.get("killerTeam"),
                        opponents=[k.payload.get("victim") or "Unknown" for k in window],
                        window_start=window[0].ts,
                        window_end=window[-1].ts,
                        event_ids=[k.event_id for k in window],
                    )
                )
                break
    return moments


def _sniper_triple(events: List[BufferedEvent]) -> List[Any]:
    from .highlights import ScoredMoment

    kills = sorted(
        (e for e in events if e.type == "kill_event" and _weapon_of(e) in SNIPER_CLASSES),
        key=lambda e: e.ts,
    )
    moments: List[Any] = []
    by_killer: Dict[str, List[BufferedEvent]] = {}
    for k in kills:
        by_killer.setdefault(k.payload.get("killer") or "Unknown", []).append(k)
    for killer, ks in by_killer.items():
        for i, anchor in enumerate(ks):
            window = [k for k in ks[i:] if 0.0 <= k.ts - anchor.ts <= 30.0]
            if len(window) >= 3:
                moments.append(
                    ScoredMoment(
                        kind="sniper_triple",
                        score=85,
                        reason=f"Sniper triple by {killer} — 3 long-range eliminations in {window[-1].ts - window[0].ts:.1f}s",
                        player=killer,
                        team=window[0].payload.get("killerTeam"),
                        opponents=[k.payload.get("victim") or "Unknown" for k in window],
                        window_start=window[0].ts,
                        window_end=window[-1].ts,
                        event_ids=[k.event_id for k in window],
                    )
                )
                break
    return moments


def _vehicle_kill(events: List[BufferedEvent]) -> List[Any]:
    from .highlights import ScoredMoment

    moments: List[Any] = []
    for e in events:
        if e.type != "kill_event":
            continue
        if _weapon_of(e) == "vehicle" or e.payload.get("vehicle_kill"):
            moments.append(
                ScoredMoment(
                    kind="vehicle_kill",
                    score=70,
                    reason=f"Vehicle kill by {e.payload.get('killer') or 'Unknown'}",
                    player=e.payload.get("killer"),
                    team=e.payload.get("killerTeam"),
                    opponents=[e.payload.get("victim") or "Unknown"],
                    window_start=e.ts,
                    window_end=e.ts,
                    event_ids=[e.event_id],
                )
            )
    return moments


_WEAPON_BLOCKER = (
    "this event set carries no kill with a truthful weapon "
    "attribution (weapon is None/'Unknown' — the Phase 23 icon "
    "matcher emits a class-level weapon only above its confidence "
    "threshold, never a guess)"
)
_VEHICLE_BLOCKER = (
    "no kill↔vehicle linkage exists; vehicle detection needs the "
    "trained BGMI model (COCO vehicle classes are not BGMI truth)"
)


def weapon_data_available(events: List[BufferedEvent]) -> bool:
    """True when at least one kill carries a real weapon attribution."""
    return any(
        e.type == "kill_event" and (e.payload.get("weapon") or "") not in ("", "Unknown")
        for e in events
    )


def trigger_registry(events: Optional[List[BufferedEvent]] = None) -> List[HighlightTrigger]:
    """The registered M11 plug-ready triggers for a given event set.

    A trigger is active for an event set only when that set actually
    carries the attribution the trigger needs — activation is
    data-driven, never a config lie.
    """
    has_weapon = weapon_data_available(events or [])
    has_vehicle = any(
        e.payload.get("vehicle_kill") or _weapon_of(e) == "vehicle" for e in (events or [])
    )
    return [
        HighlightTrigger(
            name="grenade_multi_kill",
            active=has_weapon,
            blocker=None if has_weapon else _WEAPON_BLOCKER,
            detector=_grenade_multi_kill,
        ),
        HighlightTrigger(
            name="sniper_triple",
            active=has_weapon,
            blocker=None if has_weapon else _WEAPON_BLOCKER,
            detector=_sniper_triple,
        ),
        HighlightTrigger(
            name="vehicle_kill",
            active=has_vehicle,
            blocker=None if has_vehicle else _VEHICLE_BLOCKER,
            detector=_vehicle_kill,
        ),
    ]
