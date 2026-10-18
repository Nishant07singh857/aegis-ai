"""AEGIS AI — zone timer service (Phase 10, Worker W2).

Operator-driven zone clock for the broadcast. Phases are an ordered list of
``{"name": str, "duration_seconds": int}`` — fully configurable, no
hardcoded BGMI timings.

State
-----
* ``status``: ``"no_data"`` (no phases configured — the honest initial
  state), ``"active"`` (a phase is ticking), ``"complete"`` (all phases ran).
* ``time_remaining``: computed from ``phase started_at + duration - now``,
  clamped at 0. The frontend counts down locally from the snapshot; the
  backend re-emits ``zone.tick`` on every operator action (phase advance,
  reconfiguration, reset).

The service never imports ``app.main`` (that would be circular). Every state
change publishes::

    {"type": "zone.tick", "data": {<full zone state snapshot>}}

``app/main.py`` registers ``ConnectionManager.broadcast`` as a listener at
startup, which fans the messages out over ``/ws/live`` — the exact pattern
used by ``DirectorService``.

Honesty: until an operator configures phases (or a live game feed drives
them), ``status`` is ``no_data`` and the frontend shows "waiting for zone
data" — no fake countdown is ever presented.
"""

from __future__ import annotations

import asyncio
import copy
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

import structlog

logger = structlog.get_logger("aegis.zone")

#: WS message type pushed to /ws/live on every zone state change.
WS_MESSAGE_TYPE = "zone.tick"

#: Zone statuses.
STATUS_NO_DATA = "no_data"
STATUS_ACTIVE = "active"
STATUS_COMPLETE = "complete"


class InvalidZoneStateError(ValueError):
    """Raised when a zone operation is not allowed in the current state
    (advance/reset with no phases configured, or advance past completion)
    or when a phase definition is invalid."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ZoneService:
    """In-memory single source of truth for the broadcast zone clock."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._listeners: List[Callable[[Dict[str, Any]], Awaitable[None]]] = []
        self._phases: List[Dict[str, Any]] = []
        self._current_index: Optional[int] = None
        self._phase_started_at: Optional[float] = None
        self._updated_at: str = _utcnow_iso()

    # ── listeners ────────────────────────────────────────────────

    def add_listener(
        self, callback: Callable[[Dict[str, Any]], Awaitable[None]]
    ) -> None:
        """Register an async ``callback(message)`` invoked on state changes."""
        self._listeners.append(callback)

    async def _notify(self, state: Dict[str, Any]) -> None:
        message = {"type": WS_MESSAGE_TYPE, "data": state}
        for listener in list(self._listeners):
            await listener(message)

    # ── reads ────────────────────────────────────────────────────

    @property
    def _status(self) -> str:
        if not self._phases:
            return STATUS_NO_DATA
        if self._current_index is None:
            return STATUS_COMPLETE
        return STATUS_ACTIVE

    def time_remaining(self) -> Optional[float]:
        """Seconds left in the current phase, or None when no phase is
        active. Clamped at 0 once a phase overruns — the operator (or the
        game feed) advances it; the backend never invents the next phase."""
        if self._status != STATUS_ACTIVE or self._phase_started_at is None:
            return None
        duration = float(self._phases[self._current_index]["duration_seconds"])
        return max(0.0, duration - (time.monotonic() - self._phase_started_at))

    def snapshot(self) -> Dict[str, Any]:
        """Current zone state (safe to send to any viewer)."""
        status = self._status
        current_phase: Optional[Dict[str, Any]] = None
        if status == STATUS_ACTIVE and self._current_index is not None:
            phase = self._phases[self._current_index]
            current_phase = {
                "name": phase["name"],
                "duration_seconds": phase["duration_seconds"],
                "phase_number": self._current_index + 1,
                "total_phases": len(self._phases),
            }
        remaining = self.time_remaining()
        return {
            "status": status,
            "phases": copy.deepcopy(self._phases),
            "current_phase": current_phase,
            "time_remaining_seconds": (
                round(remaining, 1) if remaining is not None else None
            ),
            "updated_at": self._updated_at,
        }

    # ── writes ───────────────────────────────────────────────────

    async def set_phases(
        self, phases: List[Dict[str, Any]], updated_by: str = "operator"
    ) -> Dict[str, Any]:
        """Replace the phase schedule and start phase 1 immediately.
        Each phase needs ``name`` (non-empty) and ``duration_seconds``
        (positive)."""
        validated: List[Dict[str, Any]] = []
        for i, phase in enumerate(phases or []):
            name = (phase.get("name") or "").strip() if isinstance(phase, dict) else ""
            duration = phase.get("duration_seconds") if isinstance(phase, dict) else None
            if not name:
                raise InvalidZoneStateError(
                    f"Phase {i + 1}: 'name' must be a non-empty string."
                )
            if (
                not isinstance(duration, (int, float))
                or isinstance(duration, bool)
                or duration <= 0
            ):
                raise InvalidZoneStateError(
                    f"Phase {i + 1} ({name!r}): 'duration_seconds' must be a "
                    f"positive number, got {duration!r}."
                )
            validated.append({"name": name, "duration_seconds": int(duration)})
        if not validated:
            raise InvalidZoneStateError(
                "Cannot configure an empty phase list: pass at least one phase."
            )
        async with self._lock:
            self._phases = validated
            self._current_index = 0
            self._phase_started_at = time.monotonic()
            self._updated_at = _utcnow_iso()
            state = copy.deepcopy(self.snapshot())
        logger.info(
            "zone phases set by %s: %d phases, starting '%s'",
            updated_by,
            len(validated),
            validated[0]["name"],
        )
        await self._notify(state)
        return state

    async def advance(self, updated_by: str = "operator") -> Dict[str, Any]:
        """Move to the next phase. Raises :class:`InvalidZoneStateError` when
        no phases are configured, or when the schedule is already complete."""
        async with self._lock:
            if self._status == STATUS_NO_DATA:
                raise InvalidZoneStateError(
                    "No zone phases configured: set_phases() first."
                )
            if self._status == STATUS_COMPLETE:
                raise InvalidZoneStateError(
                    "Zone schedule already complete: set_phases() to run a new one."
                )
            assert self._current_index is not None
            finished = self._phases[self._current_index]["name"]
            self._current_index += 1
            if self._current_index >= len(self._phases):
                self._current_index = None
                self._phase_started_at = None
                logger.info("zone schedule complete (by %s)", updated_by)
            else:
                self._phase_started_at = time.monotonic()
                logger.info(
                    "zone advanced by %s: '%s' -> '%s'",
                    updated_by,
                    finished,
                    self._phases[self._current_index]["name"],
                )
            self._updated_at = _utcnow_iso()
            state = copy.deepcopy(self.snapshot())
        await self._notify(state)
        return state

    async def reset(self, updated_by: str = "operator") -> Dict[str, Any]:
        """Restart the current schedule at phase 1. Raises
        :class:`InvalidZoneStateError` when no phases are configured."""
        async with self._lock:
            if self._status == STATUS_NO_DATA:
                raise InvalidZoneStateError(
                    "No zone phases configured: set_phases() first."
                )
            self._current_index = 0
            self._phase_started_at = time.monotonic()
            self._updated_at = _utcnow_iso()
            state = copy.deepcopy(self.snapshot())
        logger.info("zone reset to phase 1 (by %s)", updated_by)
        await self._notify(state)
        return state


#: Process-wide singleton used by the API router and app/main.py.
zone_service = ZoneService()
