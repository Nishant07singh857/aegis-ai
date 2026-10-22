# backend/app/services/live_director/service.py
"""Live Director — the ONE unified director decision service (Phase 25, P0-0).

This service closes the gap named in docs/DIRECTOR_DECISION_BLUEPRINT.md §0:
AEGIS had three director stacks and no autonomous camera switch on the real
spectator path.

Merge map (what came from where)
---------------------------------
* ``ObserverDirector`` (``app/agents/observer_agents/director_ai.py``, previously
  DEAD CODE with zero live callers) — PORTED here: candidate scoring (fight
  intensity base, clutch +25, priority team/player +15, narrative continuity
  +4/appearance capped +12), absolute switch margins (40 inside the fight-hold
  window / 15 default / 5 clutch challenger / 10 stale incumbent), staleness
  decay (90 s fight / 30 s other, -0.5/s), and the SafetyController rules
  (3 s switch cooldown, 8 switches/min cap, final-zone hard lock).
  Two warts FIXED in the port:
    - the fight "hold" was a boolean (fight still active); here it is a TRUE
      TIMER whose length comes from the match phase (PhaseTracker vocabulary:
      loot/early >= 30 s, mid_game >= 45 s, late_game >= 60 s, final_circle
      locked until the fight resolves).
    - the playbook dead-end (``_execute_playbook_action`` silently ignoring
      ``switch_to_fight`` / ``show_overview`` / ...) is not ported at all —
      there is no playbook layer here; every evaluation ends in an explicit
      logged outcome. (The deprecated stack's handler was also fixed in place
      to handle/log every action name explicitly.)
* ``DirectorAIEngine`` (``app/intelligence/director_ai.py``) — NOT ported. It
  remains DEMO_MODE-only (its ``SWITCH_POV`` never passed ``active_camera`` —
  the bug this service buries by always writing scene AND camera slot through
  :meth:`DirectorService.system_publish`).
* ``DirectorService`` (``app/services/director_service.py``) — unchanged role:
  the single state store / program truth. This service is its only AI writer
  on the live path, honouring the existing mode contract exactly:
  full_auto -> ``system_publish(scene=..., active_camera=<slot>)``;
  semi_auto -> ``propose_suggestion(...)`` (60 s approval flow);
  manual    -> never writes (no publish, no suggestion, no preview);
  clone     -> (Phase 27) fires like full_auto through system_publish
               but with the LEARNED knob profile; while grabbed by a
               human it is manual (never writes, shadow-logs with
               rule ``clone_grabbed_no_write``).

Bus topics — VERIFIED in code (Phase 25 audit)
----------------------------------------------
* ``fight.detected``  — canonical name published by
  ``FightDetector._flush_pending_fight_events`` (fight_detector.py). The
  legacy alias ``fight_detected`` (which ObserverDirector subscribed to) is
  bridged by ``app.core.event_names`` alias dispatch in the EventBus, so the
  suspected mismatch could not silently drop events on a running bus — but
  this service subscribes to the canonical published name regardless.
  Payload: {fight_id, teams_involved: [team_id...], intensity, fight_type,
  location, match_id} (+ optional is_clutch / featured_player).
* ``kill_event`` / ``knock_event`` — the NormalizedEvent bus shape
  (feed_adapter ``to_bus_payload`` / simulator): {killer, killerTeam,
  victim, victimTeam, weapon, isKnock, timestamp, ...}. Canonical aliases
  ``kill.occurred`` / ``knock.occurred`` reach this subscriber via the same
  alias dispatch.
* ``intel_alert`` — published by ``VisionIntelligenceService._emit_intel_alert``
  (Phase 24 derivations). This service is its FIRST consumer. Kinds consumed:
  third_party, gatekeep, zone_pull, split, isolated, bleedout_urgency.
  These feed anticipation / pre-positioning (the precombat + zone_pressure
  score terms and the PVW preview) — an intel-only candidate cannot blind-cut
  past an on-air incumbent (see ON_AIR_BASE).
* ``alive_count_update`` — players/teams alive counts when present (drives
  the final-zone hard-lock thresholds; never invented when absent).
* ``replay.candidate`` — PUBLISHED by this service (never a PiP box): when a
  second simultaneous fight cannot take PGM under the grammar, it is handed
  to the replay queue (W3). THE CONTRACT (both sides — publisher here,
  consumer ``app/services/replay/queue.py::handle_candidate`` — must meet
  this exact field list; the joint test is
  ``tests/unit/test_replay_candidate_contract.py``):
    fight_id (str), teams (list[str]), window {started_at, last_activity_at},
    intensity (float), is_clutch (bool), reason (str), source="live_director",
    kind (str: "clutch" when is_clutch else "fight" — never a fabricated
    detector kind), window_start (float, = fight started_at),
    window_end (float, = fight last_activity_at), score (int, =
    int(intensity) — truthful, not the consumer default), moment_id (str,
    = fight_id, so queue dedupe works), team (str, first team of the
    fight), opponents (list[str], remaining teams), slot_number (int,
    challenger candidate's observer slot).

Broadcast grammar (frame-verified, non-negotiable)
--------------------------------------------------
Hard cuts only · map cam only between fights (this service never selects
map_cam autonomously; DirectorService's existing 30 s max-hold enforcement
stays) · NO PiP/split during fights ever — simultaneous fight #2 goes to the
replay queue · min fight hold is the phase timer above · final circle: stay
until the fight resolves.

Attribution honesty
-------------------
Team -> observer slot is resolved ONLY through the observer-slot mapping
(``SlotResolver``, the same source VisionIntelligenceService uses), in both
directions. A team that does not resolve to a slot, or an event with no
resolvable team, is dropped and counted — never guessed.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import structlog

from app.core.event_bus import EventBus, EventPriority, EventRecord
from app.core.event_names import canonical_name
from app.services.director_service import (
    DIRECTOR_SCENES,
    DirectorService,
    default_director_mode,
)
from app.services.vision_intelligence.attribution import (
    SlotResolver,
    TeamAttribution,
)

from .config import LiveDirectorConfig
from .engagement import EngagementState, EngagementTracker
from .storyline import compute_storyline

logger = structlog.get_logger("aegis.live_director")

SUBSCRIBER_ID = "live_director"

#: Bus topics this service consumes (verified — see module docstring).
LIVE_DIRECTOR_EVENT_TYPES = [
    "fight.detected",
    "kill_event",
    "knock_event",
    "intel_alert",
    "alive_count_update",
]

#: Bus topic published for the simultaneous-fight hand-off (W3 consumes).
REPLAY_CANDIDATE_TOPIC = "replay.candidate"

# ── Ported constants (ObserverDirector / POVSelector / SafetyController) ──
#: Absolute switch margins (points).
MARGIN_FIGHT_HOLD = 40.0
MARGIN_DEFAULT = 15.0
MARGIN_CLUTCH_CHALLENGER = 5.0
MARGIN_STALE_INCUMBENT = 10.0
#: Staleness: decay starts after these ages, at -0.5 points/second.
STALE_AFTER_FIGHT_S = 90.0
STALE_AFTER_OTHER_S = 30.0
STALE_DECAY_PER_S = 0.5
#: Score terms.
CLUTCH_BONUS = 25.0
PRIORITY_TEAM_BONUS = 15.0
PRIORITY_PLAYER_BONUS = 15.0
CONTINUITY_PER_APPEARANCE = 4.0
CONTINUITY_CAP = 12.0
#: Incumbent baseline when the on-air team has no live signal of its own
#: (ported from ObserverDirector's current-scene base of 50). This is what
#: stops intel-only anticipation from blind-cutting the program feed.
ON_AIR_BASE = 50.0
#: SafetyController rules.
SWITCH_COOLDOWN_S = 3.0
MAX_SWITCHES_PER_MINUTE = 8
FINAL_ZONE_TOP3_MAX_TEAMS = 3
FINAL_ZONE_TOP5_MAX_TEAMS = 5
FINAL_ZONE_STABILITY_S = 12.0
#: A promoted fight with no kill/knock/fight activity for this long is
#: considered resolved (there is no fight_ended producer on the bus —
#: FightDetector resolves fights internally and publishes nothing).
FIGHT_RESOLVE_IDLE_S = 25.0
#: Kill/knock combat heat (active_combat term when no promoted fight covers
#: the team): rolling window, per-event points, capped.
HEAT_WINDOW_S = 60.0
HEAT_KILL_POINTS = 15.0
HEAT_KNOCK_POINTS = 10.0
HEAT_CAP = 50.0
#: Intel anticipation lifetimes / weights.
INTEL_TTL_S = 90.0
PRECOMBAT_THIRD_PARTY = 20.0
PRECOMBAT_GATEKEEP_OUTSIDE = 15.0
PRECOMBAT_SHAPE = 8.0
PRECOMBAT_CAP = 30.0
ZONE_PRESSURE_PULL = 10.0
ZONE_PRESSURE_GATEKEEP = 10.0
ZONE_PRESSURE_CAP = 20.0
BLEEDOUT_ACTIVE_BONUS = 10.0
#: Decision log ring size.
MAX_DECISION_LOG = 200

#: Min fight hold (seconds) by PhaseTracker phase — a TRUE TIMER.
#: final_circle is handled as a lock-until-resolved, not a number.
MIN_FIGHT_HOLD_BY_PHASE: Dict[str, float] = {
    "plane": 30.0,
    "parachute": 30.0,
    "loot": 30.0,
    "early": 30.0,
    "mid_game": 45.0,
    "late_game": 60.0,
    "final_circle": 60.0,
    "not_started": 30.0,
    "unknown": 45.0,
    "ended": 60.0,
}


def min_fight_hold_s(phase: str) -> float:
    return MIN_FIGHT_HOLD_BY_PHASE.get((phase or "unknown").strip().lower(), 45.0)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_team(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text != "Unknown" else None


# ── Phase 25 P1: default storyline providers ─────────────────────
# Each returns None / empty on ANY failure or missing singleton: the
# storyline layer degrades to multiplier 1.0, never to invented data.
# All are injectable on LiveDirector for tests and coordinator wiring.


def _default_standings_provider() -> Optional[List[Dict[str, Any]]]:
    try:
        from app.services.live_stats import get_live_stats_service

        service = get_live_stats_service()
        if service is None:
            return None
        payload = service.live_standings()
        rows = payload.get("standings") if isinstance(payload, dict) else None
        return list(rows) if rows else None
    except Exception:  # pragma: no cover - defensive
        return None


def _default_match_point_provider() -> Optional[set]:
    try:
        from app.services.match_point import match_point_service

        snapshot = match_point_service.snapshot()
        teams = set()
        evaluation = snapshot.get("last_evaluation") or {}
        for entry in evaluation.get("on_match_point") or []:
            team_id = entry.get("team_id") if isinstance(entry, dict) else None
            if team_id:
                teams.add(str(team_id))
        smash = snapshot.get("smash") or {}
        for entry in smash.get("match_point_teams") or []:
            team_id = entry.get("team_id") if isinstance(entry, dict) else None
            if team_id:
                teams.add(str(team_id))
        return teams or None
    except Exception:  # pragma: no cover - defensive
        return None


class LiveDirector:
    """Unified live director decision service (see module docstring)."""

    def __init__(
        self,
        bus: EventBus,
        director_service: DirectorService,
        slot_resolver: SlotResolver,
        phase_provider: Optional[Callable[[], str]] = None,
        priority_teams: Optional[set] = None,
        priority_players: Optional[set] = None,
        clock: Callable[[], float] = time.time,
        # ── Phase 25 P1 (all optional; honest defaults) ──
        config: Optional[LiveDirectorConfig] = None,
        engagement: Optional[EngagementTracker] = None,
        standings_provider: Optional[Callable[[], Any]] = None,
        match_point_provider: Optional[Callable[[], Any]] = None,
        narrative_provider: Optional[Callable[[], Any]] = None,
        win_prob_provider: Optional[Callable[[], Any]] = None,
        establishing_view_provider: Optional[Callable[[], Optional[str]]] = None,
        # ── Phase 29 (W4): per-feed POV state, slot -> FeedPOVState ──
        # Optional; default None keeps behaviour byte-identical. Used
        # ONLY for read-only exposure (status / decision-log flag) —
        # it never feeds scoring.
        pov_state_provider: Optional[Callable[[int], Any]] = None,
    ):
        self._bus = bus
        self._director = director_service
        self._slot_resolver = slot_resolver
        self._phase_provider = phase_provider or self._default_phase
        self.priority_teams = set(priority_teams or set())
        self.priority_players = set(priority_players or set())
        self._clock = clock

        # ── Phase 25 P1 wiring ──
        self.config = config or LiveDirectorConfig()
        self.engagement = engagement or EngagementTracker(
            config=self.config, clock=clock, phase_provider=self._phase_provider
        )
        # ── Phase 27 (W3): learning-profile seam ──
        # Per-evaluation effective config: == self.config in every
        # factory mode; in clone (not grabbed) the learnable fields
        # are overlaid from the knob provider's learned profile.
        # _score_team reads ONLY _eval_config, so direct _score_team
        # calls (tests) see factory behaviour by default.
        self._eval_config = self.config
        self._eval_learned = False
        try:
            self._base_precombat_confidence = float(
                self.engagement._config.precombat_confidence_enter
            )
        except Exception:  # pragma: no cover - defensive
            self._base_precombat_confidence = (
                self.config.precombat_confidence_enter
            )
        #: Clone-grab inactivity tracking (this director's own clock):
        #: first evaluation observing the grab, and last human write.
        self._clone_grab_seen_at: Optional[float] = None
        self._clone_last_activity_at: Optional[float] = None
        try:
            from app.services.director_service import (
                register_output_write_sink,
            )

            register_output_write_sink(self._on_output_write_for_clone)
        except Exception:  # pragma: no cover - defensive
            pass
        # Storyline data providers: sync callables returning the shapes
        # documented in storyline.py (None = data absent = neutral).
        self._standings_provider = standings_provider or _default_standings_provider
        self._match_point_provider = (
            match_point_provider or _default_match_point_provider
        )
        self._narrative_provider = narrative_provider
        self._win_prob_provider = win_prob_provider
        self._establishing_view_provider = establishing_view_provider
        self._pov_state_provider = pov_state_provider
        #: Per-evaluation storyline context (rebuilt in _evaluate).
        self._story_standings: Dict[str, Dict[str, Any]] = {}
        self._story_match_point: set = set()
        self._story_win_prob: Dict[str, float] = {}
        self._story_win_leader: Optional[str] = None
        #: Finishes per team from the live kill stream (hot streak).
        self._finish_times: Dict[str, List[float]] = {}
        #: Narrative flags noted directly (team_id -> kind -> expiry).
        self._narrative_flags: Dict[str, Dict[str, float]] = {}
        #: Drop-clash watch (team_id -> {poi, teams, at}).
        self._drop_clash_watch: Dict[str, Dict[str, Any]] = {}
        #: Pending context-first beat (see _context_beat_decision).
        self._pending_beat: Optional[Dict[str, Any]] = None

        self._started = False
        self._eval_lock = asyncio.Lock()

        # Slot attribution caches (built from the SlotResolver; the DB
        # resolver has its own 5 s TTL underneath).
        self._team_to_slot: Dict[str, int] = {}
        self._slot_to_attr: Dict[int, TeamAttribution] = {}
        self._attr_built_at: float = -1e18
        self._attr_ttl_s = 5.0

        # Live signal state — real events only, nothing fabricated.
        self._fights: Dict[str, Dict[str, Any]] = {}
        self._combat_heat: Dict[str, List[Tuple[float, float]]] = {}
        self._intel_precombat: Dict[str, Dict[str, float]] = {}
        self._intel_zone_pressure: Dict[str, Dict[str, float]] = {}
        #: Last-seen timestamps for intel, keyed (team_id, kind).
        self._intel_seen: Dict[Tuple[str, str], float] = {}
        self._intel_active: Dict[str, float] = {}
        self._teams_alive: Optional[int] = None
        self._players_alive: Optional[int] = None

        # Program tracking (mirrors DirectorService; resynced every eval).
        self._scene_started_at: float = clock()
        self._last_known_slot: Optional[int] = None
        self._current_fight_id: Optional[str] = None
        self._shown_teams: deque = deque(maxlen=20)
        self._switch_timestamps: List[float] = []

        # Replay hand-offs already emitted (one per fight).
        self._replay_emitted: set = set()

        # Decision log + counters.
        self._decision_log: deque = deque(maxlen=MAX_DECISION_LOG)
        self.stats: Dict[str, int] = {
            "events_received": 0,
            "decisions": 0,
            "switches": 0,
            "suggestions": 0,
            "dropped_unknown_team": 0,
            "replay_candidates_emitted": 0,
            "handler_errors": 0,
        }
        self._last_error = ""
        self._preview_key: Optional[Tuple[Any, ...]] = None

    # ── lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        """Subscribe to the live event topics on the bus. Idempotent."""
        if self._started:
            return
        self._bus.subscribe(
            subscriber_id=SUBSCRIBER_ID,
            callback=self.handle_record,
            event_types=list(LIVE_DIRECTOR_EVENT_TYPES),
        )
        self._started = True
        logger.info(
            "live_director_subscribed", event_types=LIVE_DIRECTOR_EVENT_TYPES
        )

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

    @staticmethod
    def _default_phase() -> str:
        try:
            from app.services.phase_tracker import phase_tracker

            return phase_tracker.current_phase()
        except Exception:  # pragma: no cover - defensive
            return "unknown"

    def _phase(self) -> str:
        try:
            return str(self._phase_provider() or "unknown")
        except Exception:  # pragma: no cover - defensive
            return "unknown"

    # ── Phase 27 (W3): knob-provider seam ────────────────────────
    # Application semantics: the learned profile drives ONLY clone
    # mode while not grabbed and only while the learning mode is not
    # 'off' (the provider itself enforces 'off'). Every other mode
    # resolves learned=False / the untouched config — the factory
    # director, byte-identical. Unregistered provider => constants.

    def _knob_provider(self):
        try:
            from app.services.director_learning.knobs import get_provider

            return get_provider()
        except Exception:  # pragma: no cover - defensive
            return None

    def _margin(self, knob: str, constant: float, learned: bool) -> float:
        provider = self._knob_provider()
        if provider is None:
            return constant
        try:
            return float(provider.effective(knob, learned=learned))
        except Exception:  # pragma: no cover - defensive
            return constant

    def _config_knob(self, knob: str, config_value: float, learned: bool) -> float:
        """Config-backed knob: factory modes keep the config value
        exactly (custom configs preserved); clone-learned reads the
        provider's learned profile."""
        if not learned:
            return config_value
        provider = self._knob_provider()
        if provider is None:
            return config_value
        try:
            return float(provider.effective(knob, learned=True))
        except Exception:  # pragma: no cover - defensive
            return config_value

    def _learned_active(self, mode: str, grabbed: bool) -> bool:
        return mode == "clone" and not grabbed

    def _build_eval_config(self, learned: bool):
        if not learned:
            return self.config
        from dataclasses import replace

        cfg = self.config
        return replace(
            cfg,
            storyline_rank1_uplift=self._config_knob(
                "storyline_rank1_uplift", cfg.storyline_rank1_uplift, True
            ),
            storyline_rank2_uplift=self._config_knob(
                "storyline_rank2_uplift", cfg.storyline_rank2_uplift, True
            ),
            storyline_match_point_uplift=self._config_knob(
                "storyline_match_point_uplift",
                cfg.storyline_match_point_uplift, True,
            ),
            storyline_hot_streak_uplift=self._config_knob(
                "storyline_hot_streak_uplift",
                cfg.storyline_hot_streak_uplift, True,
            ),
            engagement_precombat_points=self._config_knob(
                "engagement_precombat_points",
                cfg.engagement_precombat_points, True,
            ),
            drop_clash_watch_points=self._config_knob(
                "drop_clash_watch_points", cfg.drop_clash_watch_points, True
            ),
        )

    def _on_output_write_for_clone(self, event: Dict[str, Any]) -> None:
        """Human writes while clone-grabbed reset the grab timer.

        Observed via DirectorService's output-write sink registry
        (W1). Uses THIS director's clock, so tests control it.
        """
        try:
            if event.get("source") not in (
                "set_camera", "set_scene", "approve_suggestion",
            ):
                return
            snap = self._director.snapshot()
            if snap.get("mode") == "clone" and snap.get("clone_grabbed"):
                self._clone_last_activity_at = self._clock()
        except Exception:  # pragma: no cover - defensive
            pass

    async def _maybe_auto_release_clone(
        self, snapshot: Dict[str, Any], now: float
    ) -> Dict[str, Any]:
        """Inactivity auto-release, riding the evaluation tick.

        Returns the (possibly refreshed) snapshot. Activity = grab
        observation time or last human output write, whichever later.
        """
        if snapshot.get("mode") != "clone" or not snapshot.get("clone_grabbed"):
            self._clone_grab_seen_at = None
            self._clone_last_activity_at = None
            return snapshot
        if self._clone_grab_seen_at is None:
            self._clone_grab_seen_at = now
            if (
                self._clone_last_activity_at is None
                or self._clone_last_activity_at < now
            ):
                self._clone_last_activity_at = now
            return snapshot
        last = max(
            self._clone_grab_seen_at,
            self._clone_last_activity_at or self._clone_grab_seen_at,
        )
        if now - last >= float(self.config.clone_grab_inactivity_s):
            try:
                await self._director.release_clone_control(
                    updated_by="live_director", reason="inactivity"
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "clone_inactivity_release_failed", error=str(exc)[:200]
                )
                return snapshot
            self._clone_grab_seen_at = None
            self._clone_last_activity_at = None
            return self._director.snapshot()
        return snapshot

    # ── attribution ──────────────────────────────────────────────

    async def _attribution_maps(self) -> Tuple[Dict[str, int], Dict[int, TeamAttribution]]:
        """(team key -> slot, slot -> attribution) from the slot resolver.

        Both team_id and team_name are keys: fight events carry team ids,
        kill/knock payloads carry team names. Cached briefly.
        """
        now = self._clock()
        if self._attr_built_at > 0 and now - self._attr_built_at < self._attr_ttl_s:
            return self._team_to_slot, self._slot_to_attr
        team_to_slot: Dict[str, int] = {}
        slot_to_attr: Dict[int, TeamAttribution] = {}
        for slot in range(1, 17):
            try:
                attr = await self._slot_resolver.resolve(slot)
            except Exception:  # pragma: no cover - defensive
                attr = None
            if attr is None:
                continue
            slot_to_attr[slot] = attr
            team_to_slot[attr.team_id] = slot
            if attr.team_name:
                team_to_slot[attr.team_name] = slot
        self._team_to_slot, self._slot_to_attr = team_to_slot, slot_to_attr
        self._attr_built_at = now
        return team_to_slot, slot_to_attr

    async def _resolve_team(self, raw_team: Any) -> Optional[Tuple[str, int]]:
        """Raw team label -> (canonical team_id, slot) or None (drop)."""
        team = _clean_team(raw_team)
        if team is None:
            return None
        team_to_slot, slot_to_attr = await self._attribution_maps()
        slot = team_to_slot.get(team)
        if slot is None:
            return None
        attr = slot_to_attr[slot]
        return attr.team_id, slot

    # ── bus intake ───────────────────────────────────────────────

    async def handle_record(self, record: EventRecord) -> None:
        """Bus callback: ingest one event, then evaluate. Never raises."""
        event = getattr(record, "event", None)
        raw_type = getattr(event, "event_type", "") if event else ""
        data = getattr(event, "data", None) if event else None
        if not isinstance(data, dict):
            return
        event_type = canonical_name(raw_type)
        self.stats["events_received"] += 1
        try:
            async with self._eval_lock:
                if event_type == "fight.detected":
                    await self._on_fight_detected(data)
                elif event_type in ("kill.occurred", "knock.occurred"):
                    await self._on_combat_event(
                        data, is_knock=(event_type == "knock.occurred")
                    )
                elif raw_type == "intel_alert":
                    await self._on_intel_alert(data)
                elif raw_type == "alive_count_update":
                    self._on_alive_count(data)
                else:
                    return
                await self._evaluate(trigger=raw_type)
        except Exception as exc:
            self.stats["handler_errors"] += 1
            self._last_error = str(exc)[:300]
            logger.error(
                "live_director_handler_error",
                event_type=raw_type,
                error=self._last_error,
            )

    async def _on_fight_detected(self, data: Dict[str, Any]) -> None:
        fight_id = _clean_team(data.get("fight_id")) or ""
        raw_teams = data.get("teams_involved") or data.get("teams") or []
        teams: List[str] = []
        for raw in raw_teams:
            resolved = await self._resolve_team(raw)
            if resolved is None:
                self.stats["dropped_unknown_team"] += 1
                logger.info(
                    "live_director_fight_team_dropped_unmapped",
                    fight_id=fight_id,
                    team=str(raw),
                    reason="team does not resolve to an observer slot; "
                    "refusing to guess its camera",
                )
                continue
            if resolved[0] not in teams:
                teams.append(resolved[0])
        if not fight_id or not teams:
            return
        try:
            intensity = float(data.get("intensity") or 50.0)
        except (TypeError, ValueError):
            intensity = 50.0
        now = self._clock()
        existing = self._fights.get(fight_id)
        if existing is None:
            self._fights[fight_id] = {
                "fight_id": fight_id,
                "teams": teams,
                "intensity": intensity,
                "is_clutch": bool(data.get("is_clutch")),
                "featured_player": _clean_team(
                    data.get("featured_player") or data.get("player_id")
                ),
                "started_at": now,
                "last_activity_at": now,
            }
        else:
            existing["last_activity_at"] = now
            existing["intensity"] = max(existing["intensity"], intensity)
            existing["is_clutch"] = existing["is_clutch"] or bool(
                data.get("is_clutch")
            )
            for team_id in teams:
                if team_id not in existing["teams"]:
                    existing["teams"].append(team_id)
        teams_alive = data.get("teams_alive")
        if isinstance(teams_alive, int) and teams_alive > 0:
            self._teams_alive = teams_alive
        # P1-5: a promoted fight moves its teams to ACTIVE_COMBAT (or
        # CLUTCH when the promotion carries a verified clutch flag).
        is_clutch = bool(data.get("is_clutch"))
        for team_id in teams:
            self.engagement.note_promoted_fight(
                team_id, fight_id, is_clutch=is_clutch, now=now
            )

    async def _on_combat_event(self, data: Dict[str, Any], is_knock: bool) -> None:
        """Kill/knock: combat heat for the killer team + fight activity."""
        killer_raw = (
            data.get("killerTeam") or data.get("killer_team_id")
            or data.get("killer_team") or data.get("team")
        )
        resolved = await self._resolve_team(killer_raw)
        if resolved is None:
            # A slot stamped on the payload identifies the observing team
            # only — it is NOT the killer's camera, so it is never used to
            # attribute the kill. Unknown killer team -> drop + count.
            self.stats["dropped_unknown_team"] += 1
            return
        team_id, _slot = resolved
        now = self._clock()
        points = HEAT_KNOCK_POINTS if is_knock else HEAT_KILL_POINTS
        series = self._combat_heat.setdefault(team_id, [])
        series.append((now, points))
        # P1: finishes feed the storyline hot-streak factor; combat
        # activity refreshes the engagement tracker's fight clock.
        if not is_knock:
            finishes = self._finish_times.setdefault(team_id, [])
            finishes.append(now)
            self._finish_times[team_id] = [
                t for t in finishes if now - t <= self.config.hot_streak_window_s
            ]
        self.engagement.note_combat_activity(team_id, now=now)
        # A kill/knock involving a team in an active fight refreshes that
        # fight's activity clock (fight resolution is inactivity-based).
        victim_raw = (
            data.get("victimTeam") or data.get("victim_team_id")
            or data.get("victim_team")
        )
        victim = await self._resolve_team(victim_raw)
        involved = {team_id} | ({victim[0]} if victim else set())
        for fight in self._fights.values():
            if involved & set(fight["teams"]):
                fight["last_activity_at"] = now

    async def _on_intel_alert(self, data: Dict[str, Any]) -> None:
        """Phase 24 intel_alert — FIRST consumer (anticipation, not cuts)."""
        kind = str(data.get("kind") or "")
        now = self._clock()

        async def note_precombat(raw_team: Any, weight: float) -> None:
            resolved = await self._resolve_team(raw_team)
            if resolved is None:
                self.stats["dropped_unknown_team"] += 1
                return
            entry = self._intel_precombat.setdefault(resolved[0], {})
            entry[kind] = max(entry.get(kind, 0.0), weight)
            self._intel_ts(resolved[0], kind, now)

        async def note_zone_pressure(raw_team: Any, weight: float) -> None:
            resolved = await self._resolve_team(raw_team)
            if resolved is None:
                self.stats["dropped_unknown_team"] += 1
                return
            entry = self._intel_zone_pressure.setdefault(resolved[0], {})
            entry[kind] = max(entry.get(kind, 0.0), weight)
            self._intel_ts(resolved[0], kind, now)

        async def note_convergence(raw_team: Any) -> None:
            # P1-5: the same intel that feeds the precombat score term
            # is convergence evidence for the engagement tracker.
            resolved = await self._resolve_team(raw_team)
            if resolved is not None:
                self.engagement.note_convergence(
                    resolved[0], kind, now=now, signals=[f"intel_alert:{kind}"]
                )

        if kind == "third_party":
            for raw in data.get("teams") or []:
                await note_precombat(raw, PRECOMBAT_THIRD_PARTY)
                await note_convergence(raw)
        elif kind == "gatekeep":
            # Observed team holds the edge (zone pressure); the teams
            # outside must come to it (pre-combat anticipation).
            if data.get("team_id"):
                await note_zone_pressure(data["team_id"], ZONE_PRESSURE_GATEKEEP)
            for raw in data.get("outside_teams") or []:
                await note_precombat(raw, PRECOMBAT_GATEKEEP_OUTSIDE)
                await note_convergence(raw)
        elif kind == "zone_pull":
            for raw in data.get("winners") or []:
                await note_zone_pressure(
                    raw.get("team_id") if isinstance(raw, dict) else raw,
                    ZONE_PRESSURE_PULL,
                )
            for raw in data.get("losers") or []:
                await note_zone_pressure(
                    raw.get("team_id") if isinstance(raw, dict) else raw,
                    ZONE_PRESSURE_PULL,
                )
        elif kind in ("split", "isolated"):
            if data.get("team_id"):
                await note_precombat(data["team_id"], PRECOMBAT_SHAPE)
                await note_convergence(data["team_id"])
        elif kind == "drop_clash":
            # P1-6: predicted/observed drop clash (flight_path service).
            # At plane/loot phase the clashing teams gain watch
            # priority (a bounded precombat weight — enough to
            # pre-position PVW on the clash, never to cut PGM) and are
            # recorded on the director's drop-clash watch surface.
            poi = data.get("poi")
            clash_teams: List[str] = []
            for raw in data.get("teams") or []:
                resolved = await self._resolve_team(raw)
                if resolved is None:
                    self.stats["dropped_unknown_team"] += 1
                    continue
                team_id = resolved[0]
                clash_teams.append(team_id)
                entry = self._intel_precombat.setdefault(team_id, {})
                _snap = self._director.snapshot()
                _learned = self._learned_active(
                    _snap.get("mode", ""), bool(_snap.get("clone_grabbed"))
                )
                entry[kind] = max(
                    entry.get(kind, 0.0),
                    self._config_knob(
                        "drop_clash_watch_points",
                        self.config.drop_clash_watch_points, _learned,
                    ),
                )
                self._intel_ts(team_id, kind, now)
                self._drop_clash_watch[team_id] = {
                    "team_id": team_id,
                    "poi": poi,
                    "teams": list(data.get("teams") or []),
                    "basis": data.get("basis"),
                    "at": now,
                }
            if clash_teams:
                self.stats["drop_clashes_seen"] = (
                    self.stats.get("drop_clashes_seen", 0) + 1
                )
        elif kind == "bleedout_urgency":
            resolved = await self._resolve_team(data.get("team_id"))
            if resolved is None:
                self.stats["dropped_unknown_team"] += 1
            else:
                self._intel_active[resolved[0]] = now
        # Unknown kinds are ignored explicitly: they carry no director
        # signal this service knows how to score.

    def _intel_ts(self, team_id: str, kind: str, ts: float) -> None:
        self._intel_seen[(team_id, kind)] = ts

    def _on_alive_count(self, data: Dict[str, Any]) -> None:
        for key in ("teams_alive", "teams"):
            value = data.get(key)
            if isinstance(value, int) and value > 0:
                self._teams_alive = value
                break
        value = data.get("players_alive") or data.get("alive_count")
        if isinstance(value, int) and value > 0:
            self._players_alive = value

    # ── scoring (unified Interest Score, named terms) ────────────

    def _prune(self, now: float) -> None:
        """Expire resolved fights / stale heat / stale intel."""
        for fight_id, fight in list(self._fights.items()):
            if now - fight["last_activity_at"] > FIGHT_RESOLVE_IDLE_S:
                del self._fights[fight_id]
                # P1-5: the engagement tracker leaves ACTIVE_COMBAT /
                # CLUTCH for this fight's teams in step with the
                # director's own fight resolution.
                self.engagement.note_fight_resolved(fight_id=fight_id, now=now)
                if self._current_fight_id == fight_id:
                    self._current_fight_id = None
        # P1-5: honest decay — silent teams fall back to baseline.
        self.engagement.tick(now)
        for team_id, series in list(self._combat_heat.items()):
            fresh = [(ts, p) for ts, p in series if now - ts <= HEAT_WINDOW_S]
            if fresh:
                self._combat_heat[team_id] = fresh
            else:
                del self._combat_heat[team_id]
        for store in (self._intel_precombat, self._intel_zone_pressure):
            for team_id, kinds in list(store.items()):
                for kind in list(kinds):
                    if now - self._intel_seen.get((team_id, kind), 0.0) > INTEL_TTL_S:
                        del kinds[kind]
                if not kinds:
                    del store[team_id]
        self._intel_active = {
            t: ts for t, ts in self._intel_active.items() if now - ts <= INTEL_TTL_S
        }

    # ── Phase 25 P1: storyline context ───────────────────────────

    def _refresh_storyline_context(self, now: float) -> None:
        """Rebuild the per-evaluation storyline inputs from the
        injected providers. Any provider failure or absence leaves the
        corresponding input empty — W then stays exactly 1.0."""
        self._story_standings = {}
        self._story_match_point = set()
        self._story_win_prob = {}
        self._story_win_leader = None
        try:
            raw = self._standings_provider() if self._standings_provider else None
            rows = raw.get("standings") if isinstance(raw, dict) else raw
            for row in rows or []:
                if isinstance(row, dict) and row.get("team_id"):
                    self._story_standings[str(row["team_id"])] = row
        except Exception:  # pragma: no cover - defensive
            self._story_standings = {}
        try:
            raw = (
                self._match_point_provider() if self._match_point_provider else None
            )
            if isinstance(raw, dict):
                entries = raw.get("on_match_point") or raw.get("teams") or []
                raw = {
                    str(e.get("team_id"))
                    for e in entries
                    if isinstance(e, dict) and e.get("team_id")
                }
            self._story_match_point = {str(t) for t in (raw or set())}
        except Exception:  # pragma: no cover - defensive
            self._story_match_point = set()
        try:
            raw = self._win_prob_provider() if self._win_prob_provider else None
            rows = raw.get("teams") if isinstance(raw, dict) else raw
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict) and row.get("team_id") is not None:
                        prob = row.get("win_probability")
                        if isinstance(prob, (int, float)):
                            self._story_win_prob[str(row["team_id"])] = float(prob)
            elif isinstance(raw, dict):
                for team_id, prob in raw.items():
                    if isinstance(prob, (int, float)):
                        self._story_win_prob[str(team_id)] = float(prob)
            if self._story_win_prob:
                self._story_win_leader = max(
                    self._story_win_prob, key=lambda t: self._story_win_prob[t]
                )
        except Exception:  # pragma: no cover - defensive
            self._story_win_prob = {}
            self._story_win_leader = None

    def _narrative_flags_for(self, team_id: str, now: float) -> List[str]:
        """Active narrative flag kinds for one team: noted flags
        (TTL-bound) merged with the injected provider's current set."""
        flags: List[str] = []
        noted = self._narrative_flags.get(team_id) or {}
        for kind, expiry in list(noted.items()):
            if now <= expiry:
                flags.append(kind)
            else:
                del noted[kind]
        if self._narrative_provider is not None:
            try:
                raw = self._narrative_provider()
                provided: List[str] = []
                if isinstance(raw, dict):
                    value = raw.get(team_id) or []
                    if isinstance(value, dict):
                        provided = [str(k) for k in value]
                    elif isinstance(value, (list, tuple, set)):
                        provided = [str(k) for k in value]
                elif isinstance(raw, (list, tuple)):
                    for entry in raw:
                        if not isinstance(entry, dict):
                            continue
                        if str(entry.get("team_id") or "") != team_id:
                            continue
                        kind = entry.get("kind") or entry.get("story_type")
                        if kind:
                            provided.append(str(kind))
                for kind in provided:
                    if kind not in flags:
                        flags.append(kind)
            except Exception:  # pragma: no cover - defensive
                pass
        return flags

    def _recent_finishes(self, team_id: str, now: float) -> int:
        window = self.config.hot_streak_window_s
        times = [
            t for t in self._finish_times.get(team_id, []) if now - t <= window
        ]
        if team_id in self._finish_times:
            self._finish_times[team_id] = times
        return len(times)

    # ── public P1 inputs (coordinator / vision wiring) ───────────

    def note_narrative_flag(
        self, team_id: str, kind: str, *, ttl_s: Optional[float] = None
    ) -> None:
        """Record a storyteller narrative flag as active for a team
        (rivalry / milestone / comeback / ...). TTL-bound; an expired
        flag contributes nothing. Flags with no team are dropped."""
        team = _clean_team(team_id)
        flag = str(kind or "").strip().lower()
        if team is None or not flag:
            return
        ttl = self.config.narrative_flag_ttl_s if ttl_s is None else float(ttl_s)
        self._narrative_flags.setdefault(team, {})[flag] = self._clock() + ttl

    async def note_potential_fight(
        self,
        team_ids: List[str],
        confidence: float,
        signals: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Feed a FightDetector PotentialFight reading (the vision
        layer's convergence signal) into the engagement tracker, then
        evaluate so PRE_COMBAT pre-positions PVW. PRE_COMBAT alone
        never cuts PGM — the evaluation applies the normal margins
        against the on-air base. Returns the decision entry, if any."""
        now = self._clock()
        # Phase 27 (W3) seam: the PRE_COMBAT confidence threshold is
        # read by the engagement tracker at intake — sync the tracker's
        # field to this mode's effective value first (factory value in
        # every non-clone mode; learned value only in clone).
        try:
            _snap = self._director.snapshot()
            _learned = self._learned_active(
                _snap.get("mode", ""), bool(_snap.get("clone_grabbed"))
            )
            self.engagement._config.precombat_confidence_enter = (
                self._config_knob(
                    "precombat_confidence_enter",
                    self._base_precombat_confidence, _learned,
                )
            )
        except Exception:  # pragma: no cover - defensive
            pass
        resolved_ids: List[str] = []
        for raw in team_ids or []:
            resolved = await self._resolve_team(raw)
            if resolved is not None:
                resolved_ids.append(resolved[0])
        if resolved_ids:
            self.engagement.note_potential_fight(
                resolved_ids, confidence, now=now, signals=signals
            )
        async with self._eval_lock:
            return await self._evaluate(trigger="potential_fight")

    async def note_rotation(
        self,
        team_id: str,
        *,
        active: bool = True,
        occupied_by: Optional[str] = None,
        poi: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Feed a RotationTracker movement reading into the engagement
        tracker (ROTATING, or PRE_COMBAT into an occupied POI)."""
        resolved = await self._resolve_team(team_id)
        if resolved is None:
            self.stats["dropped_unknown_team"] += 1
            return None
        occupied_id = None
        if occupied_by:
            occupied = await self._resolve_team(occupied_by)
            occupied_id = occupied[0] if occupied else None
        self.engagement.note_rotation(
            resolved[0],
            active=active,
            occupied_by=occupied_id,
            poi=poi,
            now=self._clock(),
        )
        async with self._eval_lock:
            return await self._evaluate(trigger="rotation")

    def engagement_snapshot(self) -> Dict[str, Any]:
        """Current per-team engagement states + transition log."""
        return self.engagement.snapshot()

    # ── Phase 29 (W4): POV exposure (read-only, never scoring) ───

    def set_pov_state_provider(
        self, provider: Optional[Callable[[int], Any]]
    ) -> None:
        """Inject/replace the POV provider after construction (the
        lifespan builds the monitor after the director)."""
        self._pov_state_provider = provider

    def pov_summary(self) -> List[Dict[str, Any]]:
        """Per-feed POV summary: slot, spectated player name/index,
        identity_known. Empty when no provider is wired — honest,
        never fabricated. Slots are probed 1..16 through the provider
        (the monitor returns None for unmonitored slots)."""
        if self._pov_state_provider is None:
            return []
        out: List[Dict[str, Any]] = []
        for slot in range(1, 17):
            try:
                state = self._pov_state_provider(slot)
            except Exception:  # pragma: no cover - defensive
                state = None
            if state is None:
                continue
            spectated = None
            try:
                spectated = state.spectated_player()
            except Exception:  # pragma: no cover - defensive
                spectated = None
            out.append({
                "slot_number": int(state.slot_number),
                "source_id": state.source_id,
                "team_id": state.team_id,
                "spectated_index": state.spectated_index,
                "spectated_name": (
                    spectated.name if spectated is not None else None
                ),
                "identity_known": bool(state.identity_known),
            })
        return out

    def _pov_on_fight(
        self, slot: Optional[int], team_has_fight: bool
    ) -> Optional[bool]:
        """Whether the POV on the chosen feed is a fighting player.

        True only when BOTH facts are real: the feed's POV state is
        known (identity resolved, spectated player present) AND that
        player's team carries a promoted fight in this evaluation.
        None when the POV is unknown — never guessed. Read-only: this
        flag is journal colour, it never changes a score."""
        if self._pov_state_provider is None or slot is None:
            return None
        try:
            state = self._pov_state_provider(int(slot))
        except Exception:  # pragma: no cover - defensive
            return None
        if state is None or not state.identity_known:
            return None
        if state.spectated_player() is None:
            return None
        return bool(team_has_fight)

    def _score_team(self, team_id: str, now: float) -> Dict[str, Any]:
        """Unified Interest Score for one team, with named breakdown terms.

        active_combat  — promoted-fight intensity, else kill/knock heat,
                         else a live bleed-out urgency bonus.
        precombat      — Phase 24 intel anticipation (third-party /
                         gatekeep / team shape), capped.
        zone_pressure  — zone-pull / gatekeep positioning pressure, capped.
        clutch         — +25 when the team's fight is a clutch.
        priority       — +15 priority team / +15 priority player.
        """
        fight = None
        for candidate in self._fights.values():
            if team_id in candidate["teams"]:
                if fight is None or candidate["intensity"] > fight["intensity"]:
                    fight = candidate
        if fight is not None:
            active = float(fight["intensity"])
        else:
            heat = sum(p for _ts, p in self._combat_heat.get(team_id, []))
            active = min(HEAT_CAP, heat)
            if team_id in self._intel_active:
                active = max(active, BLEEDOUT_ACTIVE_BONUS)
        # P1-5: PRE_COMBAT engagement contributes to the precombat
        # term via max (the intel and tracker views of the same
        # convergence are never double-counted), still under the cap.
        engagement_state = self.engagement.state_of(team_id)
        intel_precombat = sum(self._intel_precombat.get(team_id, {}).values())
        engagement_precombat = (
            self._eval_config.engagement_precombat_points
            if engagement_state == EngagementState.PRE_COMBAT
            else 0.0
        )
        precombat = min(
            PRECOMBAT_CAP, max(intel_precombat, engagement_precombat)
        )
        zone_pressure = min(
            ZONE_PRESSURE_CAP,
            sum(self._intel_zone_pressure.get(team_id, {}).values()),
        )
        clutch = CLUTCH_BONUS if (fight and fight["is_clutch"]) else 0.0
        priority = 0.0
        if team_id in self.priority_teams:
            priority += PRIORITY_TEAM_BONUS
        if fight and fight.get("featured_player") in self.priority_players:
            priority += PRIORITY_PLAYER_BONUS
        base_breakdown = {
            "precombat": round(precombat, 1),
            "active_combat": round(active, 1),
            "zone_pressure": round(zone_pressure, 1),
            "clutch": round(clutch, 1),
            "priority": round(priority, 1),
        }
        base_score = sum(base_breakdown.values())
        # P1-4: storyline multiplier W over the fight-signal base ONLY.
        # base == 0 -> score 0 regardless of W (storyline alone can
        # never manufacture a switch). Missing data -> W == 1.0 with
        # an empty factor list, stated in the breakdown.
        storyline = compute_storyline(
            team_id,
            standings_row=self._story_standings.get(team_id),
            on_match_point=team_id in self._story_match_point,
            recent_finishes=self._recent_finishes(team_id, now),
            narrative_flags=self._narrative_flags_for(team_id, now),
            win_probability=self._story_win_prob.get(team_id),
            win_prob_is_leader=(team_id == self._story_win_leader),
            config=self._eval_config,
        )
        score = round(base_score * storyline["multiplier"], 1)
        breakdown = {
            **base_breakdown,
            "storyline_bonus": round(score - round(base_score, 1), 1),
            "storyline": {
                "multiplier": storyline["multiplier"],
                "factors": storyline["factors"],
            },
        }
        return {
            "team_id": team_id,
            "score": score,
            "base_score": round(base_score, 1),
            "breakdown": breakdown,
            "storyline": breakdown["storyline"],
            "engagement_state": engagement_state.value,
            "fight_id": fight["fight_id"] if fight else None,
            "is_clutch": bool(fight and fight["is_clutch"]),
            "has_fight": fight is not None,
        }

    def _incumbent_score(
        self, team_id: Optional[str], scored: Optional[Dict[str, Any]], now: float
    ) -> Dict[str, Any]:
        """Current on-air score: unified score + narrative continuity
        (ported) + on-air base when signalless, minus staleness decay."""
        breakdown = dict(
            scored["breakdown"]
            if scored
            else {
                "precombat": 0.0,
                "active_combat": 0.0,
                "zone_pressure": 0.0,
                "clutch": 0.0,
                "priority": 0.0,
                "storyline_bonus": 0.0,
                "storyline": {"multiplier": 1.0, "factors": []},
            }
        )
        base = float(scored["score"]) if scored else 0.0
        on_air_base = 0.0
        if base <= 0.0:
            on_air_base = ON_AIR_BASE
            base = ON_AIR_BASE
        appearances = sum(1 for t in self._shown_teams if t == team_id)
        continuity = min(CONTINUITY_CAP, appearances * CONTINUITY_PER_APPEARANCE)
        age = now - self._scene_started_at
        stale_after = (
            STALE_AFTER_FIGHT_S
            if (scored and scored["has_fight"])
            else STALE_AFTER_OTHER_S
        )
        staleness = max(0.0, (age - stale_after) * STALE_DECAY_PER_S)
        breakdown["on_air_base"] = round(on_air_base, 1)
        breakdown["narrative_continuity"] = round(continuity, 1)
        breakdown["staleness_penalty"] = round(-staleness, 1)
        return {
            "team_id": team_id,
            "score": round(base + continuity - staleness, 1),
            "breakdown": breakdown,
            "stale": age > stale_after,
            "scene_age_s": round(age, 1),
            "has_fight": bool(scored and scored["has_fight"]),
        }

    # ── decision engine ──────────────────────────────────────────

    async def evaluate_now(self, trigger: str = "manual") -> Optional[Dict[str, Any]]:
        """Run one evaluation outside a bus event (tests / ops)."""
        async with self._eval_lock:
            return await self._evaluate(trigger=trigger)

    async def _evaluate(self, trigger: str) -> Optional[Dict[str, Any]]:
        now = self._clock()
        self._prune(now)
        # P1: storyline inputs are rebuilt once per evaluation.
        self._refresh_storyline_context(now)
        snapshot = self._director.snapshot()
        # Phase 27 (W3): inactivity auto-release rides this tick.
        snapshot = await self._maybe_auto_release_clone(snapshot, now)
        mode = snapshot.get("mode") or default_director_mode()
        grabbed = bool(snapshot.get("clone_grabbed")) if mode == "clone" else False
        # Learned profile drives ONLY clone + not grabbed.
        learned = self._learned_active(mode, grabbed)
        self._eval_learned = learned
        self._eval_config = self._build_eval_config(learned)
        current_slot = snapshot.get("active_camera")
        phase = self._phase()
        self.engagement.observe_phase(phase)

        _team_to_slot, slot_to_attr = await self._attribution_maps()
        current_attr = slot_to_attr.get(current_slot) if current_slot else None
        current_team = current_attr.team_id if current_attr else None

        # Resync with DirectorService: an external (operator) camera change
        # resets our scene clock and current-fight tracking. The FIRST
        # sync only records the slot — the scene clock runs from service
        # start, so a long-quiet opening scene is honestly stale.
        if current_slot != self._last_known_slot:
            if self._last_known_slot is not None:
                self._scene_started_at = now
                if current_team is None or not any(
                    current_team in f["teams"] for f in self._fights.values()
                ):
                    self._current_fight_id = None
            self._last_known_slot = current_slot

        # If the on-air team is in an active fight, that fight is current.
        if current_team is not None:
            for fight in self._fights.values():
                if current_team in fight["teams"]:
                    self._current_fight_id = fight["fight_id"]
                    break

        # P1-7: a pending context-first beat completes (or is aborted
        # by a hot fight / mode change) before any new decision.
        if self._pending_beat is not None:
            beat_entry = await self._service_pending_beat(
                trigger=trigger, mode=mode, phase=phase, now=now,
                current_team=current_team, current_slot=current_slot,
                clone_grabbed=grabbed,
            )
            if beat_entry is not None:
                return beat_entry
            if self._pending_beat is not None:
                return None  # beat still running; PGM stays on the beat

        # Score every team with a live signal.
        signalled = (
            set(self._combat_heat)
            | set(self._intel_precombat)
            | set(self._intel_zone_pressure)
            | set(self._intel_active)
            | {t for f in self._fights.values() for t in f["teams"]}
            # P1-5/P1-6: PRE_COMBAT teams and drop-clash watch teams
            # are candidates (for PVW pre-positioning above all).
            | set(self.engagement.precombat_teams())
            | set(self._drop_clash_watch)
        )
        scored: Dict[str, Dict[str, Any]] = {}
        for team_id in signalled:
            resolved_slot = _team_to_slot.get(team_id)
            if resolved_slot is None:
                continue  # unresolvable -> never a candidate (counted at intake)
            entry = self._score_team(team_id, now)
            entry["slot"] = resolved_slot
            scored[team_id] = entry

        incumbent_scored = scored.get(current_team) if current_team else None
        incumbent = self._incumbent_score(current_team, incumbent_scored, now)

        challengers = [
            entry
            for team_id, entry in scored.items()
            if team_id != current_team
        ]
        # Deterministic tie-break (score, then team_id): the director
        # must never flip a coin between equal challengers.
        challengers.sort(key=lambda e: (-e["score"], e["team_id"]))
        best = challengers[0] if challengers else None

        # PVW preview: the top challenger, updated whenever it changes —
        # even when grammar blocks the cut. Manual mode writes nothing;
        # a grabbed clone writes nothing either (manual semantics).
        if mode != "manual" and not grabbed:
            await self._update_preview(best, now)

        if best is None:
            return None  # idle: no candidate, no decision, nothing invented

        base_entry = {
            "timestamp": _utcnow_iso(),
            "trigger": trigger,
            "mode": mode,
            "phase": phase,
            "candidates": [
                {
                    "team_id": e["team_id"],
                    "slot": e["slot"],
                    "score": e["score"],
                    "base_score": e.get("base_score"),
                    "breakdown": e["breakdown"],
                    "storyline": e.get("storyline"),
                    "engagement_state": e.get("engagement_state"),
                    "fight_id": e["fight_id"],
                    "is_incumbent": e["team_id"] == current_team,
                }
                for e in sorted(
                    scored.values(), key=lambda e: e["score"], reverse=True
                )
            ],
            "incumbent": {
                "team_id": current_team,
                "slot": current_slot,
                "score": incumbent["score"],
                "breakdown": incumbent["breakdown"],
            },
            "challenger": {
                "team_id": best["team_id"],
                "slot": best["slot"],
                "score": best["score"],
                "fight_id": best["fight_id"],
                "storyline": best.get("storyline"),
                "engagement_state": best.get("engagement_state"),
            },
            # Phase 29 (W4): is the on-air feed's spectated player in
            # a fight? None when POV is unknown; never affects scoring.
            "pov_on_fight": self._pov_on_fight(
                current_slot,
                bool(incumbent_scored and incumbent_scored.get("has_fight")),
            ),
        }

        if mode == "manual":
            return self._log_decision(
                {**base_entry, "outcome": "hold", "rule": "manual_mode_no_write",
                 "margin_required": None,
                 "margin_actual": round(best["score"] - incumbent["score"], 1),
                 "reason": "MANUAL mode: the live director never writes."}
            )

        if grabbed:
            # Phase 27 (W3): clone grabbed — EXACTLY manual semantics
            # (no publish, no suggestion, no preview — preview already
            # skipped above), but evaluation + shadow logging continue.
            return self._log_decision(
                {**base_entry, "outcome": "hold",
                 "rule": "clone_grabbed_no_write",
                 "margin_required": None,
                 "margin_actual": round(best["score"] - incumbent["score"], 1),
                 "reason": (
                     "CLONE mode grabbed by "
                     f"{snapshot.get('clone_grabbed_by')}: the human "
                     "drives; the live director never writes while grabbed."
                 )}
            )

        # ── grammar + safety gates (order matters) ──
        scene_age = now - self._scene_started_at
        current_fight = (
            self._fights.get(self._current_fight_id)
            if self._current_fight_id
            else None
        )
        challenger_same_fight = bool(
            current_fight and best["fight_id"] == current_fight["fight_id"]
        )
        margin_actual = round(best["score"] - incumbent["score"], 1)

        block_rule: Optional[str] = None
        # Phase 27 (W3): margins resolve through the knob provider
        # PER EVALUATION (learned profile only in ungrabbed clone;
        # factory constants everywhere else — identical decisions).
        margin_required: float = self._margin(
            "margin_default", MARGIN_DEFAULT, learned
        )

        if (
            phase == "final_circle"
            and current_fight is not None
            and not challenger_same_fight
        ):
            block_rule = "final_circle_lock_until_fight_resolves"
        elif (
            self._teams_alive is not None
            and self._teams_alive <= FINAL_ZONE_TOP3_MAX_TEAMS
            and (phase == "final_circle" or current_fight is not None)
            and not challenger_same_fight
        ):
            block_rule = "final_zone_hard_lock_top3"
        elif (
            self._teams_alive is not None
            and self._teams_alive <= FINAL_ZONE_TOP5_MAX_TEAMS
            and (phase == "final_circle" or current_fight is not None)
            and scene_age < FINAL_ZONE_STABILITY_S
            and not challenger_same_fight
        ):
            block_rule = "final_zone_stability_top5"
        elif (
            self._switch_timestamps
            and now - self._switch_timestamps[-1] < SWITCH_COOLDOWN_S
        ):
            block_rule = "safety_switch_cooldown_3s"
        elif (
            len([t for t in self._switch_timestamps if now - t < 60.0])
            >= MAX_SWITCHES_PER_MINUTE
        ):
            block_rule = "safety_max_switches_per_minute_8"

        if block_rule is not None:
            entry = self._log_decision(
                {**base_entry, "outcome": "rejected_challenger",
                 "rule": block_rule, "margin_required": None,
                 "margin_actual": margin_actual,
                 "reason": f"Challenger {best['team_id']} blocked by {block_rule}."}
            )
            await self._maybe_replay_handoff(best, current_fight)
            return entry

        # ── margin rules (ported absolute margins) ──
        in_hold_window = bool(
            current_fight is not None
            and scene_age < min_fight_hold_s(phase)
            and not challenger_same_fight
        )
        if in_hold_window:
            margin_required = self._margin(
                "margin_fight_hold", MARGIN_FIGHT_HOLD, learned
            )
            rule = "margin_fight_hold_40"
        elif best["is_clutch"]:
            margin_required = self._margin(
                "margin_clutch", MARGIN_CLUTCH_CHALLENGER, learned
            )
            rule = "margin_clutch_5"
        elif incumbent["stale"]:
            margin_required = self._margin(
                "margin_stale_incumbent", MARGIN_STALE_INCUMBENT, learned
            )
            rule = "margin_stale_10"
        else:
            margin_required = self._margin(
                "margin_default", MARGIN_DEFAULT, learned
            )
            rule = "margin_default_15"

        if margin_actual < margin_required:
            entry = self._log_decision(
                {**base_entry, "outcome": "rejected_challenger", "rule": rule,
                 "margin_required": margin_required,
                 "margin_actual": margin_actual,
                 "reason": (
                     f"Challenger {best['team_id']} score {best['score']} vs "
                     f"incumbent {incumbent['score']}: margin {margin_actual} "
                     f"< required {margin_required} ({rule})."
                 )}
            )
            await self._maybe_replay_handoff(best, current_fight)
            return entry

        # ── the challenger wins: act per mode ──
        reason = (
            f"Live director: {best['team_id']} wins on unified Interest Score "
            f"{best['score']} vs incumbent {incumbent['score']} "
            f"(margin {margin_actual} >= {margin_required}, {rule}); "
            f"fight={best['fight_id'] or 'combat-heat/intel'}, "
            f"breakdown={best['breakdown']}."
        )
        if mode == "semi_auto":
            suggestion = await self._director.propose_suggestion(
                suggestion_type="scene",
                payload={
                    "scene": "main",
                    "active_camera": best["slot"],
                    "team_id": best["team_id"],
                },
                reason=(
                    f"{reason} Approve to put slot {best['slot']} "
                    f"({best['team_id']}) on program."
                ),
                confidence=max(0.0, min(1.0, best["score"] / 100.0)),
                created_by="live_director",
            )
            if suggestion is None:
                return self._log_decision(
                    {**base_entry, "outcome": "hold",
                     "rule": "semi_auto_suggestion_noop_or_duplicate",
                     "margin_required": margin_required,
                     "margin_actual": margin_actual,
                     "reason": "Semi-auto suggestion was a no-op/duplicate "
                     "against current state; queue left honestly unchanged."}
                )
            self.stats["suggestions"] += 1
            return self._log_decision(
                {**base_entry, "outcome": "suggestion", "rule": rule,
                 "margin_required": margin_required,
                 "margin_actual": margin_actual, "reason": reason,
                 "suggestion_id": suggestion.get("id"),
                 "context_beat": {
                     "taken": False,
                     "reason": "semi_auto_suggestion_flow_no_beat",
                     "scene": None,
                     "duration_s": self.config.context_beat_duration_s,
                 }}
            )

        # P1-7: context-first beat — an ANTICIPATION cut (challenger
        # arrived via PRE_COMBAT, no promoted fight yet) may first
        # take the establishing view for a bounded beat, then the
        # fight feed. Hot cuts, the final circle and missing views
        # skip it; every decision is logged (see _context_beat_decision).
        beat = self._context_beat_decision(
            best, phase=phase, current_scene=snapshot.get("scene")
        )
        if beat["taken"]:
            published = await self._director.system_publish(
                scene=beat["scene"], updated_by="live_director"
            )
            if published.get("changed"):
                self._pending_beat = {
                    "team_id": best["team_id"],
                    "slot": best["slot"],
                    "fight_id": best["fight_id"],
                    "scene": beat["scene"],
                    "started_at": now,
                    "completes_at": now + beat["duration_s"],
                    "duration_s": beat["duration_s"],
                    "rule": rule,
                }
                self.stats["context_beats_taken"] = (
                    self.stats.get("context_beats_taken", 0) + 1
                )
                return self._log_decision(
                    {**base_entry, "outcome": "context_beat", "rule": rule,
                     "margin_required": margin_required,
                     "margin_actual": margin_actual,
                     "reason": (
                         f"Context-first beat: establishing view "
                         f"{beat['scene']} for {beat['duration_s']:g}s "
                         f"before the anticipation cut to {best['team_id']} "
                         f"(slot {best['slot']})."
                     ),
                     "context_beat": beat}
                )
            beat = {
                **beat,
                "taken": False,
                "reason": "establishing_publish_no_change",
            }

        # full_auto: THE write — scene AND active_camera, through
        # DirectorService only. (The demo engine's SWITCH_POV omitted
        # active_camera; that bug is buried here.)
        result = await self._director.system_publish(
            scene="main",
            active_camera=best["slot"],
            updated_by="live_director",
        )
        if not result.get("changed"):
            return self._log_decision(
                {**base_entry, "outcome": "hold",
                 "rule": "system_publish_no_change",
                 "margin_required": margin_required,
                 "margin_actual": margin_actual,
                 "reason": "system_publish reported no change (mode race)."}
            )
        self._switch_timestamps.append(now)
        self._scene_started_at = now
        self._last_known_slot = best["slot"]
        self._current_fight_id = best["fight_id"]
        self._shown_teams.append(best["team_id"])
        if current_team is not None:
            self._shown_teams.append(current_team)
        self.stats["switches"] += 1
        return self._log_decision(
            {**base_entry, "outcome": "switch", "rule": rule,
             "margin_required": margin_required,
             "margin_actual": margin_actual, "reason": reason,
             "context_beat": beat}
        )

    # ── Phase 25 P1-7: context-first beat machinery ──────────────

    def _establishing_view(self) -> Optional[str]:
        """The establishing scene for a context-first beat, or None
        when the rig does not provide one (beat then skips silently —
        no scene is ever invented)."""
        if self._establishing_view_provider is not None:
            try:
                view = self._establishing_view_provider()
            except Exception:  # pragma: no cover - defensive
                return None
            return str(view) if view else None
        scene = self.config.context_beat_scene
        return scene if scene in DIRECTOR_SCENES else None

    def _context_beat_decision(
        self,
        best: Dict[str, Any],
        *,
        phase: str,
        current_scene: Optional[str],
    ) -> Dict[str, Any]:
        """Taken/skipped + why, for one winning challenger.

        Guardrails (blueprint C1, grammar-first):
          * a HOT cut (challenger already has a promoted fight) is
            NEVER delayed by the beat;
          * the final circle takes no beat at all;
          * only anticipation cuts qualify — the challenger arrived
            via PRE_COMBAT (engagement state or a precombat score
            term) with no promoted fight yet;
          * no establishing view in the rig -> skip silently.
        """
        base = {
            "taken": False,
            "scene": None,
            "duration_s": self.config.context_beat_duration_s,
        }
        if not self.config.context_beat_enabled:
            return {**base, "reason": "context_beat_disabled"}
        if phase == "final_circle":
            return {**base, "reason": "final_circle_no_beat"}
        if best.get("has_fight"):
            return {**base, "reason": "hot_fight_cut_never_delayed"}
        is_anticipation = (
            best.get("engagement_state") == EngagementState.PRE_COMBAT.value
            or (best.get("breakdown") or {}).get("precombat", 0.0) > 0
        )
        if not is_anticipation:
            return {**base, "reason": "not_an_anticipation_cut"}
        view = self._establishing_view()
        if view is None:
            return {**base, "reason": "establishing_view_unavailable"}
        if current_scene == view:
            return {**base, "reason": "already_on_establishing_view"}
        return {
            "taken": True,
            "reason": "anticipation_cut_via_precombat",
            "scene": view,
            "duration_s": self.config.context_beat_duration_s,
        }

    async def _service_pending_beat(
        self,
        *,
        trigger: str,
        mode: str,
        phase: str,
        now: float,
        current_team: Optional[str],
        current_slot: Optional[int],
        clone_grabbed: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Advance a pending context-first beat.

        Returns a decision entry when the beat completes / aborts /
        is cancelled; returns None when the beat is still running
        (caller then holds all further decisions) or when the abort
        hands control back to the normal decision flow.
        """
        pending = self._pending_beat
        if pending is None:
            return None

        def _entry(outcome: str, rule: str, reason: str, beat: Dict[str, Any]):
            return self._log_decision(
                {
                    "timestamp": _utcnow_iso(),
                    "trigger": trigger,
                    "mode": mode,
                    "phase": phase,
                    "candidates": [],
                    "incumbent": {
                        "team_id": current_team,
                        "slot": current_slot,
                        "score": None,
                        "breakdown": {},
                    },
                    "challenger": {
                        "team_id": pending["team_id"],
                        "slot": pending["slot"],
                        "score": None,
                        "fight_id": pending["fight_id"],
                    },
                    "outcome": outcome,
                    "rule": rule,
                    "margin_required": None,
                    "margin_actual": None,
                    "reason": reason,
                    "context_beat": beat,
                }
            )

        if mode not in ("full_auto", "clone") or clone_grabbed:
            self._pending_beat = None
            return _entry(
                "hold",
                "context_beat_cancelled_mode_change",
                "Context-first beat cancelled: director left FULL_AUTO "
                "mid-beat; the pending cut is dropped, not forced.",
                {"taken": False, "reason": "mode_changed_mid_beat",
                 "scene": pending["scene"],
                 "duration_s": pending["duration_s"]},
            )

        # A HOT fight (promoted, different team) aborts the beat
        # instantly — hot cuts are never delayed, even mid-beat. The
        # normal evaluation below then makes the hot cut itself.
        hot = any(
            fight_id != pending.get("fight_id")
            and pending["team_id"] not in fight["teams"]
            for fight_id, fight in self._fights.items()
        )
        if hot and now < pending["completes_at"]:
            self._pending_beat = None
            self.stats["context_beats_aborted"] = (
                self.stats.get("context_beats_aborted", 0) + 1
            )
            _entry(
                "hold",
                "context_beat_aborted_hot_fight",
                "Context-first beat aborted: a promoted fight elsewhere "
                "is hot; the beat never delays a hot cut.",
                {"taken": False, "reason": "aborted_by_hot_fight",
                 "scene": pending["scene"],
                 "duration_s": pending["duration_s"]},
            )
            return None  # fall through to the normal decision flow

        if now < pending["completes_at"]:
            return None  # still beating; caller holds

        # Beat elapsed: complete the cut if the target still carries a
        # real signal; otherwise cancel honestly.
        self._pending_beat = None
        team_id = pending["team_id"]
        still_signalled = (
            team_id in self._combat_heat
            or team_id in self._intel_precombat
            or any(team_id in f["teams"] for f in self._fights.values())
            or self.engagement.state_of(team_id)
            == EngagementState.PRE_COMBAT
        )
        beat_done = {
            "taken": True,
            "completed": True,
            "reason": "anticipation_cut_via_precombat",
            "scene": pending["scene"],
            "duration_s": pending["duration_s"],
        }
        if not still_signalled:
            return _entry(
                "hold",
                "context_beat_cancelled_target_gone",
                f"Context-first beat ended but {team_id} no longer "
                "carries any live signal; the cut is cancelled.",
                {**beat_done, "completed": False,
                 "reason": "target_signal_gone"},
            )
        result = await self._director.system_publish(
            scene="main",
            active_camera=pending["slot"],
            updated_by="live_director",
        )
        if not result.get("changed"):
            return _entry(
                "hold",
                "system_publish_no_change",
                "Beat completed but system_publish reported no change "
                "(mode race).",
                beat_done,
            )
        self._switch_timestamps.append(now)
        self._scene_started_at = now
        self._last_known_slot = pending["slot"]
        self._current_fight_id = pending["fight_id"]
        self._shown_teams.append(team_id)
        if current_team is not None:
            self._shown_teams.append(current_team)
        self.stats["switches"] += 1
        return _entry(
            "switch",
            "context_beat_completed",
            f"Context-first beat complete: cut to {team_id} "
            f"(slot {pending['slot']}) after a "
            f"{pending['duration_s']:g}s establishing beat on "
            f"{pending['scene']}.",
            beat_done,
        )

    async def _maybe_replay_handoff(
        self, best: Dict[str, Any], current_fight: Optional[Dict[str, Any]]
    ) -> None:
        """Grammar U9: a second simultaneous fight is NEVER boxed (no PiP).

        When PGM is committed to a different live fight, the challenger
        fight is handed to the replay queue via ``replay.candidate``.

        Emitted contract (see module docstring; consumer is
        ``ReplayQueue.handle_candidate``): fight_id, teams,
        window {started_at, last_activity_at}, intensity, is_clutch,
        reason, source, kind, window_start, window_end, score,
        moment_id, team, opponents, slot_number. Kind honesty: a
        simultaneous fight is NOT automatically a detector kind
        (squad_wipe / multi_kill / ...) — kind is "clutch" only when
        the fight is_clutch, otherwise the director-curated "fight".
        """
        fight_id = best.get("fight_id")
        if not fight_id or current_fight is None:
            return
        if fight_id == current_fight.get("fight_id"):
            return
        if fight_id in self._replay_emitted:
            return
        fight = self._fights.get(fight_id)
        if fight is None:
            return
        self._replay_emitted.add(fight_id)
        self.stats["replay_candidates_emitted"] += 1
        teams = list(fight["teams"])
        try:
            await self._bus.publish(
                event_type=REPLAY_CANDIDATE_TOPIC,
                data={
                    "fight_id": fight_id,
                    "teams": teams,
                    "window": {
                        "started_at": fight["started_at"],
                        "last_activity_at": fight["last_activity_at"],
                    },
                    "intensity": fight["intensity"],
                    "is_clutch": fight["is_clutch"],
                    "reason": (
                        "Simultaneous fight while PGM is committed to "
                        f"{current_fight['fight_id']}; grammar forbids PiP "
                        "during fights — offered to the replay queue."
                    ),
                    "source": "live_director",
                    # ── flat consumer contract (ReplayQueue.handle_candidate) ──
                    "kind": "clutch" if fight["is_clutch"] else "fight",
                    "window_start": fight["started_at"],
                    "window_end": fight["last_activity_at"],
                    "score": int(fight["intensity"]),
                    "moment_id": fight_id,
                    "team": teams[0] if teams else None,
                    "opponents": teams[1:] if len(teams) > 1 else [],
                    "slot_number": best.get("slot"),
                },
                priority=EventPriority.HIGH,
                source=SUBSCRIBER_ID,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "replay_candidate_publish_failed", error=str(exc)[:200]
            )

    async def _update_preview(
        self, best: Optional[Dict[str, Any]], now: float
    ) -> None:
        """PVW: publish the top candidate into DirectorService preview."""
        if best is None:
            preview = None
            key = None
        else:
            preview = {
                "slot": best["slot"],
                "scene": "main",
                "team_id": best["team_id"],
                "fight_id": best["fight_id"],
                "reason": (
                    f"Top candidate: {best['team_id']} "
                    f"(score {best['score']})"
                ),
                "score": best["score"],
                "breakdown": dict(best["breakdown"]),
                "updated_at": _utcnow_iso(),
            }
            key = (best["slot"], best["team_id"], best["fight_id"], best["score"])
        if key == self._preview_key:
            return
        self._preview_key = key
        try:
            await self._director.update_preview(preview)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("preview_update_failed", error=str(exc)[:200])

    # ── decision log / status ────────────────────────────────────

    def _log_decision(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        self._decision_log.append(entry)
        self.stats["decisions"] += 1
        logger.info(
            "live_director_decision",
            outcome=entry.get("outcome"),
            rule=entry.get("rule"),
            challenger=(entry.get("challenger") or {}).get("team_id"),
        )
        # Phase 27 (W1): forward to the director-learning decision
        # journal sink. Default NO-OP (no sink registered -> behaviour
        # byte-identical); notify_decision itself swallows sink errors,
        # and the import is guarded too — a broken journal can never
        # break or delay a decision.
        try:
            from app.services.director_learning.journal import notify_decision

            notify_decision(entry)
        except Exception:  # pragma: no cover - defensive
            pass
        return entry

    def decision_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Recent decision-log entries, oldest first (read-only copy)."""
        entries = list(self._decision_log)
        return entries[-max(1, int(limit)):]

    def status(self) -> Dict[str, Any]:
        snapshot = self._director.snapshot()
        now = self._clock()
        drop_watch = [
            entry
            for entry in self._drop_clash_watch.values()
            if now - entry["at"] <= self.config.drop_clash_ttl_s
        ]
        return {
            "running": self._started,
            "subscriber_id": SUBSCRIBER_ID,
            "event_types": list(LIVE_DIRECTOR_EVENT_TYPES),
            "mode": snapshot.get("mode"),
            "active_camera": snapshot.get("active_camera"),
            "current_fight_id": self._current_fight_id,
            "active_fights": len(self._fights),
            "teams_alive": self._teams_alive,
            "players_alive": self._players_alive,
            "stats": dict(self.stats),
            "last_error": self._last_error,
            # ── Phase 25 P1 surfaces (read-only) ──
            # Per-team engagement states (P1-5): also reachable via
            # GET /director/decision-log's status payload, which is
            # operator-gated like its neighbours.
            "engagement": self.engagement.snapshot(),
            # Active drop-clash watch entries (P1-6).
            "drop_clash_watch": drop_watch,
            # Pending context-first beat (P1-7), or None.
            "pending_context_beat": (
                dict(self._pending_beat) if self._pending_beat else None
            ),
            # ── Phase 27 (W3) surfaces ──
            "clone": {
                "grabbed": bool(snapshot.get("clone_grabbed")),
                "grabbed_by": snapshot.get("clone_grabbed_by"),
                "grabbed_at": snapshot.get("clone_grabbed_at"),
                "grab_inactivity_s": self.config.clone_grab_inactivity_s,
                "learned_active": bool(self._eval_learned),
            },
            "learning_profile": (
                self._knob_provider().learned_snapshot()
                if self._knob_provider() is not None
                else None
            ),
            # Phase 29 (W4): per-feed POV summary (empty without a
            # POV provider — the pre-Phase-29 behaviour).
            "pov_feeds": self.pov_summary(),
            "storyline_config": {
                "max_multiplier": self.config.storyline_max_multiplier,
                "rank1_uplift": self.config.storyline_rank1_uplift,
                "rank2_uplift": self.config.storyline_rank2_uplift,
                "rank_top4_uplift": self.config.storyline_rank_top4_uplift,
                "match_point_uplift": self.config.storyline_match_point_uplift,
                "hot_streak_uplift": self.config.storyline_hot_streak_uplift,
                "hot_streak_finishes": self.config.hot_streak_finishes,
                "hot_streak_window_s": self.config.hot_streak_window_s,
            },
        }
