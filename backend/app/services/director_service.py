# backend/app/services/director_service.py
"""
AEGIS AI — Director service.

The single source of truth for what is on the broadcast output page.
Holds the director state in memory (process-local, like MATCH_STATE):

    mode        "full_auto" | "semi_auto" | "manual" | "clone"
                (default "semi_auto" — see DEFAULT_DIRECTOR_MODE;
                "clone" = Director-Style mode,
                Phase 27: the AI fires like full_auto but with the
                LEARNED knob profile; a human may grab manual control
                inside clone — see grab_clone_control)
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
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

DIRECTOR_MODES = ("full_auto", "semi_auto", "manual", "clone")
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
    # Phase 31: ELIMINATED banner template (frontend registry
    # already carries this id; the backend contract must too, or
    # set_graphics validation would reject the operator's choice).
    "lowerthird-eliminated",
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

#: Canonical default director mode for a FRESH show (Phase 28) — the ONE
#: place the default is defined; every other site resolves through
#: :func:`default_director_mode` (or mirrors it, in the frontend).
#: Rationale: AI salaah deta hai, insaan ka aakhri faisla — jab tak Clone
#: ke paas seekha hua data na ho, naya show semi_auto se shuru ho.
DEFAULT_DIRECTOR_MODE = "semi_auto"

#: Environment override for the fresh-show default (any DIRECTOR_MODES
#: value; the legacy "auto" is normalised like everywhere else). An
#: invalid value is ignored — the canonical default applies.
DIRECTOR_DEFAULT_MODE_ENV = "AEGIS_DIRECTOR_DEFAULT_MODE"


def default_director_mode() -> str:
    """Resolve the fresh-show default director mode.

    Reads ``AEGIS_DIRECTOR_DEFAULT_MODE`` at call time (so a fresh
    :class:`DirectorService` honours the operator's environment), and
    falls back to :data:`DEFAULT_DIRECTOR_MODE`. This governs ONLY the
    default when nothing has been set: an explicitly chosen/stored
    mode is never overridden by this function.
    """
    raw = (os.environ.get(DIRECTOR_DEFAULT_MODE_ENV) or "").strip().lower()
    if raw == LEGACY_AUTO_MODE:
        raw = "full_auto"
    if raw in DIRECTOR_MODES:
        return raw
    return DEFAULT_DIRECTOR_MODE

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

# ── Phase 27 (W1): learning observation sinks ─────────────────────
# Output-write sinks observe REAL program-output changes (scene /
# active_camera) on the write path, tagged with the writing method
# (``source``). That tag is the human-vs-system distinguisher the
# learning loop relies on: operator writes come only through
# set_camera/set_scene (MANUAL-gated) and approve_suggestion (a human
# decision), while AI writes come only through system_publish.
# Suggestion fates are forwarded to the journal's fate sink (see
# app/services/director_learning/journal.py). All notification is
# failure-isolated: a broken sink never breaks a director write, and
# with no sink registered every notify below is a no-op.

#: Signature of an output-write sink: sync callable taking the event dict.
OutputWriteSink = Callable[[Dict[str, Any]], None]

_output_write_sinks: List[OutputWriteSink] = []


def register_output_write_sink(fn: OutputWriteSink) -> None:
    """Register a sync callable observing real output writes."""
    if fn not in _output_write_sinks:
        _output_write_sinks.append(fn)


def unregister_output_write_sink(fn: OutputWriteSink) -> None:
    """Unregister a previously registered output-write sink."""
    if fn in _output_write_sinks:
        _output_write_sinks.remove(fn)


def _notify_output_write(event: Dict[str, Any]) -> None:
    for fn in list(_output_write_sinks):
        try:
            fn(event)
        except Exception:  # a broken sink must never break a write
            pass


def _notify_fate(fate: Dict[str, Any]) -> None:
    try:
        from app.services.director_learning.journal import notify_fate

        notify_fate(fate)
    except Exception:  # pragma: no cover - defensive
        pass


def _notify_transition(entry: Dict[str, Any]) -> None:
    """Phase 27 (W3): journal-log a mode/grab/release transition as a
    synthetic decision entry (outcome='mode_transition'). The labeler
    ignores non-decision outcomes by design. Failure-isolated."""
    try:
        from app.services.director_learning.journal import notify_decision

        notify_decision(entry)
    except Exception:  # pragma: no cover - defensive
        pass


def _transition_entry(
    *, trigger: str, rule: str, reason: str, mode: str
) -> Dict[str, Any]:
    return {
        "timestamp": _utcnow_iso(),
        "trigger": trigger,
        "mode": mode,
        "phase": None,
        "candidates": [],
        "incumbent": None,
        "challenger": None,
        "outcome": "mode_transition",
        "rule": rule,
        "reason": reason,
        "margin_required": None,
        "margin_actual": None,
        "suggestion_id": None,
    }


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
        "mode": default_director_mode(),
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
        # Phase 25 (W1, P0-0/P0-3): PVW preview — the live director's top
        # candidate, pre-positioned for the next cut. Shape (stable, W4's
        # multiview UI builds on exactly this):
        #   {"slot": int, "scene": str, "team_id": str|None,
        #    "fight_id": str|None, "reason": str, "score": float,
        #    "breakdown": {term: float}, "updated_at": ISO-8601}
        # None when the live director has no candidate. Preview is NOT
        # program output: it never changes scene/active_camera by itself.
        "preview": None,
        # Phase 27 (W3): clone-mode manual grab. While grabbed, the
        # human drives (set_camera/set_scene allowed) and the AI is
        # silent — exactly like manual — until explicit release or
        # the inactivity auto-release (LiveDirectorConfig
        # clone_grab_inactivity_s, enforced on the LiveDirector tick).
        "clone_grabbed": False,
        "clone_grabbed_by": None,
        "clone_grabbed_at": None,
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
        validated: Dict[str, Any] = {
            "scene": _validate_scene(payload.get("scene", ""))
        }
        # Phase 25 (integration): a scene suggestion may carry the
        # camera slot it intends to put on program — the live
        # director's semi_auto proposals do, so that APPROVING the
        # suggestion applies scene AND camera as one operator-
        # equivalent write (previously the camera could not ride a
        # suggestion at all and the operator had to set it by hand).
        # Absent = scene-only, exactly the historical behaviour.
        if payload.get("active_camera") is not None:
            validated["active_camera"] = _validate_camera(
                payload["active_camera"]
            )
        if payload.get("team_id") is not None:
            validated["team_id"] = str(payload["team_id"])
        return validated
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
        #: Phase 31: last phase for which phase suggestions were
        #: proposed (edge trigger for the production caller in
        #: app/main.py — a phase proposes once, on entry, not on
        #: every ticker tick).
        self._last_phase_suggestion_phase: Optional[str] = None

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
        from_mode = self._state["mode"]
        async with self._lock:
            self._state["mode"] = mode
            if mode != "clone":
                # Leaving clone drops any manual grab with it.
                self._state["clone_grabbed"] = False
                self._state["clone_grabbed_by"] = None
                self._state["clone_grabbed_at"] = None
            self._state["updated_by"] = updated_by
            self._state["updated_at"] = _utcnow_iso()
            state = copy.deepcopy(self._state)
        # Phase 27 (W3): mode changes to/from clone (and all mode
        # changes) reach the decision journal as synthetic entries.
        _notify_transition(
            _transition_entry(
                trigger="mode_change",
                rule=f"mode_{from_mode}_to_{mode}",
                reason=(
                    f"Director mode changed from {from_mode} to {mode} "
                    f"by {updated_by}."
                ),
                mode=mode,
            )
        )
        await self._broadcast({"type": WS_MESSAGE_TYPE, "data": state})
        return state

    def register_output_write_sink(self, fn: OutputWriteSink) -> None:
        """Instance convenience for the module-level sink registry
        (W1's journal builder looks this up on the service)."""
        register_output_write_sink(fn)

    def _require_manual(self) -> None:
        # MANUAL always allows operator writes; CLONE allows them
        # only while the human has grabbed control (Phase 27, W3).
        if self._state["mode"] == "manual":
            return
        if (
            self._state["mode"] == "clone"
            and self._state.get("clone_grabbed")
        ):
            return
        raise DirectorModeError(
            "Director is not in MANUAL mode: operator scene/overlay/"
            "lower-third writes are rejected. In SEMI-AUTO, approve AI "
            "suggestions instead; switch to MANUAL for direct control "
            "via POST /api/v1/director/mode {\"mode\": \"manual\"}. "
            "In CLONE mode, grab control first."
        )

    # ── clone-mode manual grab (Phase 27, W3) ─────────────────────

    async def grab_clone_control(
        self, updated_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """Grab manual control inside CLONE mode. Instant; clone-only.

        While grabbed the LiveDirector behaves exactly like manual
        (never writes) but keeps shadow-logging with rule
        'clone_grabbed_no_write'. Idempotent when already grabbed.
        """
        async with self._lock:
            if self._state["mode"] != "clone":
                raise DirectorModeError(
                    "Clone grab is only valid in CLONE mode "
                    f"(current mode: {self._state['mode']})."
                )
            already = bool(self._state.get("clone_grabbed"))
            self._state["clone_grabbed"] = True
            self._state["clone_grabbed_by"] = updated_by
            self._state["clone_grabbed_at"] = _utcnow_iso()
            self._state["updated_by"] = updated_by
            self._state["updated_at"] = _utcnow_iso()
            state = copy.deepcopy(self._state)
        if not already:
            _notify_transition(
                _transition_entry(
                    trigger="grab",
                    rule="clone_grabbed",
                    reason=(
                        f"Clone control grabbed by {updated_by}: the "
                        "human now drives; the AI is silent (manual "
                        "semantics) until release."
                    ),
                    mode="clone",
                )
            )
        await self._broadcast({"type": WS_MESSAGE_TYPE, "data": state})
        return state

    async def release_clone_control(
        self,
        updated_by: Optional[str] = None,
        reason: str = "explicit",
    ) -> Dict[str, Any]:
        """Release a clone grab ('explicit' or 'inactivity').

        No-op (returns current state) when not grabbed. After release
        the clone director resumes firing with the learned profile.
        """
        if reason not in ("explicit", "inactivity"):
            raise ValueError(
                f"Invalid release reason {reason!r}: must be "
                "'explicit' or 'inactivity'."
            )
        async with self._lock:
            if not self._state.get("clone_grabbed"):
                return copy.deepcopy(self._state)
            grabbed_by = self._state.get("clone_grabbed_by")
            self._state["clone_grabbed"] = False
            self._state["clone_grabbed_by"] = None
            self._state["clone_grabbed_at"] = None
            self._state["updated_by"] = updated_by
            self._state["updated_at"] = _utcnow_iso()
            state = copy.deepcopy(self._state)
        _notify_transition(
            _transition_entry(
                trigger="release",
                rule=f"clone_released_{reason}",
                reason=(
                    f"Clone control released ({reason}) by {updated_by}; "
                    f"was grabbed by {grabbed_by}. The clone director "
                    "resumes with the learned profile."
                ),
                mode=state["mode"],
            )
        )
        await self._broadcast({"type": WS_MESSAGE_TYPE, "data": state})
        return state

    async def set_camera(
        self, slot: object, updated_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """Operator camera select (Phase 17 W2). Rejected unless MANUAL mode.

        Switches the active observer camera shown on the broadcast output
        video layer (GET /api/v1/video/feed/{slot}).
        """
        slot = _validate_camera(slot)
        prev_camera = self._state.get("active_camera")
        async with self._lock:
            self._require_manual()
            self._state["active_camera"] = slot
            self._state["updated_by"] = updated_by
            self._state["updated_at"] = _utcnow_iso()
            state = copy.deepcopy(self._state)
        if slot != prev_camera:
            # Phase 27 (W1): a REAL human camera change (manual-gated
            # operator write) — observed by the learning shadow.
            _notify_output_write(
                {
                    "source": "set_camera",
                    "scene": state["scene"],
                    "active_camera": state["active_camera"],
                    "updated_by": updated_by,
                    "mode": state["mode"],
                }
            )
        await self._broadcast({"type": WS_MESSAGE_TYPE, "data": state})
        return state

    async def set_scene(
        self, scene: str, updated_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """Operator scene select. Rejected unless MANUAL mode."""
        scene = _validate_scene(scene)
        prev_scene = self._state.get("scene")
        async with self._lock:
            self._require_manual()
            self._apply_scene(scene, updated_by)
            state = copy.deepcopy(self._state)
        if state["scene"] != prev_scene:
            # Phase 27 (W1): a REAL human scene change (manual-gated).
            _notify_output_write(
                {
                    "source": "set_scene",
                    "scene": state["scene"],
                    "active_camera": state["active_camera"],
                    "updated_by": updated_by,
                    "mode": state["mode"],
                }
            )
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
        Phase 27 (W3): CLONE mode also publishes, but only while NOT
        grabbed; a grabbed clone is human-driven, so this no-ops.
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
            # FULL_AUTO always publishes. CLONE publishes only while
            # NOT grabbed (Phase 27, W3): a grabbed clone is under
            # manual human control, so the AI path no-ops exactly as
            # it does in manual/semi_auto.
            _mode = self._state["mode"]
            _publishable = _mode == "full_auto" or (
                _mode == "clone" and not self._state.get("clone_grabbed")
            )
            if not _publishable:
                return {"changed": False, "state": copy.deepcopy(self._state)}
            prev_scene = self._state.get("scene")
            prev_camera = self._state.get("active_camera")
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
        if (
            state["scene"] != prev_scene
            or state.get("active_camera") != prev_camera
        ):
            # Phase 27 (W1): a REAL system/AI output change — observed
            # so the learning shadow can tell it apart from human
            # writes (source == "system_publish" => never human).
            _notify_output_write(
                {
                    "source": "system_publish",
                    "scene": state["scene"],
                    "active_camera": state.get("active_camera"),
                    "updated_by": updated_by,
                    "mode": state["mode"],
                }
            )
        await self._broadcast({"type": WS_MESSAGE_TYPE, "data": state})
        return {"changed": True, "state": state}

    # ── PVW preview (Phase 25, W1) ──────────────────────────────

    async def update_preview(
        self, preview: Optional[Dict[str, Any]]
    ) -> bool:
        """Set/clear the PVW preview candidate (live director only).

        Preview is informational pre-positioning for the director UI —
        it is not a program write, so it is allowed in FULL_AUTO and
        SEMI_AUTO mode alike (the live director itself never calls this
        in MANUAL mode). Broadcasts ``director.state`` only when the
        preview actually changed. Returns True when it changed.
        """
        async with self._lock:
            if self._state.get("preview") == preview:
                return False
            self._state["preview"] = copy.deepcopy(preview)
            state = copy.deepcopy(self._state)
        await self._broadcast({"type": WS_MESSAGE_TYPE, "data": state})
        return True

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
            prev_scene = self._state.get("scene")
            prev_camera = self._state.get("active_camera")
            self._apply_suggestion_payload(
                suggestion["type"], suggestion["payload"], updated_by
            )
            suggestion["status"] = "applied"
            suggestion["decided_at"] = _utcnow_iso()
            suggestion["decided_by"] = updated_by
            outgoing = copy.deepcopy(suggestion)
            state = copy.deepcopy(self._state)
        # Phase 27 (W1): the human's decision on this suggestion is a
        # fate; a scene approval that actually moved the output is
        # also a human output write (the shadow's distinguisher).
        _notify_fate(
            {
                "suggestion_id": suggestion_id,
                "fate": "approved",
                "decided_by": updated_by,
                "suggestion": outgoing,
                "ts": outgoing.get("decided_at"),
            }
        )
        if outgoing.get("type") == "scene" and (
            state["scene"] != prev_scene
            or state.get("active_camera") != prev_camera
        ):
            _notify_output_write(
                {
                    "source": "approve_suggestion",
                    "suggestion_id": suggestion_id,
                    "scene": state["scene"],
                    "active_camera": state.get("active_camera"),
                    "updated_by": updated_by,
                    "mode": state["mode"],
                }
            )
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
        # Phase 27 (W1): rejection is a human fate (opens the journal's
        # 30 s "modified" watch — see director_learning/journal.py).
        _notify_fate(
            {
                "suggestion_id": suggestion_id,
                "fate": "rejected",
                "decided_by": updated_by,
                "suggestion": outgoing,
                "ts": outgoing.get("decided_at"),
            }
        )
        await self._broadcast({"type": WS_SUGGESTION_TYPE, "data": outgoing})
        return outgoing

    # ── phase-aware suggestions (Phase 24, W3) ─────────────────
    # Pre-existing director suggestion sources: the match simulator's
    # fight path in app/main.py (kill/knock -> killfeed_focus) — that
    # fight logic is UNCHANGED and keeps owning mid-game. Added here:
    # suggestions keyed by the live match phase (PhaseTracker), each
    # stating its phase reason. In FULL_AUTO the director acts via
    # system_publish elsewhere; these proposals only land in SEMI_AUTO
    # (propose_suggestion's own gate), exactly like the fight source.

    @staticmethod
    def phase_suggestion_plan(phase: str) -> List[Dict[str, Any]]:
        """Pure plan: suggestion dicts for a match phase (no I/O).

        Each entry: {type, payload, reason, confidence}; every reason
        names its phase. Final circle NEVER proposes map_cam (the
        final-zone HARD lock already lives in the zone/director stack —
        this plan aligns with it by preferring killfeed_focus, it does
        not duplicate the lock).
        """
        phase = (phase or "").strip().lower()
        if phase in ("plane", "parachute"):
            return [
                {
                    "type": "scene",
                    "payload": {"scene": "map_cam"},
                    "reason": (
                        f"[phase: {phase}] Drop phase — map-cam shows the "
                        "flight path and where teams land."
                    ),
                    "confidence": 0.8,
                },
                {
                    "type": "lower_third",
                    "payload": {
                        "visible": True,
                        "title": "DROP WATCH",
                        "subtitle": "Teams landing — contested drops incoming",
                    },
                    "reason": (
                        f"[phase: {phase}] Drop phase — drop-watch graphic "
                        "while teams land."
                    ),
                    "confidence": 0.7,
                },
            ]
        if phase in ("loot", "early"):
            return [
                {
                    "type": "scene",
                    "payload": {"scene": "map_cam"},
                    "reason": (
                        f"[phase: {phase}] Loot/early phase — rotation-watch "
                        "on map-cam: who rotates first, who holds centre."
                    ),
                    "confidence": 0.65,
                }
            ]
        if phase == "mid_game":
            # Existing fight logic owns mid-game suggestions; the phase
            # layer deliberately adds nothing here (no duplication).
            return []
        if phase == "late_game":
            return [
                {
                    "type": "scene",
                    "payload": {"scene": "standings"},
                    "reason": (
                        "[phase: late_game] Late game (zone >=5 or alive <=16) "
                        "— standings tell the points story now."
                    ),
                    "confidence": 0.75,
                },
                {
                    "type": "lower_third",
                    "payload": {
                        "visible": True,
                        "title": "WIN PROBABILITY",
                        "subtitle": "Late game — WWCD race is live",
                    },
                    "reason": (
                        "[phase: late_game] Late game — win-probability "
                        "story graphic is relevant now."
                    ),
                    "confidence": 0.7,
                },
            ]
        if phase == "final_circle":
            return [
                {
                    "type": "scene",
                    "payload": {"scene": "killfeed_focus"},
                    "reason": (
                        "[phase: final_circle] Final circle (alive <=8 or "
                        "zone >=7) — killfeed focus lock; map-cam "
                        "suggestions suppressed in the final circle."
                    ),
                    "confidence": 0.9,
                }
            ]
        return []

    async def propose_phase_suggestions(
        self, phase_snapshot: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Propose the phase-aware suggestions for the current phase.

        ``phase_snapshot`` may carry {"phase": ...}; when omitted the
        PhaseTracker singleton is queried. Returns the suggestions that
        were actually created (empty outside SEMI_AUTO / for phases with
        no plan — propose_suggestion enforces both honestly).
        """
        phase = None
        if isinstance(phase_snapshot, dict):
            phase = phase_snapshot.get("phase")
        if not phase:
            try:
                from app.services.phase_tracker import phase_tracker

                phase = phase_tracker.current_phase()
            except Exception:  # pragma: no cover - defensive
                phase = None
        created: List[Dict[str, Any]] = []
        for item in self.phase_suggestion_plan(str(phase or "")):
            suggestion = await self.propose_suggestion(
                suggestion_type=item["type"],
                payload=item["payload"],
                reason=item["reason"],
                confidence=item["confidence"],
                created_by="phase_tracker",
            )
            if suggestion is not None:
                created.append(suggestion)
        return created

    async def propose_phase_suggestions_on_change(
        self, phase_snapshot: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Phase 31 production entry point for phase suggestions.

        Proposes exactly once per phase ENTRY: when the current phase
        differs from the last phase this method proposed for, it
        delegates to :meth:`propose_phase_suggestions` and records the
        phase; a repeat call for the same phase returns ``[]`` without
        touching the suggestion queue. Phases with no plan are still
        recorded (so a plan-less phase never re-fires either), and a
        phase the tracker re-derives identically across ticks stays
        silent. Outside SEMI_AUTO the delegate is already a no-op, so
        this method is safe to call on any ticker cadence in any mode.
        """
        phase = None
        if isinstance(phase_snapshot, dict):
            phase = phase_snapshot.get("phase")
        if not phase:
            try:
                from app.services.phase_tracker import phase_tracker

                phase = phase_tracker.current_phase()
            except Exception:  # pragma: no cover - defensive
                phase = None
        phase = str(phase or "")
        if phase == self._last_phase_suggestion_phase:
            return []
        self._last_phase_suggestion_phase = phase
        return await self.propose_phase_suggestions({"phase": phase})

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
            # Phase 27 (W1): same expired fate as _prune_expired —
            # without this the transition here would be invisible to
            # the journal (the pruner only sees "pending" rows).
            _notify_fate(
                {
                    "suggestion_id": suggestion["id"],
                    "fate": "expired",
                    "decided_by": None,
                    "suggestion": copy.deepcopy(suggestion),
                    "ts": _utcnow_iso(),
                }
            )
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
        expired_now = []
        for suggestion in self._suggestions.values():
            if suggestion["status"] != "pending":
                continue
            try:
                expires_at = datetime.fromisoformat(suggestion["expires_at"])
            except (ValueError, TypeError):
                expires_at = now
            if expires_at <= now:
                suggestion["status"] = "expired"
                expired_now.append(copy.deepcopy(suggestion))
        # Phase 27 (W1): expiry is a fate too (decided_by=None — nobody
        # decided; the 60 s TTL lapsed). Notified AFTER the mutation
        # loop; notify_fate is failure-isolated and lock-free, and this
        # method's callers may hold the lock.
        for suggestion in expired_now:
            _notify_fate(
                {
                    "suggestion_id": suggestion["id"],
                    "fate": "expired",
                    "decided_by": None,
                    "suggestion": suggestion,
                    "ts": now.isoformat(),
                }
            )

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
            if payload["scene"] != self._state["scene"]:
                return False
            camera = payload.get("active_camera")
            return camera is None or camera == self._state.get(
                "active_camera"
            )
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
            if payload.get("active_camera") is not None:
                # Validated at propose time (_validate_camera).
                self._state["active_camera"] = payload["active_camera"]
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
