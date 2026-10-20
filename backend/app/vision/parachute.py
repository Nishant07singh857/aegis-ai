# backend/app/vision/parachute.py
"""
AEGIS AI - Parachute-Phase Detection (Phase 22 / W3, M3). PLUG-READY.

There is no visual parachute detector in this build (no trained model
can see parachutes truthfully). What IS truthfully derivable is the
match's drop phase from early-match signals the live pipeline already
produces honestly:

* ``players_alive`` from the alive-count reader,
* ``kills_so_far`` from the kill feed,
* ``match_elapsed_s`` from the match clock.

Phase rules (BGMI: 64 players / 16 teams lobby):

* ``plane``     — elapsed < 60 s, nobody has landed a kill yet and the
  full lobby is alive: players are in the plane / jumping.
* ``parachute`` — elapsed 60–150 s, full lobby still alive, zero kills:
  players are under canopy / just landing. (A kill ends this phase —
  combat means boots are on the ground somewhere.)
* ``landed``    — any kill recorded, or alive count below the full
  lobby, or elapsed > 150 s with kills: the drop phase is over.
* ``unknown``   — any required signal missing. NEVER guessed.

The detector consumes signals; it does not read frames. When a real
visual drop detector exists (trained model), it can replace this
signal-based estimate behind the same ``detect_phase()`` shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

#: Full BGMI lobby size this product runs (16 teams x 4).
FULL_LOBBY_PLAYERS = 64

PLANE_MAX_ELAPSED_S = 60.0
PARACHUTE_MAX_ELAPSED_S = 150.0


class DropPhase(str, Enum):
    PLANE = "plane"
    PARACHUTE = "parachute"
    LANDED = "landed"
    UNKNOWN = "unknown"


@dataclass
class ParachutePhaseResult:
    phase: DropPhase
    reason: str
    players_alive: Optional[int] = None
    kills_so_far: Optional[int] = None
    match_elapsed_s: Optional[float] = None


class ParachutePhaseDetector:
    """Signal-based drop-phase estimator (see module docstring)."""

    def detect_phase(
        self,
        *,
        players_alive: Optional[int] = None,
        kills_so_far: Optional[int] = None,
        match_elapsed_s: Optional[float] = None,
        full_lobby: int = FULL_LOBBY_PLAYERS,
    ) -> ParachutePhaseResult:
        def _result(phase: DropPhase, reason: str) -> ParachutePhaseResult:
            return ParachutePhaseResult(
                phase=phase,
                reason=reason,
                players_alive=players_alive,
                kills_so_far=kills_so_far,
                match_elapsed_s=match_elapsed_s,
            )

        if players_alive is None or kills_so_far is None or match_elapsed_s is None:
            return _result(
                DropPhase.UNKNOWN,
                "missing signal(s): players_alive, kills_so_far and "
                "match_elapsed_s are all required — phase not guessed",
            )
        if kills_so_far > 0 or players_alive < full_lobby:
            return _result(
                DropPhase.LANDED,
                "combat has started (kills recorded or lobby no longer full)",
            )
        if match_elapsed_s < PLANE_MAX_ELAPSED_S:
            return _result(DropPhase.PLANE, "full lobby alive, no kills, <60s elapsed")
        if match_elapsed_s <= PARACHUTE_MAX_ELAPSED_S:
            return _result(
                DropPhase.PARACHUTE,
                "full lobby alive, no kills, 60–150s elapsed (under canopy)",
            )
        return _result(
            DropPhase.LANDED,
            "drop window elapsed with a full lobby and no kills (quiet landing)",
        )
