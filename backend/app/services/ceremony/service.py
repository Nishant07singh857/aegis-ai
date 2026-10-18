"""AEGIS AI — WWCD ceremony flow service (Phase 10, Worker W2).

Runs the post-match winner ceremony on the broadcast::

    idle -> winner_announce -> stats_rundown -> interview -> standings -> done

The service never imports ``app.main`` (that would be circular). It keeps a
list of async listener callbacks; every step change publishes::

    {"type": "ceremony.step", "data": {<full ceremony state snapshot>}}

``app/main.py`` registers ``ConnectionManager.broadcast`` as a listener at
startup, which fans the messages out over ``/ws/live`` — the exact pattern
used by ``DirectorService``.

Honesty rules
-------------
* The winner's identity comes from the caller (``winner_team_id`` resolved
  against the real teams service by the API layer, or an explicit
  ``team_name``). It is never guessed.
* ``stats`` and ``interview`` are operator/system-supplied payloads passed
  through verbatim — nothing is fabricated. When absent the state carries
  ``null`` and the frontend shows an honest "pending" card.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

import structlog

logger = structlog.get_logger("aegis.ceremony")

#: WS message type pushed to /ws/live on every ceremony step change.
WS_MESSAGE_TYPE = "ceremony.step"

#: Ordered ceremony steps. ``advance`` walks this list strictly in order.
STEPS: tuple[str, ...] = (
    "idle",
    "winner_announce",
    "stats_rundown",
    "interview",
    "standings",
    "done",
)


class InvalidCeremonyTransitionError(ValueError):
    """Raised when a ceremony transition is not allowed (advance before
    start, advance past done, or start while a ceremony is already running)."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CeremonyService:
    """In-memory single source of truth for the WWCD ceremony state."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._listeners: List[Callable[[Dict[str, Any]], Awaitable[None]]] = []
        self._step: str = "idle"
        self._winner_team_id: Optional[str] = None
        self._winner_team_name: Optional[str] = None
        self._stats: Optional[Dict[str, Any]] = None
        self._interview: Optional[Dict[str, Any]] = None
        self._updated_at: str = _utcnow_iso()

    # ── listeners ────────────────────────────────────────────────

    def add_listener(
        self, callback: Callable[[Dict[str, Any]], Awaitable[None]]
    ) -> None:
        """Register an async ``callback(message)`` invoked on step changes."""
        self._listeners.append(callback)

    async def _notify(self, state: Dict[str, Any]) -> None:
        message = {"type": WS_MESSAGE_TYPE, "data": state}
        for listener in list(self._listeners):
            await listener(message)

    # ── reads ────────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """Current ceremony state (safe to send to any viewer)."""
        return {
            "step": self._step,
            "steps": list(STEPS),
            "winner": {
                "team_id": self._winner_team_id,
                "team_name": self._winner_team_name,
            },
            "stats": copy.deepcopy(self._stats),
            "interview": copy.deepcopy(self._interview),
            "updated_at": self._updated_at,
        }

    # ── writes ───────────────────────────────────────────────────

    async def start(
        self,
        winner_team_id: Optional[str] = None,
        team_name: Optional[str] = None,
        stats: Optional[Dict[str, Any]] = None,
        interview: Optional[Dict[str, Any]] = None,
        updated_by: str = "operator",
    ) -> Dict[str, Any]:
        """Begin the ceremony for a match winner.

        Identity: ``team_name`` wins when given; otherwise ``winner_team_id``
        is recorded as-is (the API layer resolves it to a name against the
        real teams service before calling). Stats/interview payloads are
        stored verbatim and never invented — pass ``None`` for honest
        "pending" states.

        Raises :class:`InvalidCeremonyTransitionError` if a ceremony is
        already in progress (``reset()`` first) or if no winner identity is
        provided.
        """
        if not winner_team_id and not team_name:
            raise InvalidCeremonyTransitionError(
                "Cannot start a ceremony without a winner: provide "
                "'winner_team_id' or 'team_name'."
            )
        async with self._lock:
            if self._step != "idle":
                raise InvalidCeremonyTransitionError(
                    f"Ceremony already in progress (step {self._step!r}); "
                    "reset() before starting a new one."
                )
            self._step = "winner_announce"
            self._winner_team_id = winner_team_id
            self._winner_team_name = team_name
            self._stats = copy.deepcopy(stats) if stats is not None else None
            self._interview = (
                copy.deepcopy(interview) if interview is not None else None
            )
            self._updated_at = _utcnow_iso()
            state = copy.deepcopy(self.snapshot())
        logger.info(
            "ceremony started for %s (by %s)",
            team_name or winner_team_id,
            updated_by,
        )
        await self._notify(state)
        return state

    async def advance(self, updated_by: str = "operator") -> Dict[str, Any]:
        """Move to the next ceremony step in strict order. Raises
        :class:`InvalidCeremonyTransitionError` when called while idle
        (``start()`` first) or after the ceremony is ``done``."""
        async with self._lock:
            idx = STEPS.index(self._step)
            if self._step == "idle":
                raise InvalidCeremonyTransitionError(
                    "Cannot advance an idle ceremony: start() it first."
                )
            if self._step == "done":
                raise InvalidCeremonyTransitionError(
                    "Ceremony is already done: reset() to run another."
                )
            self._step = STEPS[idx + 1]
            self._updated_at = _utcnow_iso()
            state = copy.deepcopy(self.snapshot())
        logger.info("ceremony advanced -> %s (by %s)", self._step, updated_by)
        await self._notify(state)
        return state

    async def reset(self, updated_by: str = "operator") -> Dict[str, Any]:
        """Return the ceremony to ``idle`` and clear the winner payloads."""
        async with self._lock:
            self._step = "idle"
            self._winner_team_id = None
            self._winner_team_name = None
            self._stats = None
            self._interview = None
            self._updated_at = _utcnow_iso()
            state = copy.deepcopy(self.snapshot())
        logger.info("ceremony reset (by %s)", updated_by)
        await self._notify(state)
        return state


#: Process-wide singleton used by the API router and app/main.py.
ceremony_service = CeremonyService()
