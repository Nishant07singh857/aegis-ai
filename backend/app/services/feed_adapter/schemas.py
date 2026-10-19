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

    # Structured core fields — populated for kill/knock/team_wipe.
    player: Optional[str] = Field(default=None, description="Actor (killer).")
    team: Optional[str] = Field(default=None, description="Actor's team.")
    victim: Optional[str] = None
    victim_team: Optional[str] = None
    weapon: Optional[str] = None

    # Free-form extra detail the source provides (zone number, damage...).
    payload: Dict[str, Any] = Field(default_factory=dict)

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
