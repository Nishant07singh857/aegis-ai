# backend/app/core/match_state.py
"""
AEGIS AI - Centralized Match State

Single source of truth for live match state: phase machine, alive counts,
zone info, kill feed buffer, and fight registry. Driven synchronously by
direct method calls from agents; the state machine optionally notifies the
event bus on phase transitions (best-effort, never raises).
"""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Deque, Dict, List, Optional

import structlog

logger = structlog.get_logger("aegis.core.match_state")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MATCH PHASE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MatchPhase(str, Enum):
    """Phases of a battle-royale match lifecycle."""
    NOT_STARTED = "not_started"
    LOBBY = "lobby"
    PLANE = "plane"
    LIVE = "live"
    FINAL_CIRCLE = "final_circle"
    FINISHED = "finished"


# Valid phase transitions. FINISHED is terminal.
_VALID_TRANSITIONS: Dict[MatchPhase, set] = {
    MatchPhase.NOT_STARTED: {
        MatchPhase.LOBBY,
        MatchPhase.PLANE,
        MatchPhase.LIVE,  # fast-path for tests / instant-start scrims
    },
    MatchPhase.LOBBY: {MatchPhase.PLANE, MatchPhase.LIVE},
    MatchPhase.PLANE: {MatchPhase.LIVE},
    MatchPhase.LIVE: {MatchPhase.FINAL_CIRCLE, MatchPhase.FINISHED},
    MatchPhase.FINAL_CIRCLE: {MatchPhase.FINISHED},
    MatchPhase.FINISHED: set(),
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MATCH STATE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class MatchState:
    """Mutable snapshot of everything known about a live match."""

    match_id: str
    phase: MatchPhase = MatchPhase.NOT_STARTED

    # Alive counts (16 teams x 4 players default)
    teams_alive: int = 16
    players_alive: int = 64

    # Zone info
    zone_phase: int = 0
    zone_center_x: float = 0.0
    zone_center_y: float = 0.0
    zone_radius: float = 0.0

    # Timing
    match_time: float = 0.0

    # Totals
    total_kills: int = 0
    total_damage: int = 0

    # Kill feed buffer (capped)
    recent_kills: Deque[Dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=50)
    )

    # Fight registry
    active_fights: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    fight_history: List[Dict[str, Any]] = field(default_factory=list)

    # ── Mutators ────────────────────────────────────────────────

    def update_teams_alive(self, teams_alive: int) -> None:
        """Update teams alive; players alive scales at 4 per team."""
        self.teams_alive = max(0, teams_alive)
        self.players_alive = self.teams_alive * 4

    def update_zone(
        self,
        phase: int,
        center_x: float,
        center_y: float,
        radius: float,
    ) -> None:
        """Update zone phase and geometry."""
        self.zone_phase = phase
        self.zone_center_x = center_x
        self.zone_center_y = center_y
        self.zone_radius = radius

    def update_match_time(self, match_time: float) -> None:
        """Update elapsed match time in seconds."""
        self.match_time = match_time

    def add_kill_event(
        self,
        killer_id: str,
        victim_id: str,
        weapon: str,
        headshot: bool = False,
        **extra: Any,
    ) -> Dict[str, Any]:
        """Record a kill in the capped kill-feed buffer."""
        event = {
            "event_id": str(uuid.uuid4()),
            "killer_id": killer_id,
            "victim_id": victim_id,
            "weapon": weapon,
            "headshot": headshot,
            "timestamp": time.time(),
            **extra,
        }
        self.recent_kills.append(event)
        self.total_kills += 1
        return event

    def add_fight(
        self,
        teams_involved: List[str],
        location: tuple,
        intensity: float,
        fight_id: Optional[str] = None,
    ) -> str:
        """Register an active fight; returns the fight id."""
        fid = fight_id or f"fight_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        self.active_fights[fid] = {
            "fight_id": fid,
            "teams": list(teams_involved),
            "location": tuple(location),
            "intensity": intensity,
            "is_active": True,
            "started_at": time.time(),
        }
        return fid

    def resolve_fight(self, fight_id: str, winner: Optional[str] = None) -> None:
        """Move a fight from active to history."""
        fight = self.active_fights.pop(fight_id, None)
        if fight is None:
            return
        fight["is_active"] = False
        fight["winner"] = winner
        fight["resolved_at"] = time.time()
        self.fight_history.append(fight)

    def get_active_fights_count(self) -> int:
        """Number of currently active fights."""
        return len(self.active_fights)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the full state snapshot."""
        return {
            "match_id": self.match_id,
            "phase": self.phase.value,
            "teams_alive": self.teams_alive,
            "players_alive": self.players_alive,
            "zone_phase": self.zone_phase,
            "zone_center": (self.zone_center_x, self.zone_center_y),
            "zone_radius": self.zone_radius,
            "match_time": self.match_time,
            "total_kills": self.total_kills,
            "total_damage": self.total_damage,
            "recent_kills": list(self.recent_kills),
            "active_fights": dict(self.active_fights),
            "active_fights_count": self.get_active_fights_count(),
            "fight_history": list(self.fight_history),
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MATCH STATE MACHINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MatchStateMachine:
    """
    Phase machine wrapping MatchState.

    Enforces the legal transition graph, records phase history with
    timings, and auto-detects final-circle and match-end conditions.
    Event-bus notifications are best-effort and never raise.
    """

    def __init__(self, match_id: str, event_bus: Any = None):
        self.match_id = match_id
        self.event_bus = event_bus
        self.state = MatchState(match_id=match_id)
        self.phase_history: List[Dict[str, Any]] = []
        self._phase_entered_at: Dict[MatchPhase, float] = {
            MatchPhase.NOT_STARTED: time.time()
        }
        self._phase_exited_at: Dict[MatchPhase, float] = {}
        self._log = logger.bind(match_id=match_id)

    # ── Transitions ─────────────────────────────────────────────

    def transition_to(self, phase: MatchPhase) -> None:
        """Move to a new phase; raises ValueError on illegal transitions."""
        current = self.state.phase
        if phase == current:
            return
        allowed = _VALID_TRANSITIONS.get(current, set())
        if phase not in allowed:
            raise ValueError(
                f"Illegal match phase transition: {current.value} -> {phase.value}"
            )

        now = time.time()
        self._phase_exited_at[current] = now
        self.state.phase = phase
        self._phase_entered_at[phase] = now
        self.phase_history.append(
            {
                "from": current.value,
                "to": phase.value,
                "entered_at": now,
            }
        )
        self._log.info(
            "Match phase transition",
            from_phase=current.value,
            to_phase=phase.value,
        )
        self._notify_phase_change(current, phase)

    def _notify_phase_change(
        self, old_phase: MatchPhase, new_phase: MatchPhase
    ) -> None:
        """Best-effort event-bus notification; never raises."""
        if self.event_bus is None:
            return
        try:
            result = self.event_bus.publish(
                event_type="match_state_change",
                data={
                    "match_id": self.match_id,
                    "old_phase": old_phase.value,
                    "new_phase": new_phase.value,
                },
                match_id=self.match_id,
                source="match_state_machine",
            )
            if inspect.isawaitable(result):
                # Synchronous context with a mock/async bus: don't leak
                # un-awaited coroutines; schedule only if a loop is running.
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    result.close()
                else:
                    loop.create_task(result)
        except Exception:
            pass

    # ── Auto-detection ──────────────────────────────────────────

    def _check_final_circle(self) -> None:
        """Promote LIVE -> FINAL_CIRCLE once the zone is deep enough."""
        if self.state.phase == MatchPhase.LIVE and self.state.zone_phase >= 7:
            self.transition_to(MatchPhase.FINAL_CIRCLE)

    def _check_match_end(self) -> None:
        """Promote LIVE -> FINISHED when a single team remains."""
        if self.state.phase == MatchPhase.LIVE and self.state.teams_alive == 1:
            self.transition_to(MatchPhase.FINISHED)

    def check_auto_transitions(self) -> None:
        """Run all auto-detection checks (call on zone/alive updates)."""
        self._check_final_circle()
        self._check_match_end()

    # ── Queries ─────────────────────────────────────────────────

    def get_phase_duration(self, phase: MatchPhase) -> float:
        """Seconds spent in a phase (to exit, or until now if current)."""
        entered = self._phase_entered_at.get(phase)
        if entered is None:
            return 0.0
        exited = self._phase_exited_at.get(phase, time.time())
        return max(0.0, exited - entered)

    def get_summary(self) -> Dict[str, Any]:
        """Compact summary for dashboards and tests."""
        return {
            "match_id": self.match_id,
            "phase": self.state.phase.value,
            "teams_alive": self.state.teams_alive,
            "players_alive": self.state.players_alive,
            "total_kills": self.state.total_kills,
            "zone_phase": self.state.zone_phase,
            "match_time": self.state.match_time,
            "active_fights": self.state.get_active_fights_count(),
            "phase_history": list(self.phase_history),
        }
