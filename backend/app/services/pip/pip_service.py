# backend/app/services/pip/pip_service.py
"""
AEGIS AI — PiP (picture-in-picture) service.

Process-local source of truth for the picture-in-picture layout on the
broadcast output pages. State::

    enabled        bool          — inset visible at all
    main_source    str           — label of the main view (e.g. "output",
                                   "map-cam", "standings") — a *source key*,
                                   never a fabricated video feed
    inset_source   str           — label of the inset view (e.g. "map-cam")
    inset_position "top_left" | "top_right" | "bottom_left" | "bottom_right"
    updated_by     str | None
    updated_at     ISO-8601 timestamp

Auth model lives in the API router (``app/api/v1/pip/router.py``):
reads are any authenticated user, writes require operator+.

Broadcasting mirrors the director service (``app/services/director_service.py``):

* ``add_listener(...)`` — main.py registers ``ConnectionManager.broadcast``,
  so every state change fans out to ``/ws/live`` as
  ``{"type": "pip.state", "data": <state>}``.
* every change is also published to the event bus via
  ``event_bus.publish(event_type="pip.state", ...)`` — the event bus is the
  persistent/event-sourcing side; the listener list is the WS fan-out side.
  When the bus isn't connected (``event_bus._running`` is False) the publish
  is skipped — the listener fan-out still reaches live clients.

Visual Broadcast Bible §4 (docs/VISUAL_BROADCAST_BIBLE.md): Krafton NEVER
uses PiP for simultaneous fights — they pick ONE fight and commit, and the
other fight is missed live. The SOURCES keyspace below deliberately
contains NO fight feed, so fight+fight PiP is unreachable by construction;
PiP is for map+gameplay (and standings/killfeed/commentary) only. The
director AI never writes to this service — no director mode
(full_auto/semi_auto/manual) can route a second live fight to PiP.

Zero paid APIs, no secrets, no placeholders.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

import structlog

logger = structlog.get_logger("aegis.services.pip")

#: WebSocket message type.
WS_MESSAGE_TYPE = "pip.state"

#: Legal inset corners.
INSET_POSITIONS = ("top_left", "top_right", "bottom_left", "bottom_right")

#: Legal source keys for the main / inset slots. These are *keys* the
#: frontend maps to real views; "output" means the main director output
#: view (whatever the director is currently showing).
#:
#: Visual Broadcast Bible §4: the keyspace deliberately contains NO fight
#: feed — PiP is map+gameplay only (fight+fight PiP is unreachable by
#: construction; see the module docstring).
SOURCES = ("output", "map-cam", "standings", "killfeed", "commentary")

#: Signature of a broadcast listener: async callable taking the message dict.
BroadcastListener = Callable[[Dict[str, Any]], Awaitable[None]]


class PiPStateError(ValueError):
    """Invalid PiP state value (bad inset position / source key)."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_state() -> Dict[str, Any]:
    return {
        "enabled": False,
        "main_source": "output",
        "inset_source": "map-cam",
        "inset_position": "bottom_right",
        "updated_by": None,
        "updated_at": _utcnow_iso(),
    }


class PiPService:
    """In-memory PiP layout state with listener fan-out + event-bus emit."""

    def __init__(self) -> None:
        self._state: Dict[str, Any] = _default_state()
        self._listeners: List[BroadcastListener] = []
        self._lock = asyncio.Lock()
        self._log = logger.bind(service="pip_service")

    # ── listeners ──────────────────────────────────────────────

    def add_listener(self, listener: BroadcastListener) -> None:
        """Register an async callback invoked with every broadcast message."""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: BroadcastListener) -> None:
        """Unregister a previously added listener."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    # ── reads ──────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """Return a deep copy of the current PiP state."""
        return copy.deepcopy(self._state)

    # ── writes ─────────────────────────────────────────────────

    async def set(
        self,
        *,
        enabled: Optional[bool] = None,
        main_source: Optional[str] = None,
        inset_source: Optional[str] = None,
        inset_position: Optional[str] = None,
        updated_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Partial update of the PiP state. Any omitted field is unchanged.

        Raises :class:`PiPStateError` on an invalid inset_position or
        source key. Broadcasts the new state to listeners and publishes it
        on the event bus.
        """
        async with self._lock:
            if inset_position is not None:
                if inset_position not in INSET_POSITIONS:
                    raise PiPStateError(
                        f"Invalid inset_position {inset_position!r}: must be "
                        f"one of {', '.join(INSET_POSITIONS)}."
                    )
                self._state["inset_position"] = inset_position
            for field_name, value in (
                ("main_source", main_source),
                ("inset_source", inset_source),
            ):
                if value is not None:
                    if value not in SOURCES:
                        raise PiPStateError(
                            f"Invalid {field_name} {value!r}: must be one of "
                            f"{', '.join(SOURCES)}."
                        )
                    self._state[field_name] = value
            if enabled is not None:
                self._state["enabled"] = bool(enabled)
            self._state["updated_by"] = updated_by
            self._state["updated_at"] = _utcnow_iso()
            state = copy.deepcopy(self._state)

        self._log.info(
            "PiP state updated",
            enabled=state["enabled"],
            inset_position=state["inset_position"],
            updated_by=updated_by,
        )
        await self._broadcast({"type": WS_MESSAGE_TYPE, "data": state})
        await self._publish_event(state)
        return state

    # ── internals ──────────────────────────────────────────────

    async def _broadcast(self, message: Dict[str, Any]) -> None:
        for listener in list(self._listeners):
            await listener(message)

    async def _publish_event(self, state: Dict[str, Any]) -> None:
        """Emit the change on the event bus (persistent event side).

        Guarded on ``event_bus._running`` so the service works fine in
        tests / dev without Redis; ``publish()`` itself drops events with
        a warning when the bus isn't connected, and we skip it entirely.
        """
        try:
            from app.core.event_bus import event_bus

            if event_bus._running:
                await event_bus.publish(
                    event_type="pip.state",
                    data=state,
                    source="pip_service",
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("pip event-bus publish failed", error=str(exc))


#: Process-wide singleton used by the API router and app/main.py.
pip_service = PiPService()
