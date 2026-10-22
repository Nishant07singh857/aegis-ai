# backend/app/services/replay/queue.py
"""Replay offer/take queue with A/B discipline (Phase 25, P0-2).

THE DISCIPLINE (research §2.3 + blueprint C7/U12)
------------------------------------------------
Professional replay is NOT "play it the moment it happens":

  1. A high-value moment that happened OFF program (PGM) — the second
     simultaneous fight, a wipe the camera never showed — is PREPARED:
     its clip is cut from the ISO recording when one exists.
  2. The prepared replay is OFFERED: it sits in a READY slot ("Replay
     A" / "Replay B") in the preview row. At most TWO entries may be
     READY at once; further moments wait QUEUED behind them.
  3. The director TAKES it — only in a lull. A take is refused while a
     fight is live on PGM, in EVERY mode (full_auto included), and
     takes are suppressed entirely from late_game on (blueprint E11 /
     "phase-6+ suppression": entries may still prepare, they expire
     honestly instead of interrupting the endgame).
  4. A take airs exactly once, packaged the way the codebase already
     airs replays: the ``replay.play`` WS path of
     :class:`~app.services.replay.service.ReplayService`, carrying the
     replay branding payload (stinger in/out + REPLAY lower-third).

STATE MACHINE
-------------
    PREPARING -> READY (offered, holds slot A or B)
              -> QUEUED (both slots busy; promoted when one frees)
              -> TAKEN (aired once) | DISMISSED | EXPIRED
    QUEUED    -> READY | DISMISSED | EXPIRED

PREPARING is real work, not a label: when the ISO recorder has a
closed segment covering the moment's window, the existing highlight
renderer cuts it (``is_video=True`` only when the render actually
succeeds). When NO recording covers the window, the entry honestly
becomes an EVENT replay (``is_video=False``, the pre-Phase-25 model):
the take re-emits the buffered events — it never claims a video clip
that does not exist.

BOUNDED-QUEUE POLICY (documented, not implied)
----------------------------------------------
  * At most ``max_entries`` (default 8) live (preparing/queued/ready)
    entries. On overflow the OLDEST LOWEST-SCORE live entry expires to
    make room; if the incoming moment scores below every live entry,
    the incoming entry itself is born EXPIRED with that reason.
  * Entries expire ``ttl_seconds`` (default 180 s) after creation —
    a stale replay of a fight three minutes ago is not broadcast
    material. Expiry frees its A/B slot and promotes the best QUEUED
    entry (highest score, then oldest).
  * Dedupe: one live entry per ``moment_id``.

SOURCES OF MOMENTS
------------------
  * :meth:`ReplayQueue.poll_highlights` — the existing HighlightDetector
    over the replay buffer (squad wipes, multi-kills, clutches and the
    plug-ready weapon triggers; scores are the detector's own).
  * :meth:`ReplayQueue.handle_candidate` / ``subscribe_to(bus)`` — the
    ``replay.candidate`` bus hand-off the live director (W1) emits for
    simultaneous fight #2. If no publisher exists yet, subscribing is
    harmless: the topic simply never fires and poll/manual enqueue
    still work (graceful degradation by construction).

    THE ``replay.candidate`` CONTRACT (both sides — publisher
    ``app/services/live_director/service.py::_maybe_replay_handoff``,
    consumer :meth:`handle_candidate` — must meet this exact field
    list; the joint test is
    ``tests/unit/test_replay_candidate_contract.py``):
      fight_id (str), teams (list[str]),
      window {started_at, last_activity_at},
      intensity (float), is_clutch (bool), reason (str),
      source="live_director",
      kind (str: "clutch" when is_clutch else "fight" — a
      director-curated kind, never a fabricated detector kind),
      window_start (float, = fight started_at),
      window_end (float, = fight last_activity_at),
      score (int, = int(intensity)),
      moment_id (str, = fight_id, so queue dedupe works),
      team (str, first team of the fight),
      opponents (list[str], remaining teams),
      slot_number (int, challenger candidate's observer slot).
    The consumer requires kind + a window (flat window_start/window_end,
    or ts, or the nested window dict as a fallback); an underspecified
    candidate is dropped, never padded out. Candidate kinds are gated
    by CANDIDATE_ACCEPTED_KINDS (detector QUALIFYING_KINDS plus the
    director-curated "fight"); the detector poll path's
    QUALIFYING_KINDS gate stays detector-only.

TAKE GATES — ``can_take_now``
-----------------------------
Computed live from injected providers (defaults read the real
singletons; tests inject fakes):

  * ``phase_suppressed`` — PhaseTracker phase in {late_game,
    final_circle} (E11; zone phase >= 5 in PhaseTracker's own mapping).
  * ``fight_on_pgm`` — a KNOWN active fight exists (the fights
    provider returned a non-empty list). A known active fight always
    blocks, in every director mode.
  * ``fight_state_unknown`` — fight state is UNKNOWN: the fights
    provider returned ``None`` (the default provider does so when no
    vision-intelligence service is wired, or when reading it raises).
    The gate fails closed (``can_take`` is False) — "no service" is
    never dressed up as "no fight". A provider returning a list
    (possibly empty) is a KNOWN state: an empty list blocks on
    neither ``fight_on_pgm`` nor ``fight_state_unknown``.
  * ``final_circle_lock`` — phase == final_circle (subsumed by the
    phase gate, reported separately because it is the SafetyController
    hard lock's queue-side mirror).

Modes: ``full_auto`` — only the system may take, via
:meth:`maybe_take_auto` (called by a ticker / status poll); operator
takes are refused. ``semi_auto`` / ``manual`` — takes/dismissals are
explicit operator actions via the API.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import structlog

logger = structlog.get_logger("aegis.replay.queue")

STATE_PREPARING = "preparing"
STATE_QUEUED = "queued"
STATE_READY = "ready"
STATE_TAKEN = "taken"
STATE_EXPIRED = "expired"
STATE_DISMISSED = "dismissed"

LIVE_STATES = (STATE_PREPARING, STATE_QUEUED, STATE_READY)
TERMINAL_STATES = (STATE_TAKEN, STATE_EXPIRED, STATE_DISMISSED)

#: Highlight kinds that qualify a moment for the replay queue, with the
#: detector's own score as the value ordering (wipe 90, verified
#: clutch 95, multi-kill 60-95, ...). Below ``min_score`` nothing is
#: queued — the queue is for high-value moments, not every kill.
QUALIFYING_KINDS = {
    "squad_wipe", "multi_kill", "clutch", "final_kill",
    "grenade_multi_kill", "sniper_triple", "vehicle_kill",
}
#: Kinds accepted on the ``replay.candidate`` (director hand-off) path:
#: every detector kind plus the director-curated ``"fight"`` — a
#: simultaneous fight handed off by the live director is truthfully a
#: fight, not a fabricated squad_wipe/multi_kill. The detector poll
#: path keeps using QUALIFYING_KINDS alone (detector-only gate).
CANDIDATE_ACCEPTED_KINDS = QUALIFYING_KINDS | {"fight"}
DEFAULT_MIN_SCORE = 60
DEFAULT_TTL_SECONDS = 180.0
DEFAULT_MAX_ENTRIES = 8
MAX_READY = 2  # Replay A + Replay B — the A/B discipline.

#: Phases in which takes are suppressed (blueprint E11, "phase 6+").
SUPPRESSED_PHASES = {"late_game", "final_circle"}

#: Bus topic emitted by the live director for simultaneous fight #2.
CANDIDATE_EVENT_TYPE = "replay.candidate"


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class ReplayTakeRefused(RuntimeError):
    """A take was attempted against the grammar gates or mode rules."""

    def __init__(self, message: str, gate: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.gate = gate or {}


class ReplayEntryNotFound(KeyError):
    pass


@dataclass
class ReplayQueueEntry:
    id: str
    kind: str
    score: int
    reason: str
    window_start: float
    window_end: float
    player: Optional[str] = None
    team: Optional[str] = None
    opponents: List[str] = field(default_factory=list)
    moment_id: Optional[str] = None
    feed_id: Optional[str] = None
    slot_number: Optional[int] = None
    source: str = "highlight_detector"  # | replay_candidate | manual
    state: str = STATE_PREPARING
    state_history: List[str] = field(default_factory=lambda: [STATE_PREPARING])
    is_video: bool = False
    render_status: str = "not_requested"
    render_files: Dict[str, str] = field(default_factory=dict)
    render_error: Optional[str] = None
    recording_path: Optional[str] = None
    slot_label: Optional[str] = None  # "A" | "B" while READY
    verified: Optional[bool] = None  # clutch verification, when known
    created_at: float = field(default_factory=time.time)
    ready_at: Optional[float] = None
    taken_at: Optional[float] = None
    taken_by: Optional[str] = None
    expires_at: float = 0.0
    status_note: Optional[str] = None

    def _set_state(self, state: str) -> None:
        self.state = state
        self.state_history.append(state)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "moment_id": self.moment_id,
            "kind": self.kind,
            "score": self.score,
            "reason": self.reason,
            "player": self.player,
            "team": self.team,
            "opponents": list(self.opponents),
            "window_start": self.window_start,
            "window_end": self.window_end,
            "window_start_iso": _iso(self.window_start),
            "window_end_iso": _iso(self.window_end),
            "feed_id": self.feed_id,
            "slot_number": self.slot_number,
            "source": self.source,
            "state": self.state,
            "state_history": list(self.state_history),
            "is_video": self.is_video,
            "render_status": self.render_status,
            "render_files": dict(self.render_files),
            "render_error": self.render_error,
            "recording_path": self.recording_path,
            "slot_label": self.slot_label,
            "verified": self.verified,
            "created_at": self.created_at,
            "created_at_iso": _iso(self.created_at),
            "ready_at": self.ready_at,
            "taken_at": self.taken_at,
            "taken_at_iso": _iso(self.taken_at),
            "taken_by": self.taken_by,
            "expires_at": self.expires_at,
            "status_note": self.status_note,
        }


# Provider callables: () -> director state dict / phase str /
# Optional fight list (None = fight state UNKNOWN; a list, possibly
# empty, = known state).
DirectorProvider = Callable[[], Dict[str, Any]]
PhaseProvider = Callable[[], str]
FightsProvider = Callable[[], Optional[List[Dict[str, Any]]]]


def _default_director_provider() -> Dict[str, Any]:
    try:
        from app.services.director_service import director_service

        return director_service.snapshot()
    except Exception:  # pragma: no cover - defensive
        return {"mode": "manual", "active_camera": None, "scene": "main"}


def _default_phase_provider() -> str:
    try:
        from app.services.phase_tracker import phase_tracker

        return phase_tracker.current_phase()
    except Exception:  # pragma: no cover - defensive
        return "unknown"


def _default_fights_provider() -> Optional[List[Dict[str, Any]]]:
    """Active fights from vision intelligence when wired, else None.

    ``None`` means fight state is UNKNOWN: no vision-intelligence
    service is wired, or reading it raised. The queue's take gate
    labels that ``fight_state_unknown`` and fails closed, so "no
    service" is never dressed up as "no fight". When the service
    exists, its real list is returned (possibly empty = known to
    have no active fights). Tests inject their own provider.
    """
    try:
        from app.services.vision_intelligence.service import get_vision_intelligence_service

        svc = get_vision_intelligence_service()
        if svc is None:
            return None
        return list(svc.fight_detector.get_active_fights())
    except Exception:  # pragma: no cover - defensive
        return None


class ReplayQueue:
    """Offer/take queue bound to one ReplayService (buffer+renderer)."""

    def __init__(
        self,
        replay_service: Any,
        recorder: Any = None,
        director_provider: Optional[DirectorProvider] = None,
        phase_provider: Optional[PhaseProvider] = None,
        fights_provider: Optional[FightsProvider] = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        min_score: int = DEFAULT_MIN_SCORE,
    ) -> None:
        self._svc = replay_service
        self.recorder = recorder
        self._director_provider = director_provider or _default_director_provider
        self._phase_provider = phase_provider or _default_phase_provider
        self._fights_provider = fights_provider or _default_fights_provider
        self.ttl_seconds = float(ttl_seconds)
        self.max_entries = int(max_entries)
        self.min_score = int(min_score)
        self._entries: Dict[str, ReplayQueueEntry] = {}
        self._subscribed_bus_id: Optional[str] = None

    # ── provider reads (never raise out) ───────────────────────

    def _director_state(self) -> Dict[str, Any]:
        try:
            return dict(self._director_provider() or {})
        except Exception:  # pragma: no cover - defensive
            return {}

    def _phase(self) -> str:
        try:
            return str(self._phase_provider() or "unknown")
        except Exception:  # pragma: no cover - defensive
            return "unknown"

    def _active_fights(self) -> Optional[List[Dict[str, Any]]]:
        """Active fights, or None when fight state is UNKNOWN.

        The provider contract is ``Optional[List]``: ``None`` (from
        the default provider's fallback, an injected provider, or a
        provider that raised) means UNKNOWN and the take gate fails
        closed; a list — possibly empty — is a known state. ``None``
        is never collapsed to ``[]`` here: that collapse is exactly
        what dressed "no service" up as "no fight".
        """
        try:
            result = self._fights_provider()
        except Exception:  # pragma: no cover - defensive
            return None
        if result is None:
            return None
        return list(result)

    def director_mode(self) -> str:
        # Missing mode falls back to the canonical fresh-show default
        # (Phase 28: director_service.default_director_mode()).
        from app.services.director_service import default_director_mode

        mode = str(
            self._director_state().get("mode") or default_director_mode()
        ).lower()
        return "full_auto" if mode == "auto" else mode

    # ── take gate ──────────────────────────────────────────────

    def can_take_now(self) -> Dict[str, Any]:
        """The lull gate, computed live. A take is allowed ONLY when
        ``can_take`` is true; every blocker is named in the payload.

        On the fight axis the gate is three-valued via the provider
        contract: provider ``None`` -> ``fight_state_unknown`` and
        ``can_take`` False (fail closed — unknown is not a lull); a
        known non-empty list -> ``fight_on_pgm``; a known empty list
        -> neither token. ``active_fights_on_pgm`` is the known list,
        or ``[]`` when the state is unknown (the unknown-ness is
        carried by ``blocked_by``, never hidden).
        """
        phase = self._phase()
        fights = self._active_fights()
        blocked_by: List[str] = []
        if phase in SUPPRESSED_PHASES:
            blocked_by.append("phase_suppressed")
        if phase == "final_circle":
            blocked_by.append("final_circle_lock")
        if fights is None:
            blocked_by.append("fight_state_unknown")
        elif fights:
            blocked_by.append("fight_on_pgm")
        return {
            "can_take": not blocked_by,
            "blocked_by": blocked_by,
            "phase": phase,
            "active_fights_on_pgm": fights if fights is not None else [],
            "director_mode": self.director_mode(),
            "active_camera": self._director_state().get("active_camera"),
        }

    # ── enqueue paths ──────────────────────────────────────────

    def enqueue_moment(
        self,
        kind: str,
        score: int,
        reason: str,
        window_start: float,
        window_end: float,
        player: Optional[str] = None,
        team: Optional[str] = None,
        opponents: Optional[List[str]] = None,
        moment_id: Optional[str] = None,
        feed_id: Optional[str] = None,
        slot_number: Optional[int] = None,
        source: str = "highlight_detector",
        verified: Optional[bool] = None,
        match_id: Optional[str] = None,
        fingerprint_dedupe: bool = False,
    ) -> ReplayQueueEntry:
        """Create an entry: PREPARING (render attempt) -> READY/QUEUED."""
        self.prune()
        for existing in self._entries.values():
            if existing.state not in LIVE_STATES:
                continue
            if moment_id and existing.moment_id == moment_id:
                return existing  # dedupe: one live entry per moment
            # The highlight detector mints a FRESH moment_id on every
            # scoring run, so id-dedupe alone cannot stop a re-poll from
            # queueing the same moment twice. For the poll path only,
            # the stable identity of a moment is kind + event window.
            # (Manual/candidate enqueues with distinct ids are distinct
            # moments even when their windows coincide.)
            if fingerprint_dedupe and (
                existing.kind == kind
                and abs(existing.window_start - float(window_start)) < 0.01
                and abs(existing.window_end - float(window_end)) < 0.01
            ):
                return existing
        now = time.time()
        entry = ReplayQueueEntry(
            id=str(uuid.uuid4()),
            kind=kind,
            score=int(score),
            reason=reason,
            window_start=float(window_start),
            window_end=float(window_end),
            player=player,
            team=team,
            opponents=list(opponents or []),
            moment_id=moment_id,
            feed_id=feed_id or (f"slot-{slot_number}" if slot_number else None),
            slot_number=slot_number,
            source=source,
            verified=verified,
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )
        self._entries[entry.id] = entry
        if not self._enforce_bound(new_entry=entry):
            return entry  # born EXPIRED by the bounded-queue policy
        self._prepare(entry, match_id=match_id)
        self._offer_or_queue(entry)
        logger.info(
            "replay_queue_entry", entry_id=entry.id, kind=kind, score=score,
            state=entry.state, is_video=entry.is_video, source=source,
        )
        return entry

    def _prepare(self, entry: ReplayQueueEntry, match_id: Optional[str] = None) -> None:
        """PREPARING work: cut the clip when a recording truly covers
        the window; otherwise the entry is an honest event replay."""
        found = None
        if self.recorder is not None:
            try:
                found = self.recorder.find_recording_for_window(
                    entry.feed_id, entry.window_start, entry.window_end
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("replay_queue_recording_lookup_failed", error=str(exc)[:200])
        if found is None:
            entry.is_video = False
            entry.render_status = "no_source"
            entry.render_error = (
                "No ISO recording covers this moment's window; the entry "
                "is an event replay (buffered events re-emitted on take), "
                "not a video clip."
            )
            return
        path, started_at = found
        entry.recording_path = str(path)
        try:
            record = self._svc.renderer.render(
                moment_id=entry.id,
                window_start=entry.window_start,
                window_end=entry.window_end,
                match_id=match_id,
                source_path=str(path),
                source_started_at=started_at,
            )
        except Exception as exc:
            entry.is_video = False
            entry.render_status = "failed"
            entry.render_error = f"Renderer raised: {str(exc)[:300]}"
            return
        entry.render_status = record.status
        entry.render_files = dict(record.files)
        entry.render_error = record.error
        # Only a genuinely rendered file set may claim video.
        entry.is_video = record.status == "rendered" and bool(record.files)
        if not entry.is_video and entry.render_error is None:
            entry.render_error = "Render did not produce a clip; entry stays an event replay."

    def _ready_entries(self) -> List[ReplayQueueEntry]:
        return [e for e in self._entries.values() if e.state == STATE_READY]

    def _offer_or_queue(self, entry: ReplayQueueEntry) -> None:
        entry.ready_at = time.time()
        if len(self._ready_entries()) < MAX_READY:
            entry._set_state(STATE_READY)
            self._assign_slot_labels()
        else:
            entry._set_state(STATE_QUEUED)
            entry.status_note = "Both Replay A and Replay B are offered; this moment queues behind them."

    def _assign_slot_labels(self) -> None:
        ready = sorted(self._ready_entries(), key=lambda e: (e.ready_at or e.created_at, e.created_at))
        labels = ["A", "B"]
        for idx, entry in enumerate(ready):
            entry.slot_label = labels[idx] if idx < len(labels) else None
        for entry in self._entries.values():
            if entry.state != STATE_READY:
                entry.slot_label = None

    def _promote_queued(self) -> None:
        """Fill free A/B slots from QUEUED: highest score, then oldest."""
        while len(self._ready_entries()) < MAX_READY:
            queued = [e for e in self._entries.values() if e.state == STATE_QUEUED]
            if not queued:
                break
            queued.sort(key=lambda e: (-e.score, e.created_at))
            nxt = queued[0]
            nxt._set_state(STATE_READY)
            nxt.ready_at = time.time()
            nxt.status_note = None
        self._assign_slot_labels()

    def _enforce_bound(self, new_entry: ReplayQueueEntry) -> bool:
        """Bounded queue: expire oldest low-value first (see docstring)."""
        live = [e for e in self._entries.values() if e.state in LIVE_STATES]
        while len(live) > self.max_entries:
            victim = min(live, key=lambda e: (e.score, e.created_at))
            victim._set_state(STATE_EXPIRED)
            victim.status_note = "Expired by bounded-queue policy: oldest low-value entry dropped for a newer moment."
            if victim is new_entry:
                return False
            live = [e for e in self._entries.values() if e.state in LIVE_STATES]
        return True

    # ── sources: detector poll + bus candidate ─────────────────

    def poll_highlights(self, limit: int = 50) -> List[ReplayQueueEntry]:
        """Scan the replay buffer's highlight reel and enqueue every
        qualifying moment not already queued. Honestly returns [] when
        the detector finds nothing — moments are never synthesised."""
        highlights = self._svc.get_highlights(limit=limit)
        before_ids = set(self._entries.keys())
        created: List[ReplayQueueEntry] = []
        pgm_camera = self._director_state().get("active_camera")
        for h in highlights:
            if h.kind not in QUALIFYING_KINDS or h.score < self.min_score:
                continue
            # Off-PGM determination: when the moment carries an
            # observer slot and it IS the program camera, the audience
            # already saw it live — it is not a replay-queue moment.
            slot_number = self._slot_for_highlight(h)
            if slot_number is not None and pgm_camera is not None and int(slot_number) == int(pgm_camera):
                continue
            verified = None
            if h.kind == "clutch":
                verified = h.score >= 95  # detector: verified 1vN scores 95
            entry = self.enqueue_moment(
                kind=h.kind,
                score=h.score,
                reason=h.reason,
                window_start=h.window_start,
                window_end=h.window_end,
                player=h.player,
                team=h.team,
                opponents=h.opponents,
                moment_id=h.moment_id,
                slot_number=slot_number,
                verified=verified,
                fingerprint_dedupe=True,
            )
            # enqueue_moment dedupes (id + kind/window fingerprint):
            # only entries born in THIS poll are reported as created.
            if entry.id not in before_ids and entry.id not in {e.id for e in created}:
                created.append(entry)
        return created

    def _slot_for_highlight(self, highlight: Any) -> Optional[int]:
        """Observer slot of a highlight's source events, when the buffer
        events carry one (``observer_slot``/``slot_number``/``slot`` —
        the attribution module's canonical keys). None = unattributed."""
        try:
            events = self._svc.buffer.events_between(
                highlight.window_start, highlight.window_end
            )
        except Exception:  # pragma: no cover - defensive
            return None
        ids = set(getattr(highlight, "event_ids", []) or [])
        for event in events:
            if ids and event.event_id not in ids:
                continue
            for key in ("observer_slot", "slot_number", "slot"):
                raw = event.payload.get(key)
                if raw is not None:
                    try:
                        slot = int(raw)
                    except (TypeError, ValueError):
                        continue
                    if 1 <= slot <= 16:
                        return slot
        return None

    async def handle_record(self, record: Any) -> None:
        """EventBus callback for ``replay.candidate``. Never raises."""
        try:
            event = getattr(record, "event", None)
            data = getattr(event, "data", None) if event is not None else None
            if not isinstance(data, dict):
                data = getattr(record, "data", None)
            if isinstance(data, dict):
                self.handle_candidate(data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("replay_candidate_handler_failed", error=str(exc)[:200])

    def handle_candidate(self, payload: Dict[str, Any]) -> Optional[ReplayQueueEntry]:
        """Consume one ``replay.candidate`` hand-off (live director's
        simultaneous fight #2). Returns the entry, or None when the
        payload lacks the minimum truthful fields (a window + a kind) —
        an underspecified candidate is dropped, never padded out.

        Contract (see module docstring; publisher is
        ``LiveDirector._maybe_replay_handoff``): kind, window_start,
        window_end, score, moment_id, team, opponents, slot_number
        (plus fight_id / teams / window / intensity / is_clutch /
        reason / source for context). Kind must be in
        CANDIDATE_ACCEPTED_KINDS — detector kinds plus the
        director-curated "fight" ("clutch" when the fight is_clutch).
        """
        kind = str(payload.get("kind") or payload.get("moment_kind") or "").strip()
        window_start = payload.get("window_start", payload.get("window_start_ts"))
        window_end = payload.get("window_end", payload.get("window_end_ts"))
        if window_start is None and payload.get("ts") is not None:
            ts = float(payload["ts"])
            window_start, window_end = ts - 5.0, ts + 10.0
        # Fallback: the publisher's nested window dict (kept in the
        # payload for context) also satisfies the window requirement.
        if window_start is None or window_end is None:
            window = payload.get("window")
            if isinstance(window, dict):
                if window_start is None:
                    window_start = window.get("started_at", window.get("start"))
                if window_end is None:
                    window_end = window.get(
                        "last_activity_at", window.get("end")
                    )
        if not kind or window_start is None or window_end is None:
            logger.info("replay_candidate_dropped_underspecified", payload_keys=sorted(payload.keys()))
            return None
        if kind not in CANDIDATE_ACCEPTED_KINDS:
            logger.info(
                "replay_candidate_dropped_unknown_kind",
                kind=kind, payload_keys=sorted(payload.keys()),
            )
            return None
        slot = payload.get("slot_number", payload.get("observer_slot"))
        # team/opponents: explicit flat fields win; the publisher's
        # teams list is the fallback (first = team, rest = opponents).
        team = payload.get("team")
        opponents = list(payload.get("opponents") or [])
        if team is None and isinstance(payload.get("teams"), list) and payload["teams"]:
            team = payload["teams"][0]
            if not opponents:
                opponents = list(payload["teams"][1:])
        return self.enqueue_moment(
            kind=kind,
            score=int(payload.get("score") or 80),
            reason=str(payload.get("reason") or f"Director candidate: {kind} (simultaneous fight)"),
            window_start=float(window_start),
            window_end=float(window_end),
            player=payload.get("player"),
            team=team,
            opponents=opponents,
            moment_id=payload.get("moment_id") or payload.get("fight_id"),
            feed_id=payload.get("feed_id"),
            slot_number=int(slot) if slot is not None else None,
            source="replay_candidate",
            verified=payload.get("verified"),
            match_id=payload.get("match_id"),
        )

    def subscribe_to(self, bus: Any) -> Optional[str]:
        """Subscribe to ``replay.candidate`` on a live EventBus.

        Graceful when the topic has no publisher yet (always true until
        W1 lands): subscription is in-memory, it simply never fires.
        Returns the subscriber id, or None if the bus refused.
        """
        try:
            self._subscriber_id = f"replay-queue-{uuid.uuid4().hex[:8]}"
            bus.subscribe(
                subscriber_id=self._subscriber_id,
                callback=self.handle_record,
                event_types=[CANDIDATE_EVENT_TYPE],
            )
            self._subscribed_bus_id = self._subscriber_id
            logger.info("replay_queue_subscribed", topic=CANDIDATE_EVENT_TYPE)
            return self._subscriber_id
        except Exception as exc:
            logger.warning("replay_queue_subscribe_failed", error=str(exc)[:200])
            return None

    # ── take / dismiss ─────────────────────────────────────────

    async def take(self, entry_id: str, taken_by: str = "operator", via: str = "operator") -> ReplayQueueEntry:
        """Air one READY entry exactly once, through replay.play.

        Refusals (ReplayTakeRefused): entry not READY (incl. already
        TAKEN — an entry never airs twice), the lull gate is closed
        (fight on PGM / phase suppression — in EVERY mode), or the mode
        rule is violated (operator take in full_auto; system take
        outside full_auto).
        """
        entry = self._entries.get(entry_id)
        if entry is None:
            raise ReplayEntryNotFound(entry_id)
        self.prune()
        if entry.state == STATE_TAKEN:
            raise ReplayTakeRefused(f"Replay entry {entry_id} was already taken at {_iso(entry.taken_at)}; an entry never airs twice.")
        if entry.state != STATE_READY:
            raise ReplayTakeRefused(f"Replay entry {entry_id} is {entry.state}, not ready (offered); only a READY entry can be taken.")
        mode = self.director_mode()
        if via == "operator" and mode == "full_auto":
            raise ReplayTakeRefused("Director is in full_auto: operator takes are refused; the system takes automatically when a lull opens.")
        if via == "system" and mode != "full_auto":
            raise ReplayTakeRefused(f"System auto-take only runs in full_auto (director is {mode}); use an explicit operator take.")
        gate = self.can_take_now()
        if not gate["can_take"]:
            raise ReplayTakeRefused(
                f"Take refused by the lull gate ({', '.join(gate['blocked_by'])}): replays never air over a live fight or in the suppressed late phases.",
                gate=gate,
            )
        # Air via the existing replay.play path (service._broadcast via
        # play_clip for event replays; video entries broadcast the same
        # type with their rendered files + branding packaging).
        if entry.is_video:
            await self._svc._broadcast({
                "type": "replay.play",
                "data": {
                    "queue_entry": entry.to_dict(),
                    "is_video": True,
                    "render_files": dict(entry.render_files),
                    "packaging": _packaging(entry),
                    "played_at": _iso(time.time()),
                },
            })
        else:
            clip = await self._svc.play_clip(entry.window_start, entry.window_end)
            await self._svc._broadcast({
                "type": "replay.play",
                "data": {
                    "queue_entry": entry.to_dict(),
                    "is_video": False,
                    "clip_event_count": clip.event_count,
                    "packaging": _packaging(entry),
                    "played_at": _iso(time.time()),
                },
            })
        entry._set_state(STATE_TAKEN)
        entry.taken_at = time.time()
        entry.taken_by = taken_by
        entry.slot_label = None
        self._promote_queued()
        logger.info("replay_taken", entry_id=entry.id, kind=entry.kind, is_video=entry.is_video, taken_by=taken_by)
        return entry

    async def maybe_take_auto(self) -> Optional[ReplayQueueEntry]:
        """full_auto ticker: take the best READY entry when the lull
        gate opens. Returns the taken entry, or None (gate closed, no
        READY entry, or not full_auto) — never forces a take."""
        if self.director_mode() != "full_auto":
            return None
        self.prune()
        ready = self._ready_entries()
        if not ready:
            return None
        if not self.can_take_now()["can_take"]:
            return None
        ready.sort(key=lambda e: (-e.score, e.created_at))
        try:
            return await self.take(ready[0].id, taken_by="system:full_auto", via="system")
        except ReplayTakeRefused:
            return None

    def dismiss(self, entry_id: str, dismissed_by: str = "operator") -> ReplayQueueEntry:
        entry = self._entries.get(entry_id)
        if entry is None:
            raise ReplayEntryNotFound(entry_id)
        if entry.state in TERMINAL_STATES:
            raise ReplayTakeRefused(f"Replay entry {entry_id} is already {entry.state}; it cannot be dismissed.")
        entry._set_state(STATE_DISMISSED)
        entry.status_note = f"Dismissed by {dismissed_by}."
        entry.slot_label = None
        self._promote_queued()
        return entry

    # ── expiry / reads ─────────────────────────────────────────

    def prune(self, now: Optional[float] = None) -> int:
        """Expire stale live entries (TTL). Returns count expired."""
        now = now if now is not None else time.time()
        expired = 0
        for entry in self._entries.values():
            if entry.state in LIVE_STATES and now >= entry.expires_at:
                entry._set_state(STATE_EXPIRED)
                entry.status_note = "Expired: the moment aged past the replay TTL without being taken."
                entry.slot_label = None
                expired += 1
        if expired:
            self._promote_queued()
            self._assign_slot_labels()
        return expired

    def get(self, entry_id: str) -> Optional[ReplayQueueEntry]:
        self.prune()
        return self._entries.get(entry_id)

    def list_entries(self, state: Optional[str] = None) -> List[ReplayQueueEntry]:
        self.prune()
        entries = sorted(self._entries.values(), key=lambda e: e.created_at, reverse=True)
        if state:
            entries = [e for e in entries if e.state == state]
        return entries

    def status(self) -> Dict[str, Any]:
        self.prune()
        counts: Dict[str, int] = {}
        for entry in self._entries.values():
            counts[entry.state] = counts.get(entry.state, 0) + 1
        return {
            "counts": counts,
            "ready_slots": {
                e.slot_label: e.id for e in self._ready_entries() if e.slot_label
            },
            "can_take_now": self.can_take_now(),
            "ttl_seconds": self.ttl_seconds,
            "max_entries": self.max_entries,
            "min_score": self.min_score,
            "max_ready": MAX_READY,
            "subscribed_bus": self._subscribed_bus_id,
            "entries": [e.to_dict() for e in self.list_entries()],
        }


def _packaging(entry: ReplayQueueEntry) -> Dict[str, Any]:
    """Replay branding/stinger treatment, reusing the codebase's own
    surfaces: the replay stinger pair (the ONE legal stinger — research
    §2.3, blueprint C7) + a REPLAY lower-third on the director state
    shape. No new player or transition is invented here."""
    return {
        "stinger_in": "replay_stinger_in",
        "stinger_out": "replay_stinger_out",
        "lower_third": {
            "visible": True,
            "title": "REPLAY",
            "subtitle": entry.reason[:120],
        },
        "slot_label": entry.slot_label,
    }
