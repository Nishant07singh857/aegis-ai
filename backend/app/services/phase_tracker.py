# backend/app/services/phase_tracker.py
"""Match-phase tracker (Phase 24, gap row 10).

Walks a full match the way a director thinks about it::

    not_started -> plane -> parachute -> loot -> early -> mid_game
                -> late_game -> final_circle -> ended

The drop phases (plane / parachute / landed) come from the EXISTING
signal-based estimator ``vision/parachute.py::ParachutePhaseDetector``
— this module is its first live caller (it previously had zero live
callers; the detector file itself is NOT modified). The tracker feeds
it the three signals it requires:

* ``players_alive`` — latest value from bus ``alive_count_update``
  events (payload keys ``players_alive`` / ``alive_count`` / ``players``
  are all accepted; the HUD reader's canonical key is ``players_alive``),
* ``kills_so_far`` — count of bus ``kill_event`` events this match,
* ``match_elapsed_s`` — from :class:`~app.services.match_clock.MatchClock`.

Post-drop phases are derived honestly from elapsed / alive / zone phase:

* ``loot``         — landed, elapsed < 360 s
* ``early``        — landed, 360 s <= elapsed < 900 s (rotation watch)
* ``mid_game``     — landed, elapsed >= 900 s, not late/final
* ``late_game``    — zone phase >= 5 OR alive <= 16
* ``final_circle`` — zone phase >= 7 OR alive <= 8

Zone phase is tracked from bus ``zone_event`` payloads
(``phase`` / ``zone_phase`` / ``phase_number``) and can also be set via
:meth:`set_zone_phase` (e.g. from the operator zone service).

Missing signals -> the detector returns ``unknown`` and the tracker
reports ``unknown`` too. A phase is NEVER guessed.

Phase changes are published on the bus as ``match_phase_changed``
events (when a bus is attached and running) and are always available
synchronously via :meth:`current_phase` / :meth:`snapshot` — the
director service queries the singleton for phase-aware suggestions.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

import structlog

from app.vision.parachute import DropPhase, ParachutePhaseDetector

logger = structlog.get_logger("aegis.phase_tracker")

SUBSCRIBER_ID = "phase_tracker"

#: Bus event types the tracker consumes.
TRACKED_EVENT_TYPES = [
    "alive_count_update",
    "kill_event",
    "knock_event",
    "zone_event",
    "match_start",
    "match_end",
]

# Match phases (director vocabulary).
PHASE_NOT_STARTED = "not_started"
PHASE_PLANE = "plane"
PHASE_PARACHUTE = "parachute"
PHASE_LOOT = "loot"
PHASE_EARLY = "early"
PHASE_MID = "mid_game"
PHASE_LATE = "late_game"
PHASE_FINAL = "final_circle"
PHASE_ENDED = "ended"
PHASE_UNKNOWN = "unknown"

LOOT_MAX_ELAPSED_S = 360.0
EARLY_MAX_ELAPSED_S = 900.0
LATE_ZONE_PHASE = 5
LATE_ALIVE_MAX = 16
FINAL_ZONE_PHASE = 7
FINAL_ALIVE_MAX = 8


def _as_int(value: Any) -> Optional[int]:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


class PhaseTracker:
    """Live match-phase state, fed from the bus + MatchClock."""

    def __init__(
        self,
        match_clock: Any = None,
        detector: Optional[ParachutePhaseDetector] = None,
        publish: Optional[Callable] = None,
    ) -> None:
        # Late-bound to the process singleton when not injected, so tests
        # can pass a fake clock and production gets the real one.
        if match_clock is None:
            from app.services.match_clock import match_clock as _mc

            match_clock = _mc
        self._match_clock = match_clock
        self._detector = detector or ParachutePhaseDetector()
        self._publish = publish  # async (event_type, data) -> None, optional
        self._bus: Any = None
        self._started = False

        self._alive: Optional[int] = None
        self._kills: int = 0
        self._zone_phase: Optional[int] = None
        self._phase: str = PHASE_NOT_STARTED
        self._drop_phase: Optional[str] = None
        self._reason: str = "match not started"
        self._history: List[Dict[str, Any]] = []

    # ── lifecycle / bus wiring ──────────────────────────────────

    def start(self, bus: Any) -> None:
        """Subscribe to the tracked bus events. Idempotent."""
        if self._started:
            return
        self._bus = bus
        if self._publish is None and bus is not None:
            async def _publish(event_type: str, data: Dict[str, Any]) -> None:
                if getattr(bus, "_running", False):
                    await bus.publish(
                        event_type=event_type,
                        data=data,
                        source="phase_tracker",
                        match_id=data.get("match_id"),
                    )

            self._publish = _publish
        bus.subscribe(
            subscriber_id=SUBSCRIBER_ID,
            callback=self.handle_record,
            event_types=list(TRACKED_EVENT_TYPES),
        )
        self._started = True
        logger.info("phase_tracker_subscribed", event_types=TRACKED_EVENT_TYPES)

    def stop(self) -> None:
        if not self._started:
            return
        try:
            self._bus.unsubscribe(SUBSCRIBER_ID)
        except Exception:  # pragma: no cover - defensive
            pass
        self._started = False

    @property
    def running(self) -> bool:
        return self._started

    # ── event intake ────────────────────────────────────────────

    async def handle_record(self, record: Any) -> None:
        """Bus callback. Never raises."""
        try:
            event = getattr(record, "event", None)
            event_type = getattr(event, "event_type", "") if event else ""
            data = getattr(event, "data", None) if event else None
            if not isinstance(data, dict):
                data = {}
            await self.handle_event(event_type, data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("phase_tracker_record_error", error=str(exc)[:200])

    async def handle_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Direct event intake (also used by tests without a bus)."""
        if event_type == "match_start":
            self._alive = None
            self._kills = 0
            self._zone_phase = None
        elif event_type == "alive_count_update":
            alive = None
            for key in ("players_alive", "alive_count", "players", "alive"):
                alive = _as_int(data.get(key))
                if alive is not None:
                    break
            if alive is not None:
                self._alive = alive
        elif event_type == "kill_event":
            self._kills += 1
        elif event_type == "zone_event":
            zone_phase = None
            for key in ("phase", "zone_phase", "phase_number", "zone"):
                zone_phase = _as_int(data.get(key))
                if zone_phase is not None:
                    break
            if zone_phase is not None:
                self._zone_phase = zone_phase
        # match_end / knock_event need no counters; evaluate covers ended.
        await self.evaluate()

    def set_zone_phase(self, zone_phase: Optional[int]) -> None:
        """External zone-phase feed (e.g. operator zone service)."""
        self._zone_phase = _as_int(zone_phase)

    def set_alive(self, alive: Optional[int]) -> None:
        self._alive = _as_int(alive)

    # ── phase derivation ────────────────────────────────────────

    def _clock_state(self) -> str:
        clock = self._match_clock
        state = getattr(clock, "state", None)
        if callable(state):
            state = state()
        return str(state or PHASE_NOT_STARTED)

    def _elapsed(self) -> Optional[float]:
        try:
            value = self._match_clock.elapsed_s()
        except Exception:  # pragma: no cover - defensive
            return None
        return float(value) if isinstance(value, (int, float)) else None

    def derive_phase(self) -> Dict[str, Any]:
        """Compute the current phase from the latest signals (pure read)."""
        clock_state = self._clock_state()
        elapsed = self._elapsed()
        if clock_state == "ended":
            return self._result(PHASE_ENDED, "match ended (match_end received)")
        if clock_state != "live":
            return self._result(
                PHASE_NOT_STARTED, "match clock not live — no match_start yet"
            )

        drop = self._detector.detect_phase(
            players_alive=self._alive,
            kills_so_far=self._kills,
            match_elapsed_s=elapsed,
        )
        self._drop_phase = drop.phase.value
        if drop.phase == DropPhase.UNKNOWN:
            return self._result(PHASE_UNKNOWN, drop.reason, drop_phase=drop.phase.value)
        if drop.phase == DropPhase.PLANE:
            return self._result(PHASE_PLANE, drop.reason, drop_phase=drop.phase.value)
        if drop.phase == DropPhase.PARACHUTE:
            return self._result(
                PHASE_PARACHUTE, drop.reason, drop_phase=drop.phase.value
            )

        # Landed: subdivide by zone / alive / elapsed.
        if (self._zone_phase is not None and self._zone_phase >= FINAL_ZONE_PHASE) or (
            self._alive is not None and self._alive <= FINAL_ALIVE_MAX
        ):
            return self._result(
                PHASE_FINAL,
                f"final circle: zone phase {self._zone_phase} / alive {self._alive}",
                drop_phase=drop.phase.value,
            )
        if (self._zone_phase is not None and self._zone_phase >= LATE_ZONE_PHASE) or (
            self._alive is not None and self._alive <= LATE_ALIVE_MAX
        ):
            return self._result(
                PHASE_LATE,
                f"late game: zone phase {self._zone_phase} / alive {self._alive}",
                drop_phase=drop.phase.value,
            )
        if elapsed is not None and elapsed < LOOT_MAX_ELAPSED_S:
            return self._result(
                PHASE_LOOT, "landed, early loot window (<360s elapsed)",
                drop_phase=drop.phase.value,
            )
        if elapsed is not None and elapsed < EARLY_MAX_ELAPSED_S:
            return self._result(
                PHASE_EARLY, "landed, early rotations (360-900s elapsed)",
                drop_phase=drop.phase.value,
            )
        return self._result(
            PHASE_MID, "mid game (landed, >=900s, not late/final)",
            drop_phase=drop.phase.value,
        )

    def _result(
        self, phase: str, reason: str, drop_phase: Optional[str] = None
    ) -> Dict[str, Any]:
        return {
            "phase": phase,
            "drop_phase": drop_phase if drop_phase is not None else self._drop_phase,
            "reason": reason,
            "alive_count": self._alive,
            "kills_so_far": self._kills,
            "zone_phase": self._zone_phase,
            "elapsed_s": self._elapsed(),
            "match_id": getattr(self._match_clock, "match_id", None),
        }

    async def evaluate(self) -> Dict[str, Any]:
        """Recompute the phase; publish a bus event on change."""
        result = self.derive_phase()
        new_phase = result["phase"]
        if new_phase != self._phase:
            previous = self._phase
            self._phase = new_phase
            self._reason = result["reason"]
            entry = {
                **result,
                "previous_phase": previous,
                "changed_at": time.time(),
            }
            self._history.append(entry)
            self._history = self._history[-50:]
            if self._publish is not None:
                try:
                    await self._publish("match_phase_changed", dict(entry))
                except Exception:  # pragma: no cover - defensive
                    pass
        else:
            self._reason = result["reason"]
        return self.snapshot()

    def current_phase(self) -> str:
        """Current phase string — re-derived so callers never read stale."""
        return self.derive_phase()["phase"]

    def snapshot(self) -> Dict[str, Any]:
        result = self.derive_phase()
        result["published_phase"] = self._phase
        result["history"] = list(self._history[-10:])
        return result


#: Process-wide singleton (wired in app/main.py lifespan).
phase_tracker = PhaseTracker()


def get_phase_tracker() -> PhaseTracker:
    return phase_tracker
