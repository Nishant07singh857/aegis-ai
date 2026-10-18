"""AEGIS AI — pre-show flow service (Phase 10, Worker W2).

State machine for the broadcast pre-show: the steps that play out before a
match goes live::

    countdown -> desk_intro -> match_intro -> starting

The service never imports ``app.main`` (that would be circular). It keeps a
list of async listener callbacks; every state change publishes::

    {"type": "preshow.state", "data": {<full pre-show state snapshot>}}

``app/main.py`` registers ``ConnectionManager.broadcast`` as a listener at
startup, which fans the messages out over ``/ws/live`` — the exact pattern
used by ``DirectorService`` (``app/services/director_service.py``).

Honesty: the countdown step is meaningless without a target timestamp, so
the state carries ``countdown_target`` (ISO-8601 or null). A null target is
not faked — the frontend renders an honest "LIVE SOON" placeholder.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

import structlog

logger = structlog.get_logger("aegis.preshow")

#: WS message type pushed to /ws/live on every pre-show state change.
WS_MESSAGE_TYPE = "preshow.state"

#: Ordered pre-show steps. ``set_step`` accepts any valid step (operators may
#: jump, e.g. skip the desk when running late); validation is name-only.
STEPS: tuple[str, ...] = ("countdown", "desk_intro", "match_intro", "starting")


class InvalidPreshowStepError(ValueError):
    """Raised when an unknown pre-show step is requested."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PreshowService:
    """In-memory single source of truth for the broadcast pre-show state."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._listeners: List[Callable[[Dict[str, Any]], Awaitable[None]]] = []
        self._step: str = "countdown"
        self._countdown_target: Optional[str] = None
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

    def snapshot(self) -> Dict[str, Any]:
        """Current pre-show state (safe to send to any viewer)."""
        return {
            "step": self._step,
            "steps": list(STEPS),
            "countdown_target": self._countdown_target,
            "updated_at": self._updated_at,
        }

    # ── writes ───────────────────────────────────────────────────

    async def set_step(self, step: str, updated_by: str = "operator") -> Dict[str, Any]:
        """Move the pre-show to ``step``. Raises :class:`InvalidPreshowStepError`
        for unknown steps. No-op steps (same value) do not emit WS traffic."""
        if step not in STEPS:
            raise InvalidPreshowStepError(
                f"Unknown pre-show step {step!r}: must be one of {list(STEPS)}."
            )
        async with self._lock:
            if step == self._step:
                return self.snapshot()
            self._step = step
            self._updated_at = _utcnow_iso()
            state = copy.deepcopy(self.snapshot())
        logger.info("preshow step -> %s (by %s)", step, updated_by)
        await self._notify(state)
        return state

    async def set_countdown_target(
        self, target_iso: str, updated_by: str = "operator"
    ) -> Dict[str, Any]:
        """Set the countdown target (ISO-8601 timestamp). The value only has
        to parse — a target in the past is allowed and simply reads 00:00:00
        on the frontend ("LIVE NOW")."""
        try:
            parsed = datetime.fromisoformat(target_iso.replace("Z", "+00:00"))
        except (ValueError, TypeError) as exc:
            raise InvalidPreshowStepError(
                f"Invalid countdown target {target_iso!r}: must be an ISO-8601 "
                f"timestamp ({exc})."
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        async with self._lock:
            self._countdown_target = parsed.isoformat()
            self._updated_at = _utcnow_iso()
            state = copy.deepcopy(self.snapshot())
        logger.info("preshow countdown target -> %s (by %s)", parsed.isoformat(), updated_by)
        await self._notify(state)
        return state

    async def reset(self, updated_by: str = "operator") -> Dict[str, Any]:
        """Return the pre-show to ``countdown`` and clear the target."""
        async with self._lock:
            self._step = "countdown"
            self._countdown_target = None
            self._updated_at = _utcnow_iso()
            state = copy.deepcopy(self.snapshot())
        logger.info("preshow reset (by %s)", updated_by)
        await self._notify(state)
        return state


#: Process-wide singleton used by the API router and app/main.py.
preshow_service = PreshowService()
