# backend/app/services/replay/highlight_triggers.py
"""
Plug-ready highlight triggers (Phase 22 / W3, M11).

Three PRD highlight kinds cannot fire truthfully today because the
kill events they depend on do not carry the required attribution:

* grenade multi-kill — needs ``weapon == "grenade"`` (or a utility
  attribution) on >=2 kills by one player in a window. Kill-feed
  weapon is honestly ``None`` today (weapon icons are never guessed;
  the trained icon detector has not landed).
* sniper triple — needs a sniper/DMR weapon class on 3 kills.
* vehicle kill — needs a kill↔vehicle linkage; the vehicle detector
  needs the trained model.

This module registers all three triggers behind ONE interface so the
day truthful weapon/vehicle data lands, they activate by flipping
``active`` (fed by real payloads) — until then each trigger is
INACTIVE, inspects nothing, and fires nothing. The registry is the
published contract: ``trigger_registry()`` tells the UI/docs exactly
which triggers are live and why the others are not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .buffer import BufferedEvent

SNIPER_CLASSES = {"sniper", "sr", "dmr", "kar98k", "m24", "awm", "mini14", "sks", "mk14"}


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
        (e for e in events if e.type == "kill_event" and _weapon_of(e) == "grenade"),
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
    "kill events carry no truthful weapon attribution today "
    "(kill-feed weapon is None by design — icons are never guessed); "
    "activates when the trained weapon-icon detector lands"
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
