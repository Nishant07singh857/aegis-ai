# backend/app/services/director_service.py
"""
AEGIS AI — Director service.

The single source of truth for what is on the broadcast output page.
Holds the director state in memory (process-local, like MATCH_STATE):

    mode        "full_auto" | "semi_auto" | "manual"  (default "full_auto")
    scene       "main" | "standings" | "killfeed_focus" | "commentary"
                                                     (default "main")
    overlays    {"scorebug": bool, "killfeed": bool}  (default both true)
    lower_third {"visible": bool, "title": str, "subtitle": str}
                                                     (default hidden/empty)
    graphics    {"scorebug_template_id": str, "lowerthird_template_id": str,
                 "standings_template_id": str, "killfeed_template_id": str}
        Template ids for the Phase 8 graphics template gallery. Defaults:
        "scorebug-classic", "lowerthird-slide", "standings-table",
        "killfeed-ticker". These ids are the contract with the frontend
        team — they are never renamed.
    updated_by  str | None
    updated_at  ISO-8601 timestamp

Rules
-----
* FULL_AUTO mode: only the system (match simulator / AI logic in
  ``app/main.py``) may write, via :meth:`system_publish`. Operator API
  writes are rejected with :class:`DirectorModeError` (the API layer turns
  that into HTTP 409). The AI acts directly — it never creates
  suggestions in this mode.
* SEMI_AUTO mode: the AI never writes and never acts. It may *propose*
  via :meth:`propose_suggestion`; each proposal lands in a pending queue
  that an operator approves or rejects through the API. No suggestion
  ever auto-applies. Operator direct writes (scene/overlay/lower-third)
  are still rejected here — the approval queue is the operator's control
  surface in this mode. The one exception is graphics template selection
  (:meth:`set_graphics`), which is a direct operator write allowed in
  both SEMI_AUTO and MANUAL mode: the AI never chooses templates in
  semi-auto (it only suggests scene/overlay/lower-third content).
* MANUAL mode: operator API writes are authoritative;
  :meth:`system_publish` and :meth:`propose_suggestion` are both no-ops,
  so the simulator/AI is fully silent.
* Mode flips are explicit, via the director API. The legacy value
  ``"auto"`` is accepted by the mode API and normalised to
  ``"full_auto"`` — the string ``"auto"`` is never stored.

Suggestions
-----------
A suggestion is a dict::

    id          uuid4 hex
    type        "scene" | "overlay" | "lower_third"
    payload     {"scene": ...} |
                {"overlay": "scorebug"|"killfeed", "visible": bool} |
                {"visible": bool, "title": str, "subtitle": str}
    reason      human-readable, e.g. "Fight detected — ...; killfeed focus
                shows the action."
    confidence  0.0-1.0
    created_at  ISO-8601
    expires_at  ISO-8601 (default created_at + 60s)
    status      "pending" | "applied" | "rejected" | "expired"

Honesty rules: suggestions are only created while the mode is
``semi_auto``; duplicates of a pending suggestion and suggestions that
would be a no-op against the current state are skipped, so an idle AI
leaves the queue genuinely empty — nothing is fabricated. Expired
suggestions are pruned lazily (on list and on propose).

Broadcasting
------------
The service never imports ``app.main`` (that would be circular). Instead it
keeps a list of async listener callbacks; every state change publishes::

    {"type": "director.state", "data": {<full director state>}}

and every suggestion event (created / approved / rejected) publishes::

    {"type": "director.suggestion", "data": {<suggestion>}}

``app/main.py`` registers ``ConnectionManager.broadcast`` as a listener at
startup, which fans the messages out over ``/ws/live``.
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

DIRECTOR_MODES = ("full_auto", "semi_auto", "manual")
# Phase 18 (W3): "map_cam" is the tactical-overview scene measured in the
# Visual Broadcast Bible §2 (map cam + 2x2 facecams + persistent team panel).
# It is a valid scene for operator selection (MANUAL), AI system_publish
# (FULL_AUTO) and AI suggestions (SEMI_AUTO) — and it is the only scene
# with a hard maximum hold (see MAP_CAM_MAX_HOLD_SECONDS).
DIRECTOR_SCENES = ("main", "standings", "killfeed_focus", "commentary", "map_cam")

#: Visual Broadcast Bible §2 (map cam rules): map cam is used for 15-30
#: seconds at a time, then a hard cut back to action. The director
#: enforces the 30s maximum in every mode — see enforce_scene_holds().
#: The 15s figure is the observed typical hold, not a lock: if a fight
#: starts, the director cuts back to gameplay immediately (Bible §3).
MAP_CAM_SCENE = "map_cam"
MAP_CAM_MAX_HOLD_SECONDS = 30.0

#: Graphics template ids per graphics type (Phase 8). These ids are the
#: contract with the frontend team — the graphics template gallery and the
#: director console share exactly these strings. Do not rename them.
SCOREBUG_TEMPLATES = (
    "scorebug-classic",
    "scorebug-modern",
    "scorebug-minimal",
)
LOWER_THIRD_TEMPLATES = (
    "lowerthird-slide",
    "lowerthird-rise",
    "lowerthird-banner",
)
STANDINGS_TEMPLATES = (
    "standings-table",
    "standings-cards",
    "standings-mvp",
    "standings-eliminator",
    "standings-igl",
    "standings-finals-mvp",
    "standings-spotlight",
)
KILLFEED_TEMPLATES = (
    "killfeed-ticker",
    "killfeed-stack",
)

#: Default template selected for each graphics type in a fresh state.
GRAPHICS_TEMPLATE_DEFAULTS = {
    "scorebug_template_id": "scorebug-classic",
    "lowerthird_template_id": "lowerthird-slide",
    "standings_template_id": "standings-table",
    "killfeed_template_id": "killfeed-ticker",
}

#: All valid template ids per graphics type, for validation.
GRAPHICS_TEMPLATE_OPTIONS = {
    "scorebug_template_id": SCOREBUG_TEMPLATES,
    "lowerthird_template_id": LOWER_THIRD_TEMPLATES,
    "standings_template_id": STANDINGS_TEMPLATES,
    "killfeed_template_id": KILLFEED_TEMPLATES,
}

#: Legacy mode value accepted by the mode API and normalised to "full_auto".
#: Never stored in state.
LEGACY_AUTO_MODE = "auto"

#: Suggestion types the AI may propose.
SUGGESTION_TYPES = ("scene", "overlay", "lower_third")

#: Default suggestion lifetime, in seconds.
SUGGESTION_TTL_SECONDS = 60.0

#: Upper bound on retained suggestion history (pending + decided).
MAX_SUGGESTION_HISTORY = 200

#: WebSocket message types.
WS_MESSAGE_TYPE = "director.state"
WS_SUGGESTION_TYPE = "director.suggestion"

#: Signature of a broadcast listener: async callable taking the message dict.
BroadcastListener = Callable[[Dict[str, Any]], Awaitable[None]]


class DirectorModeError(RuntimeError):
    """Operator write attempted while the director is not in MANUAL mode."""


class SuggestionNotFoundError(KeyError):
    """No pending director suggestion with the given id (unknown, decided
    or expired)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _default_state() -> Dict[str, Any]:
    return {
        "mode": "full_auto",
        "scene": "main",
        # Phase 18 (W3): ISO timestamp of the last scene change. Drives the
        # map-cam 30s maximum hold (enforce_scene_holds) and the frontend
        # auto-return countdown. Stamped only when the scene actually
        # changes, so re-publishing the same scene never extends the hold.
        "scene_entered_at": _utcnow_iso(),
        "overlays": {"scorebug": True, "killfeed": True},
        "lower_third": {"visible": False, "title": "", "subtitle": ""},
        "graphics": dict(GRAPHICS_TEMPLATE_DEFAULTS),
        # Phase 17 W2: active observer camera slot (1..16) driving the
        # broadcast output video layer (GET /api/v1/video/feed/{slot}).
        "active_camera": 1,
        "updated_by": None,
        "updated_at": _utcnow_iso(),
    }


def _validate_mode(mode: str) -> str:
    mode = (mode or "").strip().lower()
    # Backward compatibility: the Phase 6 "auto" value is the old name of
    # full_auto. Normalise it here so "auto" is never stored.
    if mode == LEGACY_AUTO_MODE:
        mode = "full_auto"
    if mode not in DIRECTOR_MODES:
        raise ValueError(
            f"Invalid director mode {mode!r}: must be one of "
            f"{', '.join(DIRECTOR_MODES)} (legacy 'auto' is accepted as "
            "'full_auto')."
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


#: Observer camera slots on the broadcast video layer.
MAX_CAMERA_SLOT = 16


def _validate_camera(slot: object) -> int:
    """Validate an observer camera slot (Phase 17 W2)."""
    try:
        n = int(slot)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(
            f"Invalid camera slot {slot!r}: must be an integer "
            f"1..{MAX_CAMERA_SLOT}."
        )
    if not 1 <= n <= MAX_CAMERA_SLOT:
        raise ValueError(
            f"Invalid camera slot {n}: must be 1..{MAX_CAMERA_SLOT}."
        )
    return n


def _validate_graphics_template(template_type: str, template_id: str) -> str:
    """Validate a graphics template id against its type's known ids."""
    options = GRAPHICS_TEMPLATE_OPTIONS[template_type]
    template_id = (template_id or "").strip()
    if template_id not in options:
        raise ValueError(
            f"Invalid template id {template_id!r} for {template_type}: "
            f"must be one of {', '.join(options)}."
        )
    return template_id


def _validate_suggestion_type(suggestion_type: str) -> str:
    suggestion_type = (suggestion_type or "").strip().lower()
    if suggestion_type not in SUGGESTION_TYPES:
        raise ValueError(
            f"Invalid suggestion type {suggestion_type!r}: must be one of "
            f"{', '.join(SUGGESTION_TYPES)}."
        )
    return suggestion_type


def _validate_suggestion_payload(
    suggestion_type: str, payload: Any
) -> Dict[str, Any]:
    """Validate + normalise an AI suggestion payload for its type."""
    if not isinstance(payload, dict):
        raise ValueError("Suggestion payload must be an object.")
    if suggestion_type == "scene":
        return {"scene": _validate_scene(payload.get("scene", ""))}
    if suggestion_type == "overlay":
        overlay = (payload.get("overlay") or "").strip().lower()
        if overlay not in ("scorebug", "killfeed"):
            raise ValueError(
                f"Invalid overlay {overlay!r}: must be 'scorebug' or "
                "'killfeed'."
            )
        return {"overlay": overlay, "visible": bool(payload.get("visible"))}
    # lower_third
    return {
        "visible": bool(payload.get("visible")),
        "title": str(payload.get("title") or ""),
        "subtitle": str(payload.get("subtitle") or ""),
    }


class DirectorService:
    """In-memory director state with mode-gated writes and WS fan-out."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._listeners: List[BroadcastListener] = []
        self._state: Dict[str, Any] = _default_state()
        #: All suggestions ever proposed, keyed by id (pending + decided
        #: history, capped at MAX_SUGGESTION_HISTORY).
        self._suggestions: Dict[str, Dict[str, Any]] = {}

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
        """Return a deep copy of the current director state."""
        return copy.deepcopy(self._state)

    # ── operator writes ────────────────────────────────────────────
    # scene/overlays/lower-third are MANUAL mode only; set_graphics is
    # allowed in MANUAL and SEMI_AUTO (template choice is a direct
    # operator write in both); set_mode is allowed in any mode.

    async def set_mode(
        self, mode: str, updated_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """Explicit mode transition. Allowed in any mode.

        The legacy value ``"auto"`` is normalised to ``"full_auto"``.
        No-op when the effective mode is already active: returns the
        current state without touching updated_at/updated_by or fanning
        out a WS frame.
        """
        mode = _validate_mode(mode)
        if mode == self._state["mode"]:
            return copy.deepcopy(self._state)
        async with self._lock:
            self._state["mode"] = mode
            self._state["updated_by"] = updated_by
            self._state["updated_at"] = _utcnow_iso()
            state = copy.deepcopy(self._state)
        await self._broadcast({"type": WS_MESSAGE_TYPE, "data": state})
        return state

    def _require_manual(self) -> None:
        if self._state["mode"] != "manual":
            raise DirectorModeError(
                "Director is not in MANUAL mode: operator scene/overlay/"
                "lower-third writes are rejected. In SEMI-AUTO, approve AI "
                "suggestions instead; switch to MANUAL for direct control "
                "via POST /api/v1/director/mode {\"mode\": \"manual\"}."
            )

    async def set_camera(
        self, slot: object, updated_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """Operator camera select (Phase 17 W2). Rejected unless MANUAL mode.

        Switches the active observer camera shown on the broadcast output
        video layer (GET /api/v1/video/feed/{slot}).
        """
        slot = _validate_camera(slot)
        async with self._lock:
            self._require_manual()
            self._state["active_camera"] = slot
            self._state["updated_by"] = updated_by
            self._state["updated_at"] = _utcnow_iso()
            state = copy.deepcopy(self._state)
        await self._broadcast({"type": WS_MESSAGE_TYPE, "data": state})
        return state

    async def set_scene(
        self, scene: str, updated_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """Operator scene select. Rejected unless MANUAL mode."""
        scene = _validate_scene(scene)
        async with self._lock:
            self._require_manual()
            self._apply_scene(scene, updated_by)
            state = copy.deepcopy(self._state)
        await self._broadcast({"type": WS_MESSAGE_TYPE, "data": state})
        return state

    async def set_overlays(
        self,
        scorebug: Optional[bool] = None,
        killfeed: Optional[bool] = None,
        updated_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Operator overlay toggles (partial update). Rejected unless MANUAL."""
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
        await self._broadcast({"type": WS_MESSAGE_TYPE, "data": state})
        return state

    async def set_lower_third(
        self,
        visible: bool,
        title: str = "",
        subtitle: str = "",
        updated_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Operator lower-third control. Rejected unless MANUAL.

        Hiding (visible=False) preserves the current title/subtitle so the
        composer's text survives a show/hide cycle; they are only replaced
        when explicitly shown with new text.
        """
        async with self._lock:
            self._require_manual()
            self._apply_lower_third(visible=visible, title=title,
                                    subtitle=subtitle, updated_by=updated_by)
            state = copy.deepcopy(self._state)
        await self._broadcast({"type": WS_MESSAGE_TYPE, "data": state})
        return state

    def _require_graphics_mode(self) -> None:
        if self._state["mode"] == "full_auto":
            raise DirectorModeError(
                "Director is in FULL_AUTO mode: graphics template selection "
                "is an operator write and is rejected here. Switch to "
                "SEMI-AUTO or MANUAL first via POST /api/v1/director/mode."
            )

    async def set_graphics(
        self,
        scorebug_template_id: Optional[str] = None,
        lowerthird_template_id: Optional[str] = None,
        standings_template_id: Optional[str] = None,
        killfeed_template_id: Optional[str] = None,
        updated_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Operator graphics template selection (partial update).

        Allowed in MANUAL and SEMI_AUTO mode: template choice is a direct
        operator write in both — the AI never selects templates itself
        (unlike scene/overlay/lower-third, which are MANUAL-only). Rejected
        with :class:`DirectorModeError` in FULL_AUTO mode, where the AI
        drives the output.
        """
        provided = {
            "scorebug_template_id": scorebug_template_id,
            "lowerthird_template_id": lowerthird_template_id,
            "standings_template_id": standings_template_id,
            "killfeed_template_id": killfeed_template_id,
        }
        if all(value is None for value in provided.values()):
            raise ValueError(
                "Nothing to update: provide at least one of "
                "'scorebug_template_id', 'lowerthird_template_id', "
                "'standings_template_id' or 'killfeed_template_id'."
            )
        validated = {
            key: _validate_graphics_template(key, value)
            for key, value in provided.items()
            if value is not None
        }
        async with self._lock:
            self._require_graphics_mode()
            self._state["graphics"].update(validated)
            self._state["updated_by"] = updated_by
            self._state["updated_at"] = _utcnow_iso()
            state = copy.deepcopy(self._state)
        await self._broadcast({"type": WS_MESSAGE_TYPE, "data": state})
        return state

    # ── system writes (FULL_AUTO mode only) ─────────────────────

    async def system_publish(
        self,
        scene: Optional[str] = None,
        overlays: Optional[Dict[str, bool]] = None,
        active_camera: Optional[object] = None,
        updated_by: str = "system",
    ) -> Dict[str, Any]:
        """Publish a system/AI director decision.

        Applies ONLY in FULL_AUTO mode — in SEMI_AUTO and MANUAL mode this
        is a no-op so the simulator/AI can never override the operator
        (in SEMI_AUTO the AI proposes suggestions instead of writing).
        Broadcasts only when the visible state actually changed (no WS
        spam for no-op ticks).

        ``active_camera`` (Phase 17 W2) lets the AI switch observer
        cameras in FULL_AUTO; validated as an integer slot 1..16.

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
        camera: Optional[int] = None
        if active_camera is not None:
            camera = _validate_camera(active_camera)
        async with self._lock:
            if self._state["mode"] != "full_auto":
                return {"changed": False, "state": copy.deepcopy(self._state)}
            changed = False
            if scene is not None and scene != self._state["scene"]:
                self._apply_scene(scene, updated_by)
                changed = True
            if overlays:
                for key, value in overlays.items():
                    if bool(value) != self._state["overlays"][key]:
                        self._state["overlays"][key] = bool(value)
                        changed = True
            if camera is not None and camera != self._state.get("active_camera"):
                self._state["active_camera"] = camera
                changed = True
            if not changed:
                return {"changed": False, "state": copy.deepcopy(self._state)}
            self._state["updated_by"] = updated_by
            self._state["updated_at"] = _utcnow_iso()
            state = copy.deepcopy(self._state)
        await self._broadcast({"type": WS_MESSAGE_TYPE, "data": state})
        return {"changed": True, "state": state}

    # ── AI suggestions (SEMI_AUTO mode only) ────────────────────

    def list_suggestions(self) -> List[Dict[str, Any]]:
        """Return the pending (non-expired) suggestions, oldest first.

        Expired suggestions are pruned first (marked "expired", dropped
        from the queue) — the lazy janitor.
        """
        self._prune_expired()
        pending = [
            s for s in self._suggestions.values() if s["status"] == "pending"
        ]
        pending.sort(key=lambda s: s["created_at"])
        return copy.deepcopy(pending)

    async def propose_suggestion(
        self,
        suggestion_type: str,
        payload: Dict[str, Any],
        reason: str,
        confidence: float,
        ttl_seconds: float = SUGGESTION_TTL_SECONDS,
        created_by: str = "director_ai",
    ) -> Optional[Dict[str, Any]]:
        """Propose an AI suggestion. SEMI_AUTO mode only.

        Outside SEMI_AUTO this is a silent no-op (FULL_AUTO: the AI acts
        directly via :meth:`system_publish`; MANUAL: the AI is silent).

        The suggestion is skipped — returning ``None`` — when it is a
        duplicate of a still-pending suggestion or a no-op against the
        current state, so the queue stays honestly empty when the AI has
        nothing genuine to propose.

        Returns the created (or deduplicated) suggestion, or ``None``.
        """
        suggestion_type = _validate_suggestion_type(suggestion_type)
        payload = _validate_suggestion_payload(suggestion_type, payload)
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("Suggestion reason must be a non-empty string.")
        confidence = max(0.0, min(1.0, float(confidence)))
        ttl_seconds = max(1.0, float(ttl_seconds))

        async with self._lock:
            self._prune_expired()
            if self._state["mode"] != "semi_auto":
                return None
            # Dedupe: an identical pending suggestion already covers this.
            for existing in self._suggestions.values():
                if (
                    existing["status"] == "pending"
                    and existing["type"] == suggestion_type
                    and existing["payload"] == payload
                ):
                    return copy.deepcopy(existing)
            # No-op guard: never suggest what is already on air.
            if self._payload_is_noop(suggestion_type, payload):
                return None
            now = _utcnow()
            suggestion = {
                "id": uuid.uuid4().hex,
                "type": suggestion_type,
                "payload": payload,
                "reason": reason,
                "confidence": confidence,
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
                "status": "pending",
                "created_by": created_by,
            }
            self._suggestions[suggestion["id"]] = suggestion
            self._cap_history()
            outgoing = copy.deepcopy(suggestion)
        await self._broadcast(
            {"type": WS_SUGGESTION_TYPE, "data": outgoing}
        )
        return outgoing

    async def approve_suggestion(
        self, suggestion_id: str, updated_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """Apply a pending suggestion exactly as a manual operator write.

        The payload goes through the same mutation path as the operator
        scene/overlay/lower-third commands (without the MANUAL-mode gate),
        then the suggestion is marked "applied" and removed from the queue.
        Rejected in FULL_AUTO mode (the AI is driving there — switch to
        SEMI-AUTO or MANUAL first).
        """
        async with self._lock:
            suggestion = self._pending_or_raise(suggestion_id)
            if self._state["mode"] == "full_auto":
                raise DirectorModeError(
                    "Director is in FULL_AUTO mode: the AI is driving, so "
                    "suggestion approvals are rejected. Switch to SEMI-AUTO "
                    "or MANUAL first via POST /api/v1/director/mode."
                )
            self._apply_suggestion_payload(
                suggestion["type"], suggestion["payload"], updated_by
            )
            suggestion["status"] = "applied"
            suggestion["decided_at"] = _utcnow_iso()
            suggestion["decided_by"] = updated_by
            outgoing = copy.deepcopy(suggestion)
            state = copy.deepcopy(self._state)
        await self._broadcast({"type": WS_MESSAGE_TYPE, "data": state})
        await self._broadcast({"type": WS_SUGGESTION_TYPE, "data": outgoing})
        return outgoing

    async def reject_suggestion(
        self, suggestion_id: str, updated_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """Drop a pending suggestion: marked "rejected", logged, removed."""
        async with self._lock:
            suggestion = self._pending_or_raise(suggestion_id)
            suggestion["status"] = "rejected"
            suggestion["decided_at"] = _utcnow_iso()
            suggestion["decided_by"] = updated_by
            outgoing = copy.deepcopy(suggestion)
        await self._broadcast({"type": WS_SUGGESTION_TYPE, "data": outgoing})
        return outgoing

    # ── scene hold enforcement (Phase 18, W3) ────────────────────

    async def enforce_scene_holds(self) -> bool:
        """Enforce the map-cam maximum hold (Visual Broadcast Bible §2).

        When the current scene is ``map_cam`` and it has been on air for
        >= :data:`MAP_CAM_MAX_HOLD_SECONDS`, the director hard-cuts back
        to the ``main`` (gameplay) scene and broadcasts the change. Works
        in every director mode — FULL_AUTO included — because the Bible
        rule is unconditional: map cam is a 15-30s tactical beat, never a
        resting state.

        Safe to call on any cadence (background ticker, REST reads): it is
        a no-op unless the hold has actually expired. Returns True when a
        revert happened.
        """
        async with self._lock:
            elapsed = self._map_cam_hold_elapsed()
            if elapsed is None or elapsed < MAP_CAM_MAX_HOLD_SECONDS:
                return False
            self._apply_scene("main", updated_by="director:auto-mapcam-timeout")
            state = copy.deepcopy(self._state)
        await self._broadcast({"type": WS_MESSAGE_TYPE, "data": state})
        return True

    def map_cam_hold_remaining(self) -> Optional[float]:
        """Seconds left before the map-cam hold expires, or None when the
        current scene is not map_cam. Pure read (no lock needed beyond the
        GIL for this in-memory state)."""
        elapsed = self._map_cam_hold_elapsed()
        if elapsed is None:
            return None
        return max(0.0, MAP_CAM_MAX_HOLD_SECONDS - elapsed)

    # ── internals ───────────────────────────────────────────────

    def _pending_or_raise(self, suggestion_id: str) -> Dict[str, Any]:
        suggestion = self._suggestions.get(suggestion_id or "")
        if suggestion is None or suggestion["status"] != "pending":
            raise SuggestionNotFoundError(
                f"No pending suggestion with id {suggestion_id!r}."
            )
        try:
            expires_at = datetime.fromisoformat(suggestion["expires_at"])
        except (ValueError, TypeError):
            expires_at = _utcnow()
        if expires_at <= _utcnow():
            suggestion["status"] = "expired"
            raise SuggestionNotFoundError(
                f"Suggestion {suggestion_id!r} has expired."
            )
        return suggestion

    def _prune_expired(self) -> None:
        """Mark expired pending suggestions "expired" (dropped from queue).

        Callers must hold the lock. Expired records stay in the capped
        history for audit; only "pending" ones are listed.
        """
        now = _utcnow()
        for suggestion in self._suggestions.values():
            if suggestion["status"] != "pending":
                continue
            try:
                expires_at = datetime.fromisoformat(suggestion["expires_at"])
            except (ValueError, TypeError):
                expires_at = now
            if expires_at <= now:
                suggestion["status"] = "expired"

    def _cap_history(self) -> None:
        """Keep the suggestion history bounded. Callers must hold the lock."""
        while len(self._suggestions) > MAX_SUGGESTION_HISTORY:
            oldest_id = None
            oldest_at = None
            for sid, suggestion in self._suggestions.items():
                if suggestion["status"] == "pending":
                    continue
                if oldest_at is None or suggestion["created_at"] < oldest_at:
                    oldest_at = suggestion["created_at"]
                    oldest_id = sid
            if oldest_id is None:
                break  # only pending left; keep them all
            del self._suggestions[oldest_id]

    def _apply_scene(self, scene: str, updated_by: Optional[str]) -> None:
        """Scene mutation shared by operator writes, system publishes and
        suggestion approvals. Callers must hold the lock.

        ``scene_entered_at`` is stamped only when the scene actually
        changes: re-publishing the current scene (e.g. a repeated AI tick)
        never extends a hold — this is what makes the map-cam 30s maximum
        airtight.
        """
        if scene != self._state.get("scene"):
            self._state["scene"] = scene
            self._state["scene_entered_at"] = _utcnow_iso()
        self._state["updated_by"] = updated_by
        self._state["updated_at"] = _utcnow_iso()

    def _map_cam_hold_elapsed(self) -> Optional[float]:
        """Seconds since the current scene was entered, or None when the
        current scene is not map_cam. Malformed stamps fail closed (treated
        as expired so the hold can never run unbounded)."""
        if self._state.get("scene") != MAP_CAM_SCENE:
            return None
        try:
            entered = datetime.fromisoformat(self._state["scene_entered_at"])
        except (ValueError, TypeError, KeyError):
            return MAP_CAM_MAX_HOLD_SECONDS
        return (_utcnow() - entered).total_seconds()

    def _payload_is_noop(
        self, suggestion_type: str, payload: Dict[str, Any]
    ) -> bool:
        """True when the suggestion would change nothing visible."""
        if suggestion_type == "scene":
            return payload["scene"] == self._state["scene"]
        if suggestion_type == "overlay":
            return (
                self._state["overlays"].get(payload["overlay"])
                == payload["visible"]
            )
        # lower_third
        current = self._state["lower_third"]
        if not payload["visible"] and not current["visible"]:
            return True
        return (
            bool(payload["visible"]) == bool(current["visible"])
            and payload["title"] == current["title"]
            and payload["subtitle"] == current["subtitle"]
        )

    def _apply_suggestion_payload(
        self,
        suggestion_type: str,
        payload: Dict[str, Any],
        updated_by: Optional[str],
    ) -> None:
        """Apply a suggestion payload via the manual-command mutation path.

        Same state mutations as the operator commands, without the
        MANUAL-mode gate. Callers must hold the lock.
        """
        if suggestion_type == "scene":
            self._apply_scene(payload["scene"], updated_by)
        elif suggestion_type == "overlay":
            self._state["overlays"][payload["overlay"]] = bool(
                payload["visible"]
            )
        else:  # lower_third
            self._apply_lower_third(
                visible=payload["visible"],
                title=payload["title"],
                subtitle=payload["subtitle"],
                updated_by=updated_by,
            )
            return
        self._state["updated_by"] = updated_by
        self._state["updated_at"] = _utcnow_iso()

    def _apply_lower_third(
        self,
        visible: bool,
        title: str,
        subtitle: str,
        updated_by: Optional[str],
    ) -> None:
        """Lower-third mutation shared by operator writes and approvals.

        Hiding preserves the current title/subtitle; they are replaced
        only when explicitly shown with new text. Callers hold the lock.
        """
        previous = dict(self._state["lower_third"])
        if visible:
            previous["title"] = title or ""
            previous["subtitle"] = subtitle or ""
        previous["visible"] = bool(visible)
        self._state["lower_third"] = previous
        self._state["updated_by"] = updated_by
        self._state["updated_at"] = _utcnow_iso()

    async def _broadcast(self, message: Dict[str, Any]) -> None:
        for listener in list(self._listeners):
            await listener(message)


#: Process-wide singleton used by the API router and app/main.py.
director_service = DirectorService()
