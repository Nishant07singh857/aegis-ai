# backend/app/services/director_service.py
"""
AEGIS AI — Director service.

The single source of truth for what is on the broadcast output page.
Holds the director state in memory (process-local, like MATCH_STATE):

    mode        "auto" | "manual"            (default "auto")
    scene       "main" | "standings" | "killfeed_focus" | "commentary"
                                             (default "main")
    overlays    {"scorebug": bool, "killfeed": bool}  (default both true)
    lower_third {"visible": bool, "title": str, "subtitle": str}
                                             (default hidden/empty)
    updated_by  str | None
    updated_at  ISO-8601 timestamp

Rules
-----
* AUTO mode: only the system (match simulator / AI logic in ``app/main.py``)
  may write, via :meth:`system_publish`. Operator API writes are rejected
  with :class:`DirectorModeError` (the API layer turns that into HTTP 409).
* MANUAL mode: operator API writes are authoritative; :meth:`system_publish`
  is a no-op so the simulator/AI can never override the operator.
* Mode flips auto<->manual are explicit, via the director API.

Broadcasting
------------
The service never imports ``app.main`` (that would be circular). Instead it
keeps a list of async listener callbacks; every state change publishes::

    {"type": "director.state", "data": {<full director state>}}

``app/main.py`` registers ``ConnectionManager.broadcast`` as a listener at
startup, which fans the message out over ``/ws/live``.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

DIRECTOR_MODES = ("auto", "manual")
DIRECTOR_SCENES = ("main", "standings", "killfeed_focus", "commentary")

#: WebSocket message type for every director state change.
WS_MESSAGE_TYPE = "director.state"

#: Signature of a broadcast listener: async callable taking the message dict.
BroadcastListener = Callable[[Dict[str, Any]], Awaitable[None]]


class DirectorModeError(RuntimeError):
    """Operator write attempted while the director is in AUTO mode."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_state() -> Dict[str, Any]:
    return {
        "mode": "auto",
        "scene": "main",
        "overlays": {"scorebug": True, "killfeed": True},
        "lower_third": {"visible": False, "title": "", "subtitle": ""},
        "updated_by": None,
        "updated_at": _utcnow_iso(),
    }


def _validate_mode(mode: str) -> str:
    mode = (mode or "").strip().lower()
    if mode not in DIRECTOR_MODES:
        raise ValueError(
            f"Invalid director mode {mode!r}: must be one of "
            f"{', '.join(DIRECTOR_MODES)}."
        )
    return mode


def _validate_scene(scene: str) -> str:
    scene = (scene or "").strip().lower()
    if scene not in DIRECTOR_SCENES:
        raise ValueError(
            f"Invalid director scene {scene!r}: must be one of "
            f"{', '.join(DIRECTOR_SCENES)}."
        )
    return scene


class DirectorService:
    """In-memory director state with mode-gated writes and WS fan-out."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._listeners: List[BroadcastListener] = []
        self._state: Dict[str, Any] = _default_state()

    # ── listeners ──────────────────────────────────────────────

    def add_listener(self, listener: BroadcastListener) -> None:
        """Register an async callback invoked with every state-change message."""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: BroadcastListener) -> None:
        """Unregister a previously added listener."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    # ── reads ──────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """Return a deep copy of the current director state."""
        return copy.deepcopy(self._state)

    # ── operator writes (MANUAL mode only, except set_mode) ─────

    async def set_mode(self, mode: str, updated_by: Optional[str] = None) -> Dict[str, Any]:
        """Explicit auto<->manual transition. Allowed in either mode.

        No-op when the mode is already active: returns the current state
        without touching updated_at/updated_by or fanning out a WS frame.
        """
        mode = _validate_mode(mode)
        if mode == self._state["mode"]:
            return copy.deepcopy(self._state)
        async with self._lock:
            self._state["mode"] = mode
            self._state["updated_by"] = updated_by
            self._state["updated_at"] = _utcnow_iso()
            state = copy.deepcopy(self._state)
        await self._broadcast(state)
        return state

    def _require_manual(self) -> None:
        if self._state["mode"] != "manual":
            raise DirectorModeError(
                "Director is in AUTO mode: operator scene/overlay/lower-third "
                "writes are rejected. Switch to MANUAL first via "
                "POST /api/v1/director/mode {\"mode\": \"manual\"}."
            )

    async def set_scene(self, scene: str, updated_by: Optional[str] = None) -> Dict[str, Any]:
        """Operator scene select. Rejected in AUTO mode."""
        scene = _validate_scene(scene)
        async with self._lock:
            self._require_manual()
            self._state["scene"] = scene
            self._state["updated_by"] = updated_by
            self._state["updated_at"] = _utcnow_iso()
            state = copy.deepcopy(self._state)
        await self._broadcast(state)
        return state

    async def set_overlays(
        self,
        scorebug: Optional[bool] = None,
        killfeed: Optional[bool] = None,
        updated_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Operator overlay toggles (partial update). Rejected in AUTO mode."""
        if scorebug is None and killfeed is None:
            raise ValueError(
                "Nothing to update: provide at least one of "
                "'scorebug' or 'killfeed'."
            )
        async with self._lock:
            self._require_manual()
            if scorebug is not None:
                self._state["overlays"]["scorebug"] = bool(scorebug)
            if killfeed is not None:
                self._state["overlays"]["killfeed"] = bool(killfeed)
            self._state["updated_by"] = updated_by
            self._state["updated_at"] = _utcnow_iso()
            state = copy.deepcopy(self._state)
        await self._broadcast(state)
        return state

    async def set_lower_third(
        self,
        visible: bool,
        title: str = "",
        subtitle: str = "",
        updated_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Operator lower-third control. Rejected in AUTO mode.

        Hiding (visible=False) preserves the current title/subtitle so the
        composer's text survives a show/hide cycle; they are only replaced
        when explicitly shown with new text.
        """
        async with self._lock:
            self._require_manual()
            previous = dict(self._state["lower_third"])
            if visible:
                previous["title"] = title or ""
                previous["subtitle"] = subtitle or ""
            previous["visible"] = bool(visible)
            self._state["lower_third"] = previous
            self._state["updated_by"] = updated_by
            self._state["updated_at"] = _utcnow_iso()
            state = copy.deepcopy(self._state)
        await self._broadcast(state)
        return state

    # ── system writes (AUTO mode only) ──────────────────────────

    async def system_publish(
        self,
        scene: Optional[str] = None,
        overlays: Optional[Dict[str, bool]] = None,
        updated_by: str = "system",
    ) -> Dict[str, Any]:
        """Publish a system/AI director decision.

        Applies ONLY in AUTO mode — in MANUAL mode this is a no-op so the
        simulator/AI can never override the operator. Broadcasts only when
        the visible state actually changed (no WS spam for no-op ticks).

        Returns ``{"changed": bool, "state": <snapshot>}``.
        """
        if scene is not None:
            scene = _validate_scene(scene)
        if overlays is not None:
            for key in overlays:
                if key not in ("scorebug", "killfeed"):
                    raise ValueError(
                        f"Unknown overlay {key!r}: must be 'scorebug'/'killfeed'."
                    )
        async with self._lock:
            if self._state["mode"] != "auto":
                return {"changed": False, "state": copy.deepcopy(self._state)}
            changed = False
            if scene is not None and scene != self._state["scene"]:
                self._state["scene"] = scene
                changed = True
            if overlays:
                for key, value in overlays.items():
                    if bool(value) != self._state["overlays"][key]:
                        self._state["overlays"][key] = bool(value)
                        changed = True
            if not changed:
                return {"changed": False, "state": copy.deepcopy(self._state)}
            self._state["updated_by"] = updated_by
            self._state["updated_at"] = _utcnow_iso()
            state = copy.deepcopy(self._state)
        await self._broadcast(state)
        return {"changed": True, "state": state}

    # ── internals ───────────────────────────────────────────────

    async def _broadcast(self, state: Dict[str, Any]) -> None:
        message = {"type": WS_MESSAGE_TYPE, "data": state}
        for listener in list(self._listeners):
            await listener(message)


#: Process-wide singleton used by the API router and app/main.py.
director_service = DirectorService()
