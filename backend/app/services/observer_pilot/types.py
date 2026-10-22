"""
AEGIS AI - Observer Auto-Pilot shared types (Phase 29, W1).

The shared contract for the whole Phase 29 line: W1 (POV state reader)
produces these, W2/W3/W4 consume them EXACTLY as defined here.

Honesty rules baked into the types:
  * A player whose name/id is not bound from the roster is
    ``player_id=None`` / ``name=None`` -- never a guessed identity.
  * A status that could not be read is ``POVPlayerStatus.UNKNOWN``.
  * ``hp_fraction`` is None when the HP strip could not be measured;
    it is never estimated from status alone.
  * ``FeedPOVState.identity_known`` is True only when the spectated
    identity was resolved against the roster (see state.py for the
    two-signal agreement rules); ``identity_source`` records HOW
    ("both" / "highlight" / "ocr" / "none").
  * ``stale`` marks a cached state whose frame did not change since
    the last real read -- ``observed_at`` is NOT refreshed for stale
    copies, so consumers can see the state's true age.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple


class POVPlayerStatus(str, Enum):
    ALIVE = "alive"
    KNOCKED = "knocked"
    DEAD = "dead"
    UNKNOWN = "unknown"


@dataclass
class PlayerPOV:
    """One roster slot's observed state on one feed."""

    roster_index: int  # 0..3, position in the team roster / team bar
    player_id: Optional[str] = None
    name: Optional[str] = None
    status: POVPlayerStatus = POVPlayerStatus.UNKNOWN
    hp_fraction: Optional[float] = None  # 0..1; None = not measurable
    hp_velocity: Optional[float] = None  # fraction/sec; negative = falling
    is_spectated: bool = False


@dataclass
class FeedPOVState:
    """The full POV state of one observer feed at one moment."""

    slot_number: int
    source_id: str
    team_id: Optional[str] = None
    observed_at: float = field(default_factory=time.time)
    spectated_index: Optional[int] = None  # roster index; None = UNKNOWN
    players: Tuple[PlayerPOV, ...] = ()
    identity_known: bool = False
    # How spectated_index was resolved: "both" | "highlight" | "ocr" | "none".
    identity_source: str = "none"
    # True when this state is a cached copy (frame unchanged since the
    # last real read). observed_at keeps the ORIGINAL read time.
    stale: bool = False
    # Raw nameplate OCR text behind an "ocr"/"both" resolution (audit).
    nameplate_text: Optional[str] = None

    def spectated_player(self) -> Optional[PlayerPOV]:
        if self.spectated_index is None:
            return None
        if 0 <= self.spectated_index < len(self.players):
            return self.players[self.spectated_index]
        return None

    def to_dict(self) -> dict:
        return {
            "slot_number": self.slot_number,
            "source_id": self.source_id,
            "team_id": self.team_id,
            "observed_at": self.observed_at,
            "spectated_index": self.spectated_index,
            "identity_known": self.identity_known,
            "identity_source": self.identity_source,
            "stale": self.stale,
            "nameplate_text": self.nameplate_text,
            "players": [
                {
                    "roster_index": p.roster_index,
                    "player_id": p.player_id,
                    "name": p.name,
                    "status": p.status.value,
                    "hp_fraction": (
                        round(p.hp_fraction, 3) if p.hp_fraction is not None else None
                    ),
                    "hp_velocity": (
                        round(p.hp_velocity, 3) if p.hp_velocity is not None else None
                    ),
                    "is_spectated": p.is_spectated,
                }
                for p in self.players
            ],
        }


__all__ = ["POVPlayerStatus", "PlayerPOV", "FeedPOVState"]
