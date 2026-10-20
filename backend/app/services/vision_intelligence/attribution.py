# backend/app/services/vision_intelligence/attribution.py
"""Observer-slot → team attribution for vision events (Phase 22).

Vision detectors work in screen space: an ``hp_update`` names a screen
slot (``player_1``), a ``position_update`` names minimap markers, a
``utility`` event names frame pixels. NONE of them know a team id.

The one truthful attribution source is the observer mapping: the device
in observer slot N spectates exactly one team (``observer_slots`` DB
table, managed by ``app.services.observer_mapping``), and the vision
capture for that device is configured with that slot (payload
``observer_slot``, stamped by ``VisionCaptureService`` from
``AEGIS_VISION_OBSERVER_SLOT``, or the subscriber's configured default).

Rules (hard):
  * Slot present + mapped in DB  -> that team. 
  * Slot present + NOT mapped    -> None (event dropped, reason logged).
  * No slot anywhere             -> None (event dropped, reason logged).
  * A team is NEVER guessed from names, positions or heuristics.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import structlog

logger = structlog.get_logger("aegis.vision_intelligence.attribution")

#: Payload keys that may carry the observer slot, in priority order.
SLOT_PAYLOAD_KEYS = ("observer_slot", "slot_number", "slot")


@dataclass
class TeamAttribution:
    """Resolved attribution for one observer slot."""

    slot_number: int
    team_id: str
    team_name: Optional[str] = None


def extract_slot_number(
    payload: Dict[str, Any], default_slot: Optional[int] = None
) -> Optional[int]:
    """Pull the observer slot (1..16) from an event payload.

    Falls back to the subscriber's configured ``default_slot`` (the
    vision capture slot config for single-device rigs). Returns None
    when no usable slot exists — callers must treat that as
    "unattributable", never as a licence to guess.
    """
    for key in SLOT_PAYLOAD_KEYS:
        raw = payload.get(key)
        if raw is None:
            continue
        try:
            slot = int(raw)
        except (TypeError, ValueError):
            continue
        if 1 <= slot <= 16:
            return slot
    if default_slot is not None and 1 <= int(default_slot) <= 16:
        return int(default_slot)
    return None


class SlotResolver:
    """Interface: slot number -> TeamAttribution | None (unmapped)."""

    async def resolve(self, slot_number: int) -> Optional[TeamAttribution]:
        raise NotImplementedError


class StaticSlotResolver(SlotResolver):
    """In-memory resolver (tests, single-rig overrides)."""

    def __init__(self, mapping: Dict[int, TeamAttribution]):
        self._mapping = dict(mapping)

    async def resolve(self, slot_number: int) -> Optional[TeamAttribution]:
        return self._mapping.get(slot_number)


class DbSlotResolver(SlotResolver):
    """Resolve slots through the ``observer_slots`` DB table.

    Uses the existing ObserverMappingService (which seeds the 16 slot
    rows and resolves team names) over short-lived sessions from the
    injected factory. Results are cached briefly (default 5 s): slot
    assignments are tournament-ops state that can change mid-match, so
    the cache is a TTL, not a snapshot. DB failures resolve to None —
    the event is dropped as unmapped rather than misattributed.
    """

    def __init__(
        self,
        session_factory: Callable[[], Any],
        cache_ttl_s: float = 5.0,
        clock: Callable[[], float] = time.time,
    ):
        self._session_factory = session_factory
        self._cache_ttl_s = cache_ttl_s
        self._clock = clock
        self._cache: Dict[int, tuple] = {}  # slot -> (expires_at, attribution|None)

    async def resolve(self, slot_number: int) -> Optional[TeamAttribution]:
        now = self._clock()
        cached = self._cache.get(slot_number)
        if cached is not None and cached[0] > now:
            return cached[1]

        attribution: Optional[TeamAttribution] = None
        try:
            from app.services.observer_mapping.service import ObserverMappingService

            async with self._session_factory() as session:
                service = ObserverMappingService(session)
                slots = await service.list_slots()
                for slot in slots:
                    if slot.slot_number == slot_number and slot.team_id:
                        attribution = TeamAttribution(
                            slot_number=slot_number,
                            team_id=str(slot.team_id),
                            team_name=await service.team_name(slot.team_id),
                        )
                        break
        except Exception as exc:  # DB down etc. — drop, never guess
            logger.warning(
                "slot_resolution_failed",
                slot_number=slot_number,
                error=str(exc)[:200],
            )
            attribution = None

        self._cache[slot_number] = (now + self._cache_ttl_s, attribution)
        return attribution

    def invalidate(self, slot_number: Optional[int] = None) -> None:
        if slot_number is None:
            self._cache.clear()
        else:
            self._cache.pop(slot_number, None)
