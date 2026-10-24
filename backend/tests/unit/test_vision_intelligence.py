# backend/tests/unit/test_vision_intelligence.py
"""Phase 22 — vision -> intelligence loop tests.

Covers:
  * Subscriber attribution: mapped slot feeds fight detector + trackers;
    unmapped slot and slotless events are dropped (never guessed).
  * HP velocity math: sliding-window HP/sec (burst vs sustained vs drip)
    replacing the old single-threshold check.
  * Proximity threshold tightened to the PRD 50 m; pixel distances are
    only converted with a calibrated scale.
  * Squad-size-delta + down-count fight signals (M4).
  * Story auto-flagger: conditions, full_auto trigger vs semi_auto /
    manual suggestion, cooldowns, missing data -> no flag.
  * Anti-cheat vision subset: position jumps, kill-rate anomaly,
    alive-count inconsistencies; telemetry checks disabled with reasons.
  * Review API auth: 401 unauthenticated, 403 wrong role (incl.
    operator), admin/director list + confirm/dismiss, 409 re-review.

No network, no Redis, no Postgres — in-memory fakes + sqlite.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from sqlalchemy import JSON
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.base_agent import AgentConfig
from app.agents.fight_agents.fight_detector import (
    FightDetector,
    FightSignal,
    HpVelocityTracker,
)
from app.agents.referee_agents.cheating_detector import CheatType
from app.core.event_bus import BaseEvent, EventRecord
from app.services.vision_intelligence.anti_cheat import (
    DISABLED_CHECKS,
    LIVE_CHECKS,
    VisionAntiCheatAnalyzer,
    checks_registry,
)
from app.services.vision_intelligence.attribution import (
    StaticSlotResolver,
    TeamAttribution,
    extract_slot_number,
)
from app.services.vision_intelligence.service import (
    TeamTrackers,
    VisionIntelligenceService,
)
from app.services.vision_intelligence.story_flagger import StoryAutoFlagger

pytestmark = pytest.mark.unit


# ────────────────────────────────────────────────────────────────
# Fakes / helpers
# ────────────────────────────────────────────────────────────────


def _mock_bus():
    bus = AsyncMock()
    bus.publish = AsyncMock(return_value="evt-id")
    bus.subscribe = Mock(return_value="sub-id")
    bus.unsubscribe = Mock(return_value=True)
    bus._running = True
    return bus


def _detector(bus=None) -> FightDetector:
    return FightDetector(
        config=AgentConfig(agent_id="fight_detector", agent_type="fight_detector"),
        event_bus=bus or _mock_bus(),
    )


def _record(event_type: str, data: Dict[str, Any], match_id: str = "m-1") -> EventRecord:
    return EventRecord(
        event=BaseEvent(
            event_type=event_type, data=data, match_id=match_id, source="vision"
        )
    )


class FakeTeamTracker:
    def __init__(self):
        self.hp_calls: List = []
        self.state_calls: List = []
        self.players: Dict[str, Any] = {}

    def get_player(self, player_id):
        return self.players.get(player_id)

    def add_player(self, player_id, player_name):
        self.players[player_id] = {"player_id": player_id, "name": player_name}
        return self.players[player_id]

    def update_player_hp(self, player_id, hp, armor=-1):
        self.hp_calls.append((player_id, hp))
        return True

    def update_player_state(self, player_id, new_state):
        self.state_calls.append((player_id, new_state))
        return True


class FakeUtilityTracker:
    def __init__(self):
        self.utility_calls: List = []
        self.healing_calls: List = []

    def record_utility_use(self, **kwargs):
        self.utility_calls.append(kwargs)

    def record_healing(self, **kwargs):
        self.healing_calls.append(kwargs)


def _service(bus=None, resolver=None, **kwargs) -> VisionIntelligenceService:
    bus = bus or _mock_bus()
    resolver = resolver or StaticSlotResolver(
        {3: TeamAttribution(slot_number=3, team_id="team-a", team_name="Alpha")}
    )
    kwargs.setdefault("fight_detector", _detector(bus))
    return VisionIntelligenceService(bus=bus, slot_resolver=resolver, **kwargs)


# ────────────────────────────────────────────────────────────────
# Attribution
# ────────────────────────────────────────────────────────────────


def test_extract_slot_number_priority_and_validation():
    assert extract_slot_number({"observer_slot": 4}) == 4
    assert extract_slot_number({"slot_number": "7"}) == 7
    assert extract_slot_number({"observer_slot": 99}) is None
    assert extract_slot_number({"observer_slot": "junk"}) is None
    assert extract_slot_number({}, default_slot=2) == 2
    assert extract_slot_number({}) is None


async def test_hp_update_mapped_slot_feeds_detector_and_tracker():
    team_tracker = FakeTeamTracker()
    svc = _service(
        tracker_provider=lambda tid, name: TeamTrackers(team_tracker=team_tracker)
    )
    await svc.handle_record(
        _record(
            "hp_update",
            {
                "observer_slot": 3,
                "player_id": "player_1",
                "slot_label": "Alpha P1",
                "hp_pct": 55,
                "previous_hp_pct": 100,
                "is_knocked": False,
                "timestamp": time.time(),
            },
        )
    )
    assert svc.stats.attributed == 1
    assert svc.stats.dropped_unmapped == 0
    # Tracker got the attributed team's player with the real HP value.
    assert team_tracker.hp_calls == [("team-a_player_1", 55)]
    # Fight detector received an HP_DROP signal from the velocity math
    # (45 HP lost in the window >= the 20 HP minimum).
    signals = list(svc.fight_detector.detection_state.signal_buffer)
    assert any(s.signal_type == FightSignal.HP_DROP for s in signals)
    hp_signal = [s for s in signals if s.signal_type == FightSignal.HP_DROP][0]
    assert hp_signal.source_team_id == "team-a"
    assert hp_signal.damage == 45


async def test_hp_update_unmapped_slot_is_dropped_never_guessed():
    team_tracker = FakeTeamTracker()
    svc = _service(
        tracker_provider=lambda tid, name: TeamTrackers(team_tracker=team_tracker)
    )
    await svc.handle_record(
        _record(
            "hp_update",
            {
                "observer_slot": 9,  # not in the resolver mapping
                "player_id": "player_1",
                "hp_pct": 10,
                "previous_hp_pct": 100,
                "timestamp": time.time(),
            },
        )
    )
    assert svc.stats.dropped_unmapped == 1
    assert svc.stats.attributed == 0
    assert team_tracker.hp_calls == []
    assert len(svc.fight_detector.detection_state.signal_buffer) == 0


async def test_event_without_slot_and_no_default_is_dropped():
    svc = _service()
    await svc.handle_record(
        _record(
            "utility_event",
            {"utility_type": "smoke", "position": [10, 10], "timestamp": time.time()},
        )
    )
    assert svc.stats.dropped_no_slot == 1
    assert svc.stats.attributed == 0


async def test_default_slot_config_attributes_slotless_events():
    team_tracker = FakeTeamTracker()
    svc = _service(
        default_slot=3,
        tracker_provider=lambda tid, name: TeamTrackers(team_tracker=team_tracker),
    )
    await svc.handle_record(
        _record(
            "hp_update",
            {
                "player_id": "player_2",
                "hp_pct": 90,
                "previous_hp_pct": 100,
                "timestamp": time.time(),
            },
        )
    )
    assert svc.stats.attributed == 1
    assert team_tracker.hp_calls == [("team-a_player_2", 90)]


async def test_zone_update_is_global_and_needs_no_slot():
    svc = _service()
    await svc.handle_record(
        _record(
            "zone_event",
            {
                "phase_source": "minimap_geometry",
                "zone_phase": 4,
                "safe_center": [120, 80],
                "safe_radius": 45,
                "timestamp": time.time(),
            },
        )
    )
    assert svc.zone_state.phase == 4
    assert svc.zone_state.safe_center == (120.0, 80.0)
    assert svc.stats.dropped_no_slot == 0


# ────────────────────────────────────────────────────────────────
# HP velocity math (M4)
# ────────────────────────────────────────────────────────────────


def test_hp_velocity_burst_vs_sustained_vs_drip():
    tracker = HpVelocityTracker(window_seconds=2.0)
    # Burst: 40 HP in one instant hit -> high velocity.
    tracker.record("p", 100, timestamp=0.0)
    burst = tracker.record("p", 60, timestamp=0.0)
    assert burst.drop_in_window == 40
    assert burst.velocity_per_sec == pytest.approx(40 / 0.25)

    # Sustained: 60 HP over the full 2 s window -> 30 HP/s.
    tracker2 = HpVelocityTracker(window_seconds=2.0)
    tracker2.record("p", 100, timestamp=0.0)
    tracker2.record("p", 80, timestamp=1.0)
    sustained = tracker2.record("p", 40, timestamp=2.0)
    assert sustained.drop_in_window == 60
    assert sustained.velocity_per_sec == pytest.approx(30.0)

    # Drip: small losses — drop inside the window stays small.
    tracker3 = HpVelocityTracker(window_seconds=2.0)
    tracker3.record("p", 100, timestamp=0.0)
    tracker3.record("p", 97, timestamp=1.0)
    drip = tracker3.record("p", 95, timestamp=2.0)
    assert drip.drop_in_window == 5
    assert drip.velocity_per_sec == pytest.approx(2.5)


def test_hp_velocity_window_slides():
    tracker = HpVelocityTracker(window_seconds=2.0)
    tracker.record("p", 100, timestamp=0.0)
    tracker.record("p", 50, timestamp=0.5)
    # 3 s later the old peak has left the window; HP recovered since.
    sample = tracker.record("p", 90, timestamp=3.5)
    assert sample.drop_in_window == 0


def test_process_hp_change_uses_window_not_single_event():
    detector = _detector()
    # Two sub-threshold drops (15 each) inside the window sum to a
    # fight signal — the OLD single-event check would have missed both.
    base = time.time()
    detector.process_hp_change("p1", "team-a", 100, 85, timestamp=base)
    assert len(detector.detection_state.signal_buffer) == 0
    detector.process_hp_change("p1", "team-a", 85, 70, timestamp=base + 0.5)
    signals = list(detector.detection_state.signal_buffer)
    assert len(signals) == 1
    assert signals[0].damage == 30  # 100 -> 70 inside the window

    # A single small drop alone never fires.
    detector2 = _detector()
    detector2.process_hp_change("p1", "team-a", 100, 90, timestamp=time.time())
    assert len(detector2.detection_state.signal_buffer) == 0


# ────────────────────────────────────────────────────────────────
# Proximity threshold (PRD 50 m)
# ────────────────────────────────────────────────────────────────


def test_proximity_threshold_is_50m():
    detector = _detector()
    assert detector.thresholds["proximity_max_distance"] == 50
    detector.process_proximity("team-a", "team-b", distance=80.0)
    assert len(detector.detection_state.signal_buffer) == 0
    detector.process_proximity("team-a", "team-b", distance=50.0)
    assert len(detector.detection_state.signal_buffer) == 1
    detector.process_proximity("team-a", "team-b", distance=30.0)
    assert len(detector.detection_state.signal_buffer) == 2


async def test_subscriber_proximity_converts_pixels_only_with_scale():
    positions = {
        "teammate_0": [0, 0],
        "teammate_1": [10, 0],
        "enemy_0": [65, 0],  # centroid (5,0) -> 60 px away
    }
    # Calibrated: 2 px/m -> 30 m < 50 m -> proximity signal fires.
    svc = _service(px_per_meter=2.0)
    await svc.handle_record(
        _record(
            "position_update",
            {"observer_slot": 3, "player_positions": positions, "timestamp": time.time()},
        )
    )
    prox = [
        s
        for s in svc.fight_detector.detection_state.signal_buffer
        if s.signal_type == FightSignal.PROXIMITY
    ]
    assert len(prox) == 1
    assert prox[0].source_team_id == "team-a"
    assert prox[0].target_team_id is None  # enemy marker team is unknown

    # Same pixels, scale 1 px/m -> 60 m > 50 m -> no signal.
    svc2 = _service(px_per_meter=1.0)
    await svc2.handle_record(
        _record(
            "position_update",
            {"observer_slot": 3, "player_positions": positions, "timestamp": time.time()},
        )
    )
    assert not [
        s
        for s in svc2.fight_detector.detection_state.signal_buffer
        if s.signal_type == FightSignal.PROXIMITY
    ]

    # Uncalibrated: pixels are NEVER passed off as meters; the honest
    # pixel distance stays in the zone analytics instead.
    svc3 = _service()
    await svc3.handle_record(
        _record(
            "position_update",
            {"observer_slot": 3, "player_positions": positions, "timestamp": time.time()},
        )
    )
    assert not [
        s
        for s in svc3.fight_detector.detection_state.signal_buffer
        if s.signal_type == FightSignal.PROXIMITY
    ]
    assert svc3.zone_state.teams["team-a"]["nearest_enemy_px"] == pytest.approx(60.0)


# ────────────────────────────────────────────────────────────────
# Squad-size delta + down-count signals (M4)
# ────────────────────────────────────────────────────────────────


async def test_team_status_drives_squad_delta_and_down_count():
    svc = _service(
        tracker_provider=lambda tid, name: TeamTrackers(team_tracker=FakeTeamTracker())
    )
    await svc.handle_record(
        _record(
            "team_status_update",
            {
                "observer_slot": 3,
                "slots": ["alive", "alive", "alive", "alive"],
                "alive_count": 4,
                "timestamp": time.time(),
            },
        )
    )
    # Two knocked, one dead: alive 4 -> 3 (delta), downs 0 -> 2.
    await svc.handle_record(
        _record(
            "team_status_update",
            {
                "observer_slot": 3,
                "slots": ["knocked", "knocked", "dead", "alive"],
                "alive_count": 3,
                "timestamp": time.time() + 5,
            },
        )
    )
    types = [s.signal_type for s in svc.fight_detector.detection_state.signal_buffer]
    assert FightSignal.ALIVE_DELTA in types
    assert FightSignal.KNOCK in types
    delta = [
        s
        for s in svc.fight_detector.detection_state.signal_buffer
        if s.signal_type == FightSignal.ALIVE_DELTA
    ][0]
    assert delta.value == 1  # exactly one player lost


def test_squad_size_delta_ignores_increases():
    detector = _detector()
    # An increase is not fight evidence (anti-cheat reviews those).
    assert detector.process_squad_size_delta("team-a", 2, 4) is None
    assert len(detector.detection_state.signal_buffer) == 0
    detector.process_squad_size_delta("team-a", 4, 2)
    signals = list(detector.detection_state.signal_buffer)
    assert signals and signals[0].signal_type == FightSignal.ALIVE_DELTA
    assert signals[0].value == 2


async def test_healing_and_loot_counters_are_per_team():
    utility_tracker = FakeUtilityTracker()
    svc = _service(
        tracker_provider=lambda tid, name: TeamTrackers(
            team_tracker=FakeTeamTracker(), utility_tracker=utility_tracker
        )
    )
    await svc.handle_record(
        _record(
            "healing_event",
            {"observer_slot": 3, "heal_type": "first_aid", "completed": True,
             "timestamp": time.time()},
        )
    )
    await svc.handle_record(
        _record(
            "loot_event",
            {"observer_slot": 3, "loot_type": "death_crate", "timestamp": time.time()},
        )
    )
    counters = svc.snapshot()["team_counters"]["team-a"]
    assert counters["healing_events"] == 1
    assert counters["loot_events"] == 1
    # Completed heal recorded with amount 0 — never an invented number.
    assert utility_tracker.healing_calls[0]["amount"] == 0


# ────────────────────────────────────────────────────────────────
# Story auto-flagger
# ────────────────────────────────────────────────────────────────


class FakeClock:
    def __init__(self, start: float = 10_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _flagger(clock, mode="full_auto", generator=None, trigger=None, publish=None):
    async def default_generator(match_id, types, context):
        return [{"id": "story-1", "type": types[0], "title": "T"}]

    async def default_trigger(story_id, username):
        return {"id": story_id, "status": "triggered"}

    return StoryAutoFlagger(
        generator=generator or default_generator,
        trigger=trigger or default_trigger,
        mode_provider=lambda: mode,
        publish=publish,
        clock=clock,
        min_interval_s=20.0,
        per_type_cooldown_s=120.0,
        per_entity_cooldown_s=300.0,
    )


async def test_flagger_full_auto_triggers_story():
    clock = FakeClock()
    triggered = []

    async def trigger(story_id, username):
        triggered.append((story_id, username))
        return {"id": story_id}

    flagger = _flagger(clock, mode="full_auto", trigger=trigger)
    flag = await flagger.on_fight_promoted("m-1", ["team-b", "team-a"])
    assert flag is not None and flag["action"] == "triggered"
    assert triggered == [("story-1", "story_auto_flagger")]
    assert flagger.stats["flags_triggered"] == 1


async def test_flagger_semi_auto_and_manual_suggest_only():
    for mode in ("semi_auto", "manual"):
        clock = FakeClock()
        triggered = []
        published = []

        async def trigger(story_id, username):
            triggered.append(story_id)
            return {"id": story_id}

        async def publish(event_type, data):
            published.append((event_type, data))

        flagger = _flagger(clock, mode=mode, trigger=trigger, publish=publish)
        flag = await flagger.on_zone_swing("m-1", "team-a")
        assert flag is not None and flag["action"] == "suggested", mode
        assert triggered == [], mode  # never goes to air without a director
        assert published and published[0][0] == "insight.generated", mode
        assert flagger.stats["flags_suggested"] == 1, mode


async def test_flagger_missing_data_means_no_flag():
    clock = FakeClock()

    async def empty_generator(match_id, types, context):
        return []  # generator found no real data

    flagger = _flagger(clock, generator=empty_generator)
    flag = await flagger.on_fight_promoted("m-1", ["team-a", "team-b"])
    assert flag is None
    assert flagger.stats["skipped_missing_data"] == 1
    assert flagger.stats["flags_triggered"] == 0
    assert flagger.stats["flags_suggested"] == 0


async def test_flagger_cooldowns_prevent_spam():
    clock = FakeClock()
    flagger = _flagger(clock, mode="semi_auto")
    first = await flagger.on_fight_promoted("m-1", ["team-a", "team-b"])
    assert first is not None

    # Immediate second candidate: global min interval blocks it.
    second = await flagger.on_zone_swing("m-1", "team-a")
    assert second is None

    # Past the global interval but inside the per-type cooldown.
    clock.advance(30)
    third = await flagger.on_fight_promoted("m-1", ["team-a", "team-c"])
    assert third is None
    assert flagger.stats["skipped_cooldown"] >= 2

    # Past every cooldown: fires again.
    clock.advance(400)
    fourth = await flagger.on_fight_promoted("m-1", ["team-a", "team-b"])
    assert fourth is not None


async def test_flagger_milestone_needs_resolvable_player_id():
    clock = FakeClock()
    flagger = _flagger(clock)
    # Vision kill lines carry names, not DB ids -> no flag, no attempt.
    assert await flagger.on_kill("m-1", None) is None
    assert flagger.stats["attempts"] == 0
    flag = await flagger.on_kill("m-1", "player-42")
    assert flag is not None and flag["story_type"] == "milestone"


# ────────────────────────────────────────────────────────────────
# Anti-cheat vision subset
# ────────────────────────────────────────────────────────────────


def test_anti_cheat_position_jump_calibrated():
    analyzer = VisionAntiCheatAnalyzer(px_per_meter=1.0, clock=FakeClock())
    assert analyzer.on_position("enemy_0", 0, 0, ts=100.0) is None
    # 100 m in 0.5 s = 200 m/s: impossible.
    flag = analyzer.on_position("enemy_0", 100, 0, ts=100.5)
    assert flag is not None
    assert flag["check_name"] == "impossible_position_jump"
    assert flag["cheat_type"] == "teleport"
    assert flag["evidence"]["speed_mps"] == pytest.approx(200.0)
    # A legal move (10 m in 1 s) raises nothing.
    assert analyzer.on_position("enemy_0", 110, 0, ts=101.5) is None


def test_anti_cheat_position_jump_uncalibrated_is_low_confidence():
    analyzer = VisionAntiCheatAnalyzer(clock=FakeClock())
    assert analyzer.on_position("self", 0, 0, ts=100.0) is None
    assert analyzer.on_position("self", 30, 0, ts=100.5) is None  # small move
    flag = analyzer.on_position("self", 280, 0, ts=101.0)  # 250 px in 0.5 s
    assert flag is not None
    assert flag["confidence"] == "low"
    assert flag["evidence"]["px_per_meter"] is None
    assert "uncalibrated" in flag["evidence"]["coordinate_space"]


def test_anti_cheat_kill_rate_anomaly():
    analyzer = VisionAntiCheatAnalyzer(clock=FakeClock())
    flag = None
    for i in range(9):
        flag = analyzer.on_kill("Scout", ts=100.0 + i * 5, killer_name="Scout")
    assert flag is not None
    assert flag["check_name"] == "kill_feed_rate_anomaly"
    assert flag["evidence"]["kills_in_window"] == 9
    # A normal pace never flags.
    analyzer2 = VisionAntiCheatAnalyzer(clock=FakeClock())
    for i in range(8):
        assert analyzer2.on_kill("Ninja", ts=100.0 + i * 30) is None


def test_anti_cheat_alive_count_inconsistencies():
    analyzer = VisionAntiCheatAnalyzer(clock=FakeClock())
    assert analyzer.on_global_alive_count(48, ts=100.0) is None
    assert analyzer.on_global_alive_count(47, ts=110.0) is None
    flag = analyzer.on_global_alive_count(48, ts=120.0)  # rose again
    assert flag is not None and flag["check_name"] == "alive_count_inconsistency"

    assert analyzer.on_team_alive_count("team-a", "Alpha", 4, ts=100.0) is None
    assert analyzer.on_team_alive_count("team-a", "Alpha", 3, ts=110.0) is None
    flag = analyzer.on_team_alive_count("team-a", "Alpha", 4, ts=120.0)
    assert flag is not None and flag["evidence"]["scope"] == "team"

    assert analyzer.on_hp_update("team-a_player_1", 0, ts=100.0) is None
    flag = analyzer.on_hp_update("team-a_player_1", 55, ts=110.0)
    assert flag is not None and flag["evidence"]["scope"] == "player_hp"


def test_anti_cheat_registry_covers_all_types_honestly():
    registry = checks_registry()
    covered = {c["cheat_type"] for c in registry}
    all_types = {t.value for t in CheatType}
    # Every one of the 12 cheat types is accounted for: live or disabled.
    assert all_types <= covered
    # Disabled checks all carry a real reason; none are silently absent.
    for entry in DISABLED_CHECKS:
        assert entry["status"] == "disabled"
        assert len(entry["reason"]) > 30
    live_names = {c["check"] for c in LIVE_CHECKS}
    assert live_names == {
        "impossible_position_jump",
        "kill_feed_rate_anomaly",
        "alive_count_inconsistency",
    }


async def test_subscriber_persists_flags_via_sink():
    saved: List[Dict[str, Any]] = []

    async def sink(flag):
        saved.append(flag)

    svc = _service(flag_sink=sink)
    # Alive count rises 40 -> 41 via two global updates.
    await svc.handle_record(
        _record("alive_count_update", {"players_alive": 40, "timestamp": time.time()})
    )
    await svc.handle_record(
        _record("alive_count_update", {"players_alive": 41, "timestamp": time.time()})
    )
    assert len(saved) == 1
    assert saved[0]["check_name"] == "alive_count_inconsistency"
    assert svc.stats.flags_raised == 1


# ────────────────────────────────────────────────────────────────
# Review API auth + lifecycle (sqlite, real router)
# ────────────────────────────────────────────────────────────────


def _sqlite_friendly_metadata():
    from app.db.base import Base

    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, postgresql.JSONB):
                column.type = JSON()


@pytest_asyncio.fixture()
async def api_client(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from fastapi import FastAPI

    from app.api import deps
    from app.api.v1.anti_cheat.router import router
    from app.api.v1.anti_cheat.service import AntiCheatFlagService
    from app.db.base import Base
    from app.db.models.user import User
    from app.db.session import get_db

    _sqlite_friendly_metadata()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # H2 (Integrity Fix Pack): get_current_user revocation-checks JWT
    # principals against the DB user's live is_active flag via
    # deps.AsyncSessionLocal, and a missing DB row fails closed (401).
    # Point that lookup at THIS test engine and seed a real (test) DB
    # user behind every hand-minted _token() below — same pattern as
    # tests/unit/test_integrity_credentials_auth.py's auth_env fixture.
    # Passwords are never exercised here (tokens are minted directly);
    # the rows exist so the is_active check has a live account to find.
    monkeypatch.setattr(deps, "AsyncSessionLocal", maker)
    async with maker() as session:
        for role in ("viewer", "operator", "spotter", "observer", "director", "admin"):
            session.add(
                User(
                    id=f"u-{role}",
                    email=f"test-{role}@example.test",
                    username=f"test-{role}",
                    hashed_password="test-only-not-a-real-hash",
                    full_name=f"Test {role}",
                    role=role,
                    is_active=True,
                )
            )
        await session.commit()

    async def override_get_db():
        async with maker() as session:
            yield session

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db

    # Seed one pending flag through the real service.
    async with maker() as session:
        svc = AntiCheatFlagService(session)
        row = await svc.record_flag(
            {
                "match_id": "m-1",
                "check_name": "impossible_position_jump",
                "cheat_type": "teleport",
                "confidence": "high",
                "confidence_score": 75.0,
                "description": "Marker jumped 200 m/s.",
                "evidence": {"speed_mps": 200.0},
                "team_id": "team-a",
                "team_name": "Alpha",
            }
        )
        flag_id = row.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, flag_id
    await engine.dispose()


def _token(role: str) -> str:
    from app.core.security import security_manager

    return security_manager.create_access_token(
        user_id=f"u-{role}", username=f"test-{role}", role=role
    )


async def test_review_api_requires_auth(api_client):
    client, _flag_id = api_client
    resp = await client.get("/api/v1/anti-cheat/flags")
    assert resp.status_code == 401


async def test_review_api_role_gate(api_client):
    client, flag_id = api_client
    # Viewer and even operator are NOT reviewers (admin/director only).
    for role in ("viewer", "operator", "spotter", "observer"):
        resp = await client.get(
            "/api/v1/anti-cheat/flags",
            headers={"Authorization": f"Bearer {_token(role)}"},
        )
        assert resp.status_code == 403, role
    for role in ("director", "admin"):
        resp = await client.get(
            "/api/v1/anti-cheat/flags",
            headers={"Authorization": f"Bearer {_token(role)}"},
        )
        assert resp.status_code == 200, role
        assert len(resp.json()) == 1
        assert resp.json()[0]["id"] == flag_id


async def test_review_api_confirm_dismiss_lifecycle(api_client):
    client, flag_id = api_client
    headers = {"Authorization": f"Bearer {_token('director')}"}

    resp = await client.post(
        f"/api/v1/anti-cheat/flags/{flag_id}/review",
        json={"action": "explode"},
        headers=headers,
    )
    assert resp.status_code == 422

    resp = await client.post(
        "/api/v1/anti-cheat/flags/nope/review",
        json={"action": "confirm"},
        headers=headers,
    )
    assert resp.status_code == 404

    resp = await client.post(
        f"/api/v1/anti-cheat/flags/{flag_id}/review",
        json={"action": "confirm"},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "confirmed"
    assert body["reviewed_by"] == "test-director"
    assert body["evidence"]["speed_mps"] == 200.0

    # A decided flag cannot be silently re-decided.
    resp = await client.post(
        f"/api/v1/anti-cheat/flags/{flag_id}/review",
        json={"action": "dismiss"},
        headers=headers,
    )
    assert resp.status_code == 409


async def test_checks_endpoint_lists_live_and_disabled(api_client):
    client, _flag_id = api_client
    resp = await client.get(
        "/api/v1/anti-cheat/checks",
        headers={"Authorization": f"Bearer {_token('admin')}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert {c["check"] for c in body["live"]} == {
        "impossible_position_jump",
        "kill_feed_rate_anomaly",
        "alive_count_inconsistency",
    }
    assert len(body["disabled"]) >= 8
    assert all(c["reason"] for c in body["disabled"])
