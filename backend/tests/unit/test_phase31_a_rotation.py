# backend/tests/unit/test_phase31_a_rotation.py
"""Phase 31 (Worker A) — dormant-producer wiring + slam anticipation.

Covers items 1 & 6 of docs/DIRECTOR_SPEC_V2_AUDIT.md §1, and only
those: production callers for check_drop_clashes /
propose_phase_suggestions / note_rotation / note_potential_fight,
rotation intel into the director (PVW pre-position ONLY), and the
isolated + nearest-enemy team-level join.

Every end-to-end test drives a REAL LiveDirector (real bus, real
DirectorService, fake clock) and asserts the director actually
received the event — not merely that a function exists.

Hard grammar kept throughout: live fight = hard cuts only, no PiP /
split; PRE_COMBAT never blind-cuts PGM; speeds stay px/s, distances
stay px; no fabricated lead time, no free-cam claim, no named
isolated player (markers are anonymous minimap keys).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.event_bus import BaseEvent, EventRecord, EventBus, EventPriority
from app.services.director_service import DirectorService
from app.services.live_director import LiveDirector
from app.services.live_director.engagement import EngagementState
from app.services.live_director.rotation_intake import (
    BRIDGE_EVENT_TYPES,
    build_rotation_bridge,
    translate_potential_fight,
    translate_rotation_event,
)
from app.services.vision_intelligence.attribution import (
    StaticSlotResolver,
    TeamAttribution,
)

pytestmark = pytest.mark.unit

SLOTS = {
    1: TeamAttribution(slot_number=1, team_id="team-a", team_name="Team A"),
    2: TeamAttribution(slot_number=2, team_id="team-b", team_name="Team B"),
    3: TeamAttribution(slot_number=3, team_id="team-c", team_name="Team C"),
    4: TeamAttribution(slot_number=4, team_id="team-d", team_name="Team D"),
}


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _real_bus() -> EventBus:
    bus = EventBus()
    bus._redis = AsyncMock()
    bus._redis.xadd = AsyncMock(return_value="1-0")
    bus._pubsub_redis = AsyncMock()
    bus._pubsub_redis.publish = AsyncMock(return_value=1)
    bus._consumer_redis = AsyncMock()
    bus._running = True
    return bus


class Rig:
    """LiveDirector + DirectorService + real bus, team A on PGM."""

    def __init__(self, phase: str = "mid_game", **live_kwargs):
        self.clock = FakeClock()
        self.phase = phase
        self.bus = _real_bus()
        self.director_service = DirectorService()
        self.live = LiveDirector(
            bus=self.bus,
            director_service=self.director_service,
            slot_resolver=StaticSlotResolver(SLOTS),
            phase_provider=lambda: self.phase,
            clock=self.clock,
            **live_kwargs,
        )
        self.live.start()

    async def publish(self, event_type: str, data: Dict[str, Any]) -> None:
        await self.bus.publish(
            event_type=event_type,
            data=data,
            priority=EventPriority.HIGH,
            source="test",
            match_id="m-1",
        )

    def pgm(self) -> Any:
        return self.director_service.snapshot()["active_camera"]

    def preview(self) -> Any:
        return self.director_service.snapshot()["preview"]


def _bridge_rig(rig: Rig):
    bridge = build_rotation_bridge(rig.live)
    rig.bus.subscribe(
        subscriber_id="phase31_rotation_intake_test",
        callback=bridge,
        event_types=list(BRIDGE_EVENT_TYPES),
    )
    return bridge


# ── pure translation ──────────────────────────────────────────────


def test_translate_plain_rotation_is_rotating_not_precombat():
    spec = translate_rotation_event(
        {
            "team_id": "team-b",
            "rotation_mode": "vehicle",
            "bearing_deg": 90.0,
            "speed_px_s": 20.0,
        }
    )
    assert spec is not None
    assert spec["team_id"] == "team-b"
    assert spec["active"] is True
    assert spec["occupied_by"] is None
    assert spec["precombat_expected"] is False
    assert spec["rotation_mode"] == "vehicle"
    assert spec["speed_px_s"] == 20.0
    # Slam intent is PVW-only, with no lead-time claim and no
    # fabricated units anywhere in the spec.
    assert spec["slam_preposition"]["action"] == "pvw_preposition_only"
    assert spec["slam_preposition"]["never_pgm_cut"] is True
    assert spec["slam_preposition"]["lead_time_claim"] is None
    blob = json.dumps(spec)
    assert "km/h" not in blob and "km_h" not in blob
    assert "distance_m" not in blob and "speed_m_s" not in blob


def test_translate_occupied_rotation_expects_precombat():
    spec = translate_rotation_event(
        {
            "team_id": "team-c",
            "occupied_by": "team-b",
            "poi": "Pochinki",
            "occupancy_basis": "shared_current_area_poi",
        }
    )
    assert spec is not None
    assert spec["precombat_expected"] is True
    assert spec["occupied_by"] == "team-b"
    assert spec["poi"] == "Pochinki"
    assert spec["slam_preposition"]["precombat"] is True


def test_translate_rotation_drops_self_occupancy_and_teamless():
    spec = translate_rotation_event(
        {"team_id": "team-b", "occupied_by": "team-b", "poi": "Pochinki"}
    )
    assert spec is not None and spec["occupied_by"] is None
    assert spec["precombat_expected"] is False
    assert translate_rotation_event({}) is None
    assert translate_rotation_event({"team_id": "  "}) is None
    assert translate_rotation_event({"team_id": "Unknown"}) is None
    assert translate_rotation_event("not-a-dict") is None  # type: ignore[arg-type]
    ended = translate_rotation_event(
        {"team_id": "team-b", "kind": "rotation_ended"}
    )
    assert ended is not None and ended["active"] is False


def test_translate_potential_fight_clamps_and_requires_teams():
    spec = translate_potential_fight(
        {"teams": ["team-b", "team-c"], "confidence": 1.5}
    )
    assert spec is not None
    assert spec["team_ids"] == ["team-b", "team-c"]
    assert spec["confidence"] == 1.0  # clamped, never > 1
    assert translate_potential_fight({"teams": [], "confidence": 0.6}) is None
    assert translate_potential_fight({"teams": ["team-b"]}) is None


# ── bridge -> real director, over the real bus ────────────────────


async def test_rotation_into_occupied_poi_prepositions_pvw_never_pgm():
    rig = Rig()
    _bridge_rig(rig)
    assert rig.pgm() == 1
    await rig.publish(
        "rotation_event",
        {
            "team_id": "team-b",
            "occupied_by": "team-c",
            "poi": "Pochinki",
            "occupancy_basis": "shared_current_area_poi",
            "rotation_mode": "vehicle",
            "speed_px_s": 20.0,
        },
    )
    assert rig.live.engagement.state_of("team-b") == EngagementState.PRE_COMBAT
    # Slam anticipation: PVW pre-positioned on the rotating team...
    preview = rig.preview()
    assert preview is not None and preview["team_id"] == "team-b"
    assert preview["slot"] == 2
    # ...but PGM NEVER blind-cuts on PRE_COMBAT alone.
    assert rig.pgm() == 1


async def test_plain_rotation_is_rotating_and_moves_nothing_on_air():
    rig = Rig()
    _bridge_rig(rig)
    await rig.publish(
        "rotation_event",
        {"team_id": "team-b", "rotation_mode": "on_foot", "speed_px_s": 5.0},
    )
    assert rig.live.engagement.state_of("team-b") == EngagementState.ROTATING
    assert rig.preview() is None
    assert rig.pgm() == 1


async def test_vehicle_rotation_without_occupancy_stays_rotating():
    # Honest boundary: engagement derives PRE_COMBAT from occupied
    # POI only — vehicle mode alone is movement, not convergence, so
    # it stays ROTATING (no manufactured PRE_COMBAT, no PGM cut).
    rig = Rig()
    bridge = _bridge_rig(rig)
    await bridge(EventRecord(event=BaseEvent(
        event_type="rotation_event",
        data={"team_id": "team-b", "rotation_mode": "vehicle",
              "speed_px_s": 22.0},
        match_id="m-1", source="test",
    )))
    assert rig.live.engagement.state_of("team-b") == EngagementState.ROTATING
    assert rig.pgm() == 1
    assert bridge.stats["forwarded"] == 1


async def test_potential_fight_bridge_threshold_is_the_directors():
    rig = Rig()
    _bridge_rig(rig)
    # At/above the director's 0.5 entry threshold -> PRE_COMBAT.
    await rig.publish(
        "potential_fight",
        {"teams": ["team-b"], "confidence": 0.6, "fight_id": "pf-1"},
    )
    assert rig.live.engagement.state_of("team-b") == EngagementState.PRE_COMBAT
    # Below threshold -> weak signal only, no state change.
    await rig.publish(
        "potential_fight",
        {"teams": ["team-c"], "confidence": 0.3, "fight_id": "pf-2"},
    )
    assert rig.live.engagement.state_of("team-c") != EngagementState.PRE_COMBAT
    assert rig.pgm() == 1


async def test_bridge_drops_teamless_and_unknown_events_without_raising():
    rig = Rig()
    bridge = _bridge_rig(rig)
    await rig.publish("rotation_event", {"rotation_mode": "vehicle"})
    await rig.publish("unrelated_topic", {"team_id": "team-b"})
    assert bridge.stats["dropped"] >= 1
    assert bridge.stats["errors"] == 0
    assert rig.live.engagement.state_of("team-b") != EngagementState.PRE_COMBAT


async def test_fight_promotion_after_precombat_moves_to_active_combat():
    # Promotion is the moment the fight path takes over — via the
    # normal fight machinery, not via the rotation intake. (Mode is
    # semi_auto here, so PGM itself stays put; the assertion is the
    # engagement transition, which is what the intake feeds.)
    rig = Rig()
    _bridge_rig(rig)
    await rig.publish(
        "rotation_event",
        {"team_id": "team-b", "occupied_by": "team-c", "poi": "Pochinki"},
    )
    assert rig.live.engagement.state_of("team-b") == EngagementState.PRE_COMBAT
    assert rig.pgm() == 1
    await rig.publish(
        "fight.detected",
        {
            "fight_id": "fight-1",
            "teams_involved": ["team-b", "team-c"],
            "intensity": 80.0,
            "fight_type": "mid_range",
        },
    )
    assert rig.live.engagement.state_of("team-b") == EngagementState.ACTIVE_COMBAT


# ── vision intelligence: rotation enrichment + isolated join ─────


def _capturing_bus():
    bus = Mock()
    bus.published: List = []

    async def _publish(event_type=None, data=None, **kwargs):
        bus.published.append((event_type, data))
        return "evt"

    bus.publish = AsyncMock(side_effect=_publish)
    bus.subscribe = Mock(return_value="sub")
    bus.unsubscribe = Mock(return_value=True)
    bus._running = True
    return bus


def _record(event_type: str, data: Dict[str, Any]) -> EventRecord:
    return EventRecord(
        event=BaseEvent(
            event_type=event_type, data=data, match_id="m-1", source="vision"
        )
    )


class _FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now


def _vi_service(bus=None, clock=None, **kwargs):
    from app.agents.base_agent import AgentConfig
    from app.agents.fight_agents.fight_detector import FightDetector
    from app.services.vision_intelligence.service import (
        VisionIntelligenceService,
    )

    bus = bus or _capturing_bus()
    clock = clock or _FakeClock()
    resolver = StaticSlotResolver(
        {
            3: TeamAttribution(slot_number=3, team_id="team-a", team_name="Alpha"),
            4: TeamAttribution(slot_number=4, team_id="team-b", team_name="Beta"),
        }
    )
    detector = FightDetector(
        config=AgentConfig(agent_id="fd", agent_type="fight_detector"),
        event_bus=_capturing_bus(),
    )
    return VisionIntelligenceService(
        bus=bus, slot_resolver=resolver, fight_detector=detector,
        clock=clock, **kwargs,
    )


async def test_vi_rotation_event_carries_px_motion_not_poi_on_minimap_feed():
    # The live minimap feed reports minimap_region_pixels, which can
    # never name a POI — so the rotation event honestly carries
    # mode/bearing/speed (px/s) and NO occupancy, instead of a
    # guessed POI. Sub-second samples: vehicle mode needs >= 3 speed
    # samples above 12 px/s before the 25 px start threshold is
    # crossed, so 5 px steps every 0.1 s (50 px/s) cross it on the
    # 5th sample with the mode already measurable.
    bus = _capturing_bus()
    clock = _FakeClock()
    svc = _vi_service(bus=bus, clock=clock)
    for i in range(6):
        t = 1000.0 + 0.1 * i
        clock.now = t
        await svc.handle_record(_record("position_update", {
            "observer_slot": 3,
            "player_positions": {
                "teammate_0": [5 * i, 100], "teammate_1": [5 * i + 4, 100],
            },
            "vehicle_markers": 1,
            "timestamp": t,
        }))
    rotations = [d for t, d in bus.published if t == "rotation_event"]
    assert len(rotations) == 1
    event = rotations[0]
    assert event["team_id"] == "team-a"
    assert event["rotation_mode"] == "vehicle"
    assert event["bearing_deg"] == 90.0
    assert event["speed_px_s"] == pytest.approx(50.0)
    assert event.get("poi") is None and event.get("occupied_by") is None
    blob = json.dumps(event)
    assert "km/h" not in blob


async def test_vi_occupancy_derivation_shared_area_poi():
    # Unit level: the derivation logic itself, with the two legs
    # seeded independently (rotation tracking in px, POI naming in
    # normalized space — see report: a single live feed carries one
    # space, so this leg awaits a normalized-coordinate source).
    from app.services.vision_intelligence.derivations import (
        DirectorDerivations,
    )

    class _StubPoiDb:
        def nearest(self, map_id, x, y, max_radius=0.05):
            return {"name": "Pochinki" if x > 0.4 else "Georgopol"}

    svc = _vi_service(
        derivations=DirectorDerivations(poi_database=_StubPoiDb())
    )
    svc.derivations.rotations.teams["team-a"] = {
        "team_id": "team-a", "team_name": "Alpha",
        "samples": [
            {"ts": 1000.0, "x": 500.0, "y": 500.0, "source": "observed_minimap"},
            {"ts": 1001.0, "x": 520.0, "y": 500.0, "source": "observed_minimap"},
        ],
        "anchor": (500.0, 500.0, 1000.0),
        "pending_start_ts": None, "last_move_ts": 1001.0,
        "active_rotation": None, "vehicle_series": [],
        "speed_samples": [{"ts": 1001.0, "speed_px_s": 20.0}],
        "shape": None, "map_id": "erangel", "coordinate_space": None,
    }
    svc.derivations.note_position_context(
        "team-b", 0.5, 0.5, "erangel", "normalized_map"
    )
    svc.derivations.note_position_context(
        "team-c", 0.1, 0.1, "erangel", "normalized_map"
    )
    extras = svc._rotation_event_extras(
        "team-a", [0.5, 0.5], "erangel", "normalized_map", 1001.0
    )
    assert extras["poi"] == "Pochinki"
    assert extras["occupied_by"] == "team-b"
    assert extras["occupancy_basis"] == "shared_current_area_poi"
    assert extras["speed_px_s"] == pytest.approx(20.0)
    # Same POI name on another map is NOT occupancy.
    svc.derivations.note_position_context(
        "team-b", 0.5, 0.5, "miramar", "normalized_map"
    )
    extras2 = svc._rotation_event_extras(
        "team-a", [0.5, 0.5], "erangel", "normalized_map", 1001.0
    )
    assert extras2.get("occupied_by") is None


async def test_vi_isolated_alert_joins_nearest_enemy_in_px():
    bus = _capturing_bus()
    svc = _vi_service(bus=bus)
    await svc.handle_record(_record("position_update", {
        "observer_slot": 3,
        "player_positions": {
            "teammate_0": [0, 0], "teammate_1": [10, 0],
            "teammate_2": [5, 8], "self_0": [200, 0],
            "enemy_0": [60, 0],
        },
        "timestamp": 1000.0,
    }))
    iso = [
        d for t, d in bus.published
        if t == "intel_alert" and d.get("kind") == "isolated"
    ]
    assert len(iso) == 1
    alert = iso[0]
    # Team-level only: the marker key is anonymous, never a player name.
    assert alert["team_id"] == "team-a"
    assert alert["marker"] == "self_0"
    # Own centroid (53.75, 2) -> enemy (60, 0): ~6.6 px, px units only.
    assert alert["nearest_enemy_px"] == pytest.approx(6.6, abs=0.2)
    assert alert["nearest_enemy_units"] == "minimap_pixels"
    assert "nearest_enemy_m" not in alert


async def test_vi_potential_fight_emitted_once_then_on_rise():
    from app.agents.fight_agents.fight_detector import (
        FightSignal,
        PotentialFight,
    )
    from app.services.vision_intelligence.service import TeamTrackers

    bus = _capturing_bus()
    svc = _vi_service(
        bus=bus,
        tracker_provider=lambda tid, name: TeamTrackers(team_tracker=Mock()),
    )
    svc.fight_detector.detection_state.potential_fights["pf-1"] = PotentialFight(
        fight_id="pf-1",
        teams_involved={"team-a", "team-b"},
        fight_probability=0.65,
        signal_count=3,
        signal_types={FightSignal.HP_DROP},
    )

    async def _tick():
        await svc.handle_record(_record("hp_update", {
            "observer_slot": 3,
            "player_id": "player_1",
            "hp_pct": 55,
            "previous_hp_pct": 100,
            "timestamp": 1000.0,
        }))

    await _tick()
    pf = [d for t, d in bus.published
          if t == "potential_fight" and d.get("fight_id") == "pf-1"]
    assert len(pf) == 1
    assert set(pf[0]["teams"]) == {"team-a", "team-b"}
    # Confidence is whatever the detector currently computes (the HP
    # signal above recomputes it before the flush) — VI passes it
    # through untouched; the assertions below pin the rise logic
    # deterministically via direct flush calls.
    assert 0.0 <= pf[0]["confidence"] <= 1.0
    # Repeat flush: no duplicate (first-sight dedup).
    await svc._after_signals("m-1")
    pf = [d for t, d in bus.published
          if t == "potential_fight" and d.get("fight_id") == "pf-1"]
    assert len(pf) == 1
    # Probability rises >= 0.1: re-emitted with the new confidence.
    svc.fight_detector.detection_state.potential_fights["pf-1"].fight_probability = 0.8
    await svc._after_signals("m-1")
    pf = [d for t, d in bus.published
          if t == "potential_fight" and d.get("fight_id") == "pf-1"]
    assert len(pf) == 2
    assert pf[-1]["confidence"] == 0.8


# ── phase suggestions: edge-triggered production entry ────────────


async def test_phase_suggestions_propose_once_per_phase_entry():
    svc = DirectorService()
    svc._state["mode"] = "semi_auto"
    created = await svc.propose_phase_suggestions_on_change({"phase": "loot"})
    assert len(created) == 1
    assert created[0]["type"] == "scene"
    assert created[0]["payload"] == {"scene": "map_cam"}
    assert len(svc.list_suggestions()) == 1
    # Same phase again: silent, no duplicate suggestion.
    assert await svc.propose_phase_suggestions_on_change({"phase": "loot"}) == []
    assert len(svc.list_suggestions()) == 1
    # New phase: proposes again (late_game's plan has two items:
    # a standings scene + a win-probability graphic).
    created = await svc.propose_phase_suggestions_on_change(
        {"phase": "late_game"}
    )
    assert len(created) == 2
    assert {"scene": "standings"} in [c["payload"] for c in created]


async def test_phase_suggestions_silent_outside_semi_auto():
    svc = DirectorService()
    svc._state["mode"] = "manual"
    assert await svc.propose_phase_suggestions_on_change({"phase": "loot"}) == []
    assert svc.list_suggestions() == []


# ── drop clash: history / current split ───────────────────────────


class _FakePoiDb:
    POIS = {
        ("erangel", "Alpha"): {"name": "Alpha", "x": 0.1, "y": 0.1},
        ("erangel", "Beta"): {"name": "Beta", "x": 0.9, "y": 0.9},
        ("erangel", "Mid"): {"name": "Mid", "x": 0.5, "y": 0.52},
    }

    def by_name(self, map_id, name):
        return self.POIS.get((map_id, name))

    def nearest(self, map_id, x, y, max_radius=0.05):
        return None


async def test_drop_clash_history_vs_current_split():
    from app.services.flight_path import FlightPathService

    svc = FlightPathService(poi_db=_FakePoiDb(), publish=AsyncMock())
    await svc.set_path(map_id="erangel", start_poi="Alpha", end_poi="Beta")
    # History (past matches): t1 + t2 usually drop Mid.
    history = [
        {"team_id": "t1", "team_name": "One", "named_location": "Mid"},
        {"team_id": "t2", "team_name": "Two", "named_location": "Mid"},
    ]
    # This match: team-b + team-c actually dropped Mid.
    current = [
        {"team_id": "team-b", "team_name": "Team B", "named_location": "Mid"},
        {"team_id": "team-c", "team_name": "Team C", "named_location": "Mid"},
    ]
    fresh = await svc.check_drop_clashes(current, history_rows=history)
    by_basis = {a["basis"]: a for a in fresh}
    assert set(by_basis) == {"predicted", "observed"}
    # Predicted comes from HISTORY, observed from THIS match — old
    # matches' contests must never alert as this match's observed.
    assert by_basis["predicted"]["teams"] == ["t1", "t2"]
    assert by_basis["observed"]["teams"] == ["team-b", "team-c"]
    # Repeat: deduped, no spam.
    assert await svc.check_drop_clashes(current, history_rows=history) == []


async def test_drop_clash_without_history_rows_preserves_old_behaviour():
    from app.services.flight_path import FlightPathService

    svc = FlightPathService(poi_db=_FakePoiDb(), publish=AsyncMock())
    await svc.set_path(map_id="erangel", start_poi="Alpha", end_poi="Beta")
    rows = [
        {"team_id": "t1", "team_name": "One", "named_location": "Mid"},
        {"team_id": "t2", "team_name": "Two", "named_location": "Mid"},
    ]
    fresh = await svc.check_drop_clashes(rows)
    assert {a["basis"] for a in fresh} == {"predicted", "observed"}
    assert all(a["teams"] == ["t1", "t2"] for a in fresh)


async def test_fetch_drop_rows_joins_names_and_scopes_map():
    from app.analysis.models import DropEvent
    from app.db.models.team import Team
    from app.services.flight_path import fetch_drop_rows

    class _Scalars:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return _Scalars(self._rows)

    class _FakeDb:
        def __init__(self, results):
            self._results = list(results)

        async def execute(self, stmt):
            return _Result(self._results.pop(0))

    drops = [
        DropEvent(team_id="t1", match_id="m-1", map_name="erangel",
                  named_location="Mid", location_x=0.5, location_y=0.52,
                  timestamp_ms=1000, contested_by=[]),
        DropEvent(team_id="t2", match_id="m-1", map_name="miramar",
                  named_location="Mid", location_x=0.5, location_y=0.52,
                  timestamp_ms=1000, contested_by=[]),
    ]
    teams = [Team(id="t1", name="One")]
    rows = await fetch_drop_rows(_FakeDb([drops, teams]), map_id="erangel")
    # Miramar row scoped out by the Python map filter; name joined.
    assert len(rows) == 1
    assert rows[0]["team_id"] == "t1"
    assert rows[0]["team_name"] == "One"
    assert rows[0]["named_location"] == "Mid"

    class _BoomDb:
        async def execute(self, stmt):
            raise RuntimeError("db down")

    assert await fetch_drop_rows(_BoomDb(), map_id="erangel") == []


# ── main.py wiring presence (lifespan needs the full app; the ticker
# and bridge subscription are verified here as wired code, and the
# behaviour they call is covered by the tests above) ───────────────


def test_main_wiring_contains_bridge_subscription_and_ticker():
    import pathlib

    main_py = (
        pathlib.Path(__file__).resolve().parents[2] / "app" / "main.py"
    )
    text = main_py.read_text()
    assert "phase31_rotation_intake" in text
    assert "build_rotation_bridge" in text
    assert "propose_phase_suggestions_on_change" in text
    assert "phase31_ticker_task" in text
    assert "check_drop_clashes" in text
