# backend/app/services/vision_intelligence/service.py
"""Vision -> intelligence loop subscriber (Phase 22).

Phase 21 wired the CV detectors into ``vision/runtime.py``, which
publishes NormalizedEvents to the event bus (``hp_update``,
``utility_event``, ``position_update``, ``zone_event``,
``healing_event``, ``loot_event`` — plus the Phase 14
``alive_count_update`` / ``team_status_update``). NOBODY consumed them:
the fight detector, team trackers, analytics, storyteller and
anti-cheat never saw a single vision observation.

This service is that consumer. It subscribes to the vision event types
on the bus and, for each event:

  1. ATTRIBUTES it to a team through the observer-slot mapping
     (``attribution.py``). Unmapped / slotless events are dropped with
     a logged reason — a team is never guessed.
  2. FEEDS the fight detector (HP velocity via ``process_hp_change``,
     utility, proximity at the PRD 50 m threshold when a calibrated
     minimap scale exists, squad-size delta and down-count signals),
     the per-team trackers (HP, player states, utility/healing event
     records, loot counters) and the zone analytics state.
  3. OFFERS storyline candidates to the narrative auto-flagger
     (``story_flagger.py``) and observations to the vision anti-cheat
     checks (``anti_cheat.py``); raised anti-cheat flags go to the
     flag sink (DB persistence in production) for human review.

Everything is defensive: a malformed event or a failing collaborator
is logged and counted, never propagated — the subscriber must not be
able to break the bus dispatch for other consumers.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

import structlog

from app.core.event_bus import EventBus, EventPriority, EventRecord

from .anti_cheat import VisionAntiCheatAnalyzer
from .attribution import SlotResolver, TeamAttribution, extract_slot_number
from .story_flagger import StoryAutoFlagger
from .zone_state import ZoneAnalyticsState

logger = structlog.get_logger("aegis.vision_intelligence")

#: Bus event types this service consumes. The first six are the
#: Phase 21 vision types; alive_count_update / team_status_update are
#: the Phase 14 vision types the squad-delta + down-count signals are
#: derived from; kill_event / knock_event feed the anti-cheat kill-rate
#: check and milestone candidates.
VISION_EVENT_TYPES = [
    "hp_update",
    "utility_event",
    "position_update",
    "zone_event",
    "healing_event",
    "loot_event",
    "alive_count_update",
    "team_status_update",
    "kill_event",
    "knock_event",
]

SUBSCRIBER_ID = "vision_intelligence"

#: async callable receiving one anti-cheat flag dict for persistence.
FlagSink = Callable[[Dict[str, Any]], Awaitable[None]]

_PLAYER_SLOT_RE = re.compile(r"(\d+)\s*$")


@dataclass
class TeamTrackers:
    """The per-team agents the subscriber feeds (either may be None)."""

    team_tracker: Any = None
    utility_tracker: Any = None


#: (team_id, team_name) -> TeamTrackers
TrackerProvider = Callable[[str, Optional[str]], TeamTrackers]


class LazyTrackerProvider:
    """Builds real TeamTracker/UtilityTracker agents on first use.

    The agents are never ``start()``ed — they are driven by direct
    method calls, the same pattern the referee agents document. Player
    rows are created on demand from screen slots (``player_1`` ->
    ``<team>_player_1``) because the vision layer knows squad slots,
    not DB player ids.
    """

    def __init__(self, bus: EventBus):
        self._bus = bus
        self._cache: Dict[str, TeamTrackers] = {}

    def __call__(self, team_id: str, team_name: Optional[str]) -> TeamTrackers:
        cached = self._cache.get(team_id)
        if cached is not None:
            return cached
        from app.agents.base_agent import AgentConfig
        from app.agents.team_agents.team_tracker import TeamTracker
        from app.agents.team_agents.utility_tracker import UtilityTracker

        name = team_name or team_id
        team_tracker = TeamTracker(
            config=AgentConfig(
                agent_id=f"vision_team_{team_id}", agent_type="team_agent"
            ),
            event_bus=self._bus,
            team_id=team_id,
            team_name=name,
            short_name=name[:4].upper(),
            player_names=[],
        )
        utility_tracker = UtilityTracker(
            config=AgentConfig(
                agent_id=f"vision_utility_{team_id}",
                agent_type="utility_tracker",
            ),
            event_bus=self._bus,
            team_id=team_id,
        )
        trackers = TeamTrackers(
            team_tracker=team_tracker, utility_tracker=utility_tracker
        )
        self._cache[team_id] = trackers
        return trackers


def _env_int(name: str) -> Optional[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if 1 <= value <= 16 else None


def _env_float(name: str) -> Optional[float]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


@dataclass
class SubscriberStats:
    received: int = 0
    attributed: int = 0
    dropped_unmapped: int = 0
    dropped_no_slot: int = 0
    handler_errors: int = 0
    by_type: Dict[str, int] = field(default_factory=dict)
    flags_raised: int = 0


class VisionIntelligenceService:
    """Event-bus subscriber closing the vision -> intelligence loop."""

    def __init__(
        self,
        bus: EventBus,
        slot_resolver: SlotResolver,
        fight_detector: Any = None,
        tracker_provider: Optional[TrackerProvider] = None,
        story_flagger: Optional[StoryAutoFlagger] = None,
        anti_cheat: Optional[VisionAntiCheatAnalyzer] = None,
        flag_sink: Optional[FlagSink] = None,
        default_slot: Optional[int] = None,
        px_per_meter: Optional[float] = None,
        clock: Callable[[], float] = time.time,
    ):
        self._bus = bus
        self._slot_resolver = slot_resolver
        self._clock = clock
        # Vision capture slot config: the single-device rig's slot, used
        # when an event payload carries no slot of its own.
        self.default_slot = (
            default_slot if default_slot is not None
            else _env_int("AEGIS_VISION_OBSERVER_SLOT")
        )
        # Calibrated minimap scale (px per meter) for proximity meters.
        # Unset = uncalibrated: proximity stays in explicit pixel units
        # and is NOT fed to the meter-threshold fight detector.
        self.px_per_meter = (
            px_per_meter if px_per_meter is not None
            else _env_float("AEGIS_VISION_MINIMAP_PX_PER_METER")
        )

        if fight_detector is None:
            from app.agents.base_agent import AgentConfig
            from app.agents.fight_agents.fight_detector import FightDetector

            fight_detector = FightDetector(
                config=AgentConfig(
                    agent_id="vision_fight_detector",
                    agent_type="fight_detector",
                ),
                event_bus=bus,
            )
        self.fight_detector = fight_detector
        self._tracker_provider = tracker_provider or LazyTrackerProvider(bus)
        self.story_flagger = story_flagger
        self.anti_cheat = anti_cheat or VisionAntiCheatAnalyzer(
            px_per_meter=self.px_per_meter, clock=clock
        )
        self._flag_sink = flag_sink
        #: Flags raised while no sink is wired (in-memory review buffer).
        self.unsaved_flags: List[Dict[str, Any]] = []

        self.zone_state = ZoneAnalyticsState()
        self.stats = SubscriberStats()
        self._started = False

        # Per-team live state for delta derivation.
        self._team_alive: Dict[str, int] = {}
        self._team_downs: Dict[str, int] = {}
        self._known_fight_ids: set = set()
        # Per-team counters the trackers don't model (loot episodes,
        # healing events — amounts are never invented, counts are real).
        self._team_counters: Dict[str, Dict[str, int]] = {}
        self._last_error = ""

    # ── lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        """Subscribe to the vision event types on the bus. Idempotent."""
        if self._started:
            return
        self._bus.subscribe(
            subscriber_id=SUBSCRIBER_ID,
            callback=self.handle_record,
            event_types=list(VISION_EVENT_TYPES),
        )
        self._started = True
        logger.info(
            "vision_intelligence_subscribed",
            event_types=VISION_EVENT_TYPES,
            default_slot=self.default_slot,
            px_per_meter=self.px_per_meter,
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

    # ── dispatch ─────────────────────────────────────────────────

    async def handle_record(self, record: EventRecord) -> None:
        """Bus callback: route one event record. Never raises."""
        event = getattr(record, "event", None)
        event_type = getattr(event, "event_type", "") if event else ""
        data = getattr(event, "data", None) if event else None
        if not isinstance(data, dict):
            return
        match_id = getattr(event, "match_id", None) or data.get("match_id")
        ts = None
        raw_ts = data.get("timestamp")
        if isinstance(raw_ts, (int, float)) and raw_ts > 0:
            ts = float(raw_ts)

        self.stats.received += 1
        self.stats.by_type[event_type] = self.stats.by_type.get(event_type, 0) + 1
        try:
            handler = self._handlers().get(event_type)
            if handler is not None:
                await handler(data, match_id, ts)
            # Flush any fight.detected events the detector queued and
            # surface newly promoted fights to the story flagger.
            await self._after_signals(match_id)
        except Exception as exc:
            self.stats.handler_errors += 1
            self._last_error = str(exc)[:300]
            logger.error(
                "vision_intelligence_handler_error",
                event_type=event_type,
                error=self._last_error,
            )

    def _handlers(self) -> Dict[str, Any]:
        return {
            "hp_update": self._on_hp_update,
            "utility_event": self._on_utility,
            "position_update": self._on_position_update,
            "zone_event": self._on_zone_update,
            "healing_event": self._on_healing,
            "loot_event": self._on_loot,
            "alive_count_update": self._on_alive_count,
            "team_status_update": self._on_team_status,
            "kill_event": self._on_kill,
            "knock_event": self._on_kill,
        }

    # ── attribution ──────────────────────────────────────────────

    async def _attribute(
        self, data: Dict[str, Any], event_type: str
    ) -> Optional[TeamAttribution]:
        """Resolve the event's team via the observer-slot mapping.

        Returns None (and counts/logs the drop) when the event carries
        no slot or the slot is unmapped. NEVER guesses a team.
        """
        slot = extract_slot_number(data, self.default_slot)
        if slot is None:
            self.stats.dropped_no_slot += 1
            logger.info(
                "vision_event_dropped_no_slot",
                event_type=event_type,
                reason="event carries no observer_slot and no default "
                "vision slot is configured (AEGIS_VISION_OBSERVER_SLOT)",
            )
            return None
        attribution = await self._slot_resolver.resolve(slot)
        if attribution is None:
            self.stats.dropped_unmapped += 1
            logger.info(
                "vision_event_dropped_unmapped_slot",
                event_type=event_type,
                slot_number=slot,
                reason="observer slot has no team assigned in "
                "observer_slots; refusing to guess attribution",
            )
            return None
        self.stats.attributed += 1
        return attribution

    def _trackers(self, attribution: TeamAttribution) -> TeamTrackers:
        return self._tracker_provider(attribution.team_id, attribution.team_name)

    def _counters(self, team_id: str) -> Dict[str, int]:
        return self._team_counters.setdefault(
            team_id, {"healing_events": 0, "loot_events": 0, "utility_events": 0}
        )

    @staticmethod
    def _player_key(team_id: str, raw_player_id: Any) -> str:
        """Map a vision screen slot ('player_1') to a tracker player id."""
        raw = str(raw_player_id or "")
        match = _PLAYER_SLOT_RE.search(raw)
        if match:
            return f"{team_id}_player_{int(match.group(1))}"
        return raw or f"{team_id}_player_1"

    def _ensure_player(self, trackers: TeamTrackers, player_key: str, label: Any) -> None:
        tracker = trackers.team_tracker
        if tracker is None:
            return
        if tracker.get_player(player_key) is None:
            tracker.add_player(player_key, str(label or player_key))

    # ── hp_update ────────────────────────────────────────────────

    async def _on_hp_update(
        self, data: Dict[str, Any], match_id: Optional[str], ts: Optional[float]
    ) -> None:
        attribution = await self._attribute(data, "hp_update")
        hp_pct = data.get("hp_pct")
        prev_pct = data.get("previous_hp_pct")
        if attribution is None or hp_pct is None or prev_pct is None:
            return
        team_id = attribution.team_id
        player_key = self._player_key(team_id, data.get("player_id"))
        trackers = self._trackers(attribution)
        self._ensure_player(trackers, player_key, data.get("slot_label"))

        # Fight detector: REAL HP with previous sample -> velocity math.
        self.fight_detector.process_hp_change(
            player_id=player_key,
            team_id=team_id,
            old_hp=int(round(float(prev_pct))),
            new_hp=int(round(float(hp_pct))),
            timestamp=ts,
        )
        # Team tracker: honest HP (its damage_taken derives from drops).
        if trackers.team_tracker is not None:
            trackers.team_tracker.update_player_hp(
                player_key, int(round(float(hp_pct)))
            )
            if data.get("is_knocked"):
                from app.agents.team_agents.team_tracker import PlayerState

                trackers.team_tracker.update_player_state(
                    player_key, PlayerState.KNOCKED
                )
        # Down-count signal: a fresh knock transition on this slot.
        if data.get("is_knocked") and not data.get("was_knocked"):
            self.fight_detector.process_down_count(team_id, 1, timestamp=ts)
        # Anti-cheat: HP returning from exactly 0.
        await self._raise_flags(
            self.anti_cheat.on_hp_update(
                player_key,
                hp_pct,
                ts=ts,
                match_id=match_id,
                team_id=team_id,
                team_name=attribution.team_name,
            )
        )

    # ── utility ──────────────────────────────────────────────────

    async def _on_utility(
        self, data: Dict[str, Any], match_id: Optional[str], ts: Optional[float]
    ) -> None:
        attribution = await self._attribute(data, "utility_event")
        if attribution is None:
            return
        team_id = attribution.team_id
        utility_type = str(data.get("utility_type") or "")
        if not utility_type:
            return
        position = data.get("position") or (0.0, 0.0)
        try:
            pos_x, pos_y = float(position[0]), float(position[1])
        except (TypeError, ValueError, IndexError):
            pos_x, pos_y = 0.0, 0.0

        # The detector saw utility on this team's screen; per the
        # attribution contract the event belongs to that slot's team.
        # hit_enemy is unknown (the detector cannot tell) -> False, and
        # the fight detector weights it as the weaker signal it is.
        self.fight_detector.process_utility(
            player_id=f"{team_id}:observer_screen",
            team_id=team_id,
            utility_type=utility_type,
            target_x=pos_x,
            target_y=pos_y,
            source_x=pos_x,
            source_y=pos_y,
            hit_enemy=False,
        )
        self._counters(team_id)["utility_events"] += 1

        trackers = self._trackers(attribution)
        if trackers.utility_tracker is not None:
            from app.agents.team_agents.utility_tracker import UtilityType

            try:
                utype = UtilityType(utility_type)
            except ValueError:
                utype = None
            if utype is not None:
                trackers.utility_tracker.record_utility_use(
                    event_id=f"vision-{team_id}-{int(self._clock() * 1000)}",
                    player_id="observer_screen",
                    player_name="Observer feed (thrower unidentified)",
                    utility_type=utype,
                    location_x=pos_x,
                    location_y=pos_y,
                )

    # ── position_update ──────────────────────────────────────────

    async def _on_position_update(
        self, data: Dict[str, Any], match_id: Optional[str], ts: Optional[float]
    ) -> None:
        attribution = await self._attribute(data, "position_update")
        if attribution is None:
            return
        team_id = attribution.team_id
        positions = data.get("player_positions") or {}

        snapshot = self.zone_state.update_team_positions(team_id, positions, ts=ts)

        # Anti-cheat: per-marker jump check (marker identity is scoped
        # to the observing slot so two devices never share a track).
        for key, pos in positions.items():
            try:
                x, y = float(pos[0]), float(pos[1])
            except (TypeError, ValueError, IndexError):
                continue
            await self._raise_flags(
                self.anti_cheat.on_position(
                    f"slot{attribution.slot_number}:{key}",
                    x,
                    y,
                    ts=ts,
                    match_id=match_id,
                    team_id=team_id,
                    team_name=attribution.team_name,
                )
            )

        # Proximity: nearest enemy marker vs the observed team's
        # centroid. Meters ONLY with a calibrated scale — otherwise the
        # pixel distance stays in the zone analytics (units explicit)
        # and the meter-threshold detector is not fed fake meters.
        nearest_px = snapshot.get("nearest_enemy_px")
        centroid = snapshot.get("centroid")
        if nearest_px is not None and centroid is not None:
            if self.px_per_meter:
                distance_m = nearest_px / self.px_per_meter
                self.fight_detector.process_proximity(
                    team_id=team_id,
                    other_team_id=None,  # enemy marker's team is unknown
                    distance=distance_m,
                    location_x=float(centroid[0]),
                    location_y=float(centroid[1]),
                )
            else:
                logger.debug(
                    "proximity_kept_in_pixels",
                    team_id=team_id,
                    nearest_enemy_px=round(float(nearest_px), 1),
                    reason="minimap scale uncalibrated "
                    "(AEGIS_VISION_MINIMAP_PX_PER_METER unset); not "
                    "feeding pixel distances as meters",
                )

        if snapshot.get("zone_swing") and self.story_flagger is not None:
            await self.story_flagger.on_zone_swing(
                match_id, snapshot.get("favoured_team_id")
            )

    # ── zone_update ──────────────────────────────────────────────

    async def _on_zone_update(
        self, data: Dict[str, Any], match_id: Optional[str], ts: Optional[float]
    ) -> None:
        # Zone state is match-global: it needs no team attribution and
        # is the one event type never dropped for a missing slot.
        changed = self.zone_state.update_zone(data, ts=ts)
        if changed and self.story_flagger is not None:
            await self.story_flagger.on_zone_swing(
                match_id, self.zone_state.favoured_team_id
            )

    # ── healing / loot ───────────────────────────────────────────

    async def _on_healing(
        self, data: Dict[str, Any], match_id: Optional[str], ts: Optional[float]
    ) -> None:
        attribution = await self._attribute(data, "healing_event")
        if attribution is None:
            return
        team_id = attribution.team_id
        self._counters(team_id)["healing_events"] += 1
        # Fight-signal context: healing after damage is a (weak) fight
        # context signal in the detector's own vocabulary.
        trackers = self._trackers(attribution)
        if trackers.utility_tracker is not None and data.get("completed"):
            from app.agents.team_agents.utility_tracker import UtilityType

            heal_map = {
                "first_aid": UtilityType.FIRST_AID,
                "medkit": UtilityType.MEDKIT,
                "bandage": UtilityType.BANDAGE,
                "boost": UtilityType.BOOST,
            }
            utype = heal_map.get(str(data.get("heal_type") or ""))
            if utype is not None:
                # Amount healed is NOT observed by the detector, so the
                # recorded amount is 0: the event (a completed heal) is
                # real, the HP number would be invented.
                trackers.utility_tracker.record_healing(
                    player_id="observer_screen",
                    player_name="Observer feed (player unidentified)",
                    heal_type=utype,
                    amount=0,
                    location_x=0.0,
                    location_y=0.0,
                )

    async def _on_loot(
        self, data: Dict[str, Any], match_id: Optional[str], ts: Optional[float]
    ) -> None:
        attribution = await self._attribute(data, "loot_event")
        if attribution is None:
            return
        # Loot episodes are counted per team (the loot detector confirms
        # a persistent crate on this team's screen). No per-player claim:
        # the detector cannot say who looted it.
        self._counters(attribution.team_id)["loot_events"] += 1

    # ── alive_count / team_status ────────────────────────────────

    async def _on_alive_count(
        self, data: Dict[str, Any], match_id: Optional[str], ts: Optional[float]
    ) -> None:
        players_alive = data.get("players_alive")
        if players_alive is None:
            return
        await self._raise_flags(
            self.anti_cheat.on_global_alive_count(
                players_alive, ts=ts, match_id=match_id
            )
        )
        if self.story_flagger is not None:
            await self.story_flagger.on_alive_count(match_id, players_alive)

    async def _on_team_status(
        self, data: Dict[str, Any], match_id: Optional[str], ts: Optional[float]
    ) -> None:
        attribution = await self._attribute(data, "team_status_update")
        if attribution is None:
            return
        team_id = attribution.team_id
        slots = data.get("slots") or []
        alive_count = data.get("alive_count")
        if alive_count is None and slots:
            alive_count = sum(1 for s in slots if s in ("alive", "knocked"))

        trackers = self._trackers(attribution)
        if trackers.team_tracker is not None and slots:
            from app.agents.team_agents.team_tracker import PlayerState

            state_map = {
                "alive": PlayerState.ALIVE,
                "knocked": PlayerState.KNOCKED,
                "dead": PlayerState.DEAD,
            }
            for idx, status in enumerate(slots, start=1):
                state = state_map.get(str(status))
                if state is None:
                    continue  # "unknown" is never guessed into a state
                player_key = f"{team_id}_player_{idx}"
                self._ensure_player(trackers, player_key, None)
                trackers.team_tracker.update_player_state(player_key, state)

        if alive_count is not None:
            alive_count = int(alive_count)
            # Anti-cheat: a team's alive count cannot rise.
            await self._raise_flags(
                self.anti_cheat.on_team_alive_count(
                    team_id,
                    attribution.team_name,
                    alive_count,
                    ts=ts,
                    match_id=match_id,
                )
            )
            # Squad-size-delta fight signal (M4).
            previous = self._team_alive.get(team_id)
            self._team_alive[team_id] = alive_count
            if previous is not None and alive_count != previous:
                self.fight_detector.process_squad_size_delta(
                    team_id, previous, alive_count, timestamp=ts
                )
            # Down-count signal: knocked slots now vs before.
            downs_now = sum(1 for s in slots if s == "knocked")
            downs_prev = self._team_downs.get(team_id, 0)
            self._team_downs[team_id] = downs_now
            if downs_now > downs_prev:
                self.fight_detector.process_down_count(
                    team_id, downs_now - downs_prev, timestamp=ts
                )

    # ── kill / knock (anti-cheat + milestone candidates) ─────────

    async def _on_kill(
        self, data: Dict[str, Any], match_id: Optional[str], ts: Optional[float]
    ) -> None:
        killer = data.get("killer") or data.get("killer_id") or ""
        killer_team = data.get("killerTeam") or data.get("killer_team_id")
        if killer and killer != "Unknown":
            await self._raise_flags(
                self.anti_cheat.on_kill(
                    str(killer),
                    ts=ts,
                    match_id=match_id,
                    killer_name=str(killer),
                    team_name=(
                        str(killer_team)
                        if killer_team and killer_team != "Unknown"
                        else None
                    ),
                )
            )
        if self.story_flagger is not None:
            player_id = data.get("killer_player_id") or data.get("killer_id")
            await self.story_flagger.on_kill(
                match_id, str(player_id) if player_id else None
            )

    # ── shared post-signal work ──────────────────────────────────

    async def _after_signals(self, match_id: Optional[str]) -> None:
        """Flush detector output + surface new fights to the flagger."""
        try:
            await self.fight_detector.tick()
        except Exception:  # pragma: no cover - defensive
            pass
        if self.story_flagger is None:
            return
        try:
            active = self.fight_detector.get_active_fights()
        except Exception:  # pragma: no cover - defensive
            return
        for fight in active:
            fight_id = fight.get("fight_id")
            if not fight_id or fight_id in self._known_fight_ids:
                continue
            self._known_fight_ids.add(fight_id)
            await self.story_flagger.on_fight_promoted(
                match_id,
                [str(t) for t in fight.get("teams", [])],
                team_alive=dict(self._team_alive),
            )

    async def _raise_flags(self, flag: Optional[Dict[str, Any]]) -> None:
        if flag is None:
            return
        self.stats.flags_raised += 1
        if self._flag_sink is not None:
            try:
                await self._flag_sink(flag)
            except Exception as exc:
                logger.error(
                    "anti_cheat_flag_persist_failed", error=str(exc)[:200]
                )
                self.unsaved_flags.append(flag)
        else:
            self.unsaved_flags.append(flag)
        try:
            await self._bus.publish(
                event_type="anti_cheat.flagged",
                data=flag,
                priority=EventPriority.HIGH,
                source=SUBSCRIBER_ID,
                match_id=flag.get("match_id"),
            )
        except Exception:  # pragma: no cover - defensive
            pass

    # ── status ───────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        fights = []
        try:
            fights = self.fight_detector.get_active_fights()
        except Exception:  # pragma: no cover - defensive
            pass
        return {
            "running": self._started,
            "subscriber_id": SUBSCRIBER_ID,
            "event_types": list(VISION_EVENT_TYPES),
            "default_slot": self.default_slot,
            "px_per_meter": self.px_per_meter,
            "proximity_units": (
                "meters (calibrated scale)" if self.px_per_meter
                else "minimap_pixels (uncalibrated — proximity not fed "
                "to the fight detector)"
            ),
            "stats": {
                "received": self.stats.received,
                "attributed": self.stats.attributed,
                "dropped_unmapped_slot": self.stats.dropped_unmapped,
                "dropped_no_slot": self.stats.dropped_no_slot,
                "handler_errors": self.stats.handler_errors,
                "by_type": dict(self.stats.by_type),
                "flags_raised": self.stats.flags_raised,
                "last_error": self._last_error,
            },
            "active_fights": fights,
            "team_alive": dict(self._team_alive),
            "team_counters": self._team_counters,
            "zone": self.zone_state.snapshot(),
            "story_flagger": (
                self.story_flagger.snapshot() if self.story_flagger else None
            ),
            "anti_cheat_flags_buffered": len(self.unsaved_flags),
        }


# ── production wiring ────────────────────────────────────────────

_service: Optional[VisionIntelligenceService] = None


def get_vision_intelligence_service() -> Optional[VisionIntelligenceService]:
    """The process-wide service built by build_vision_intelligence_service."""
    return _service


def build_vision_intelligence_service(
    bus: EventBus,
) -> VisionIntelligenceService:
    """Build the production service (DB slot resolver, DB flag sink,
    DB-backed story flagger) and register it as the process singleton.

    Called from app/main.py's lifespan after the event bus connects.
    Every collaborator degrades honestly: no DB -> slot resolution
    fails closed (events dropped as unmapped); story generation uses
    the real StoryService over short-lived sessions.
    """
    global _service
    from app.db.session import AsyncSessionLocal

    from .attribution import DbSlotResolver

    async def _flag_sink(flag: Dict[str, Any]) -> None:
        from app.api.v1.anti_cheat.service import AntiCheatFlagService

        async with AsyncSessionLocal() as session:
            await AntiCheatFlagService(session).record_flag(flag)

    async def _story_generator(match_id, types, context):
        from app.api.v1.stories.service import StoryService

        async with AsyncSessionLocal() as session:
            stories = await StoryService(session).generate(
                match_id=match_id,
                types=types,
                context=context,
                tournament_id=context.get("tournament_id"),
            )
        return [
            {
                "id": s.id,
                "type": s.story_type,
                "title": s.title,
                "body": s.body,
                "data": s.data or {},
            }
            for s in stories
        ]

    async def _story_trigger(story_id: str, username: str):
        from app.api.v1.stories.service import StoryService

        async with AsyncSessionLocal() as session:
            return await StoryService(session).trigger(story_id, username=username)

    def _director_mode() -> str:
        from app.services.director_service import director_service

        return str(director_service.snapshot().get("mode") or "manual")

    async def _publish(event_type: str, data: Dict[str, Any]) -> None:
        if getattr(bus, "_running", False):
            await bus.publish(
                event_type=event_type,
                data=data,
                source="vision_intelligence",
                match_id=data.get("match_id"),
            )

    flagger = StoryAutoFlagger(
        generator=_story_generator,
        trigger=_story_trigger,
        mode_provider=_director_mode,
        publish=_publish,
    )
    _service = VisionIntelligenceService(
        bus=bus,
        slot_resolver=DbSlotResolver(AsyncSessionLocal),
        story_flagger=flagger,
        flag_sink=_flag_sink,
    )
    return _service
