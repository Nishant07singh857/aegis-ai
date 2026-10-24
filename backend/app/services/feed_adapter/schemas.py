# backend/app/services/feed_adapter/schemas.py
"""Normalized event schema — THE contract every game feed must speak.

Any real feed adapter (observer-slot relay, operator console, future CV
pipeline) maps its source events into NormalizedEvent and calls
BaseFeedAdapter.ingest(). Downstream consumers (replay buffer, highlight
detector, kill feed) only ever see these fields.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class FeedEventType(str, Enum):
    """Event vocabulary every adapter must map into."""

    KILL = "kill"
    KNOCK = "knock"
    TEAM_WIPE = "team_wipe"
    ZONE_UPDATE = "zone_update"
    MATCH_START = "match_start"
    MATCH_END = "match_end"
    STANDINGS_UPDATE = "standings_update"
    COMMENTARY = "commentary"
    # Phase 14 (vision pipeline): HUD readers emit these. They are real
    # game-state observations (alive counter, team-bar status), not guesses.
    ALIVE_COUNT = "alive_count"
    TEAM_STATUS = "team_status"
    # Phase 21 (CV wiring): the Gen-1 CV detectors (HP bars, utility,
    # minimap, healing, loot) emit these through the same contract.
    # Zone geometry from the minimap reuses ZONE_UPDATE above, with
    # payload["phase_source"] == "minimap_geometry".
    HP_UPDATE = "hp_update"
    UTILITY = "utility"
    POSITION_UPDATE = "position_update"
    HEALING = "healing"
    LOOT = "loot"
    # Phase 24 (spotter completions, discrepancies D1/D5): human spotter
    # tags for events no vision detector truthfully produces. Additive —
    # every pre-existing type keeps its value.
    AIRDROP_SPOTTED = "airdrop_spotted"
    FLARE_USED = "flare_used"


#: Headshot provenance values that may be PERSISTED as player stats
#: (Integrity Fix Pack C2). docs/WEAPON_RECOGNITION.md §2.7 measured
#: the icon-derived headshot flag at 7.7 % precision — noise — and bans
#: downstream consumption; the only trustworthy producers are human
#: ones: the spotter HEADSHOT toggle and manual/observer console entry,
#: both of which arrive through the authenticated feed-ingest endpoint,
#: where the server stamps one of these values from the caller's role
#: (a client-supplied source is never trusted — see api/v1/feed).
#: Vision/icon events resolve to ``vision_killfeed`` and can NEVER be
#: in this set.
HEADSHOT_TRUSTED_SOURCES = frozenset({"spotter", "manual", "observer"})

#: Provenance stamped on every event the vision killfeed pipeline
#: emits (icon-template marker matching — see WEAPON_RECOGNITION §2.7).
VISION_KILLFEED_SOURCE = "vision_killfeed"

#: Feed event type -> event-bus event type. kill/knock keep the bus
#: names the match simulator already publishes so every existing
#: consumer (replay buffer, highlights) works unchanged.
BUS_EVENT_TYPE_MAP: Dict[FeedEventType, str] = {
    FeedEventType.KILL: "kill_event",
    FeedEventType.KNOCK: "knock_event",
    FeedEventType.TEAM_WIPE: "team_wipe_event",
    FeedEventType.ZONE_UPDATE: "zone_event",
    FeedEventType.MATCH_START: "match_start",
    FeedEventType.MATCH_END: "match_end",
    FeedEventType.STANDINGS_UPDATE: "standings",
    FeedEventType.COMMENTARY: "commentary",
    FeedEventType.ALIVE_COUNT: "alive_count_update",
    FeedEventType.TEAM_STATUS: "team_status_update",
    FeedEventType.HP_UPDATE: "hp_update",
    FeedEventType.UTILITY: "utility_event",
    FeedEventType.POSITION_UPDATE: "position_update",
    FeedEventType.HEALING: "healing_event",
    FeedEventType.LOOT: "loot_event",
    FeedEventType.AIRDROP_SPOTTED: "airdrop_spotted",
    FeedEventType.FLARE_USED: "flare_used",
}


class NormalizedEvent(BaseModel):
    """One game event in the normalized contract."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: FeedEventType
    ts: float = Field(
        default_factory=time.time,
        description="Epoch seconds when the event happened in-game.",
    )
    match_id: Optional[str] = None

    # Provenance of this event (Integrity C2): who produced it —
    # "vision_killfeed" for the icon/OCR pipeline, "spotter" / "manual"
    # / "observer" for human console entry (stamped server-side by the
    # ingest endpoint from the caller's role), an adapter id for other
    # feed sources. None = unknown, which is NEVER a trusted headshot
    # source. Downstream stats consumers must discriminate on this —
    # see NormalizedEvent.is_headshot_trusted().
    source: Optional[str] = None

    # Structured core fields — populated for kill/knock/team_wipe.
    player: Optional[str] = Field(default=None, description="Actor (killer).")
    team: Optional[str] = Field(default=None, description="Actor's team.")
    victim: Optional[str] = None
    victim_team: Optional[str] = None
    weapon: Optional[str] = None

    # Free-form extra detail the source provides (zone number, damage...).
    payload: Dict[str, Any] = Field(default_factory=dict)

    def resolved_source(self) -> Optional[str]:
        """Best-known provenance: explicit field, else payload hints.

        The vision killfeed payload is recognised by its detector
        markers (``detector`` / ``ocr_confidence`` / icon-template
        weapon source) so even an unstamped vision event can never be
        mistaken for a human-confirmed one.
        """
        if self.source:
            text = str(self.source).strip().lower()
            if text:
                return text
        for key in ("source", "headshot_source"):
            raw = self.payload.get(key)
            if raw:
                return str(raw).strip().lower()
        if (
            self.payload.get("detector")
            or self.payload.get("weapon_source") == "icon_template"
            or "ocr_confidence" in self.payload
        ):
            return VISION_KILLFEED_SOURCE
        return None

    def is_headshot_trusted(self) -> bool:
        """True ONLY for a human-confirmed headshot (Integrity C2).

        The raw ``payload["headshot"]`` flag is set by the vision icon
        matcher at 7.7 % measured precision (WEAPON_RECOGNITION §2.7);
        it is a hint for highlights/commentary, never a statistic. Only
        a spotter/manual/observer-sourced flag may be persisted.
        """
        if not bool(self.payload.get("headshot", False)):
            return False
        return self.resolved_source() in HEADSHOT_TRUSTED_SOURCES

    def to_bus_payload(self) -> Dict[str, Any]:
        """Canonical bus payload (matches the simulator's kill_event shape)."""
        data: Dict[str, Any] = {
            "id": self.event_id,
            "timestamp": self.ts,
            "match_id": self.match_id,
            **self.payload,
        }
        if self.event_type in (FeedEventType.KILL, FeedEventType.KNOCK):
            data.update(
                {
                    "killer": self.player or "Unknown",
                    "killerTeam": self.team or "Unknown",
                    "victim": self.victim or "Unknown",
                    "victimTeam": self.victim_team or "Unknown",
                    "weapon": self.weapon or "Unknown",
                    "isKnock": self.event_type == FeedEventType.KNOCK,
                    # Phase 24: optional headshot flag
                    # (payload["headshot"]=True). TWO producers set it:
                    # the spotter toggle (truthful) and the vision icon
                    # matcher (7.7 % precision — noise, WEAPON_RECOGNITION
                    # §2.7). The raw flag stays for hint consumers
                    # (highlights/commentary); stats consumers MUST use
                    # "headshot_confirmed" (Integrity C2), which is True
                    # only for a trusted human source.
                    "headshot": bool(self.payload.get("headshot", False)),
                    "headshot_confirmed": self.is_headshot_trusted(),
                    "headshot_source": self.resolved_source() or "unknown",
                    "source": self.resolved_source() or "unknown",
                }
            )
        elif self.event_type == FeedEventType.TEAM_WIPE:
            data.update(
                {
                    "team": self.victim_team or self.team or "Unknown",
                    "eliminated_by": self.player,
                }
            )
        return data
