# backend/tests/unit/test_vision_intelligence_derivations.py
"""Phase 24 (W2) — director derivations from spectator signals.

Covers, per derivation: real-input behaviour AND the no-data ->
no-output rule.

  * Zone geometry: per-team signed distance-to-edge / distance-to-
    centre (pixels; metres only when calibrated), zone-pull
    winners/losers snapshots on NEW zone reveals + history.
  * Rotations: path / bearing / speed, start classification vs zone
    phase, urgency bands, vehicle-mode inference from vehicle_markers.
  * Fight forecasting: third-party alerts (>=3 killer teams), fight
    heat with decay, gatekeep ONLY when positions support it.
  * Team shape: spread / split / isolated from per-marker minimap
    coordinates (which MinimapReader does emit), POI naming only for
    normalized map coordinates.
  * Airdrops: loot air_drop log with honest spotting semantics,
    contested from same-slot combat within 60 s, spotter events.
  * Knock sessions: KNOCKED->ALIVE = inferred revive (+ bus event +
    TeamTracker.record_revive wired), KNOCKED->DEAD = bled out, kill
    event = finished, bleed-out urgency thresholds (repeat-scaled).
  * Status snapshot: observed {hp, boost, alive_count, team_status}.
  * Intel API: auth + payload shapes, service-missing honesty.

No network, no Redis, no Postgres — in-memory fakes.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, Mock

import pytest

from app.agents.base_agent import AgentConfig
from app.agents.fight_agents.fight_detector import FightDetector
from app.core.event_bus import BaseEvent, EventRecord
from app.services.vision_intelligence.attribution import (
    StaticSlotResolver,
    TeamAttribution,
)
from app.services.vision_intelligence.derivations import (
    AirdropTracker,
    DirectorDerivations,
    FightForecaster,
    KnockSessionTracker,
    RotationTracker,
    ZoneClock,
    compute_team_shape,
)
from app.services.vision_intelligence.geometry import (
    bearing_degrees,
    bleed_thresholds,
    bleed_urgency_level,
    classify_rotation_start,
    urgency_band,
    zone_distances,
)
from app.services.vision_intelligence.service import (
    TeamTrackers,
    VisionIntelligenceService,
)
from app.services.vision_intelligence.zone_state import ZoneAnalyticsState

pytestmark = pytest.mark.unit

BASE = 1_700_000_000.0


class FakeClock:
    def __init__(self, start: float = BASE):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _record(event_type: str, data: Dict[str, Any], match_id: str = "m-1") -> EventRecord:
    return EventRecord(
        event=BaseEvent(
            event_type=event_type, data=data, match_id=match_id, source="test"
        )
    )


class FakeTeamTracker:
    def __init__(self):
        self.hp_calls: List = []
        self.state_calls: List = []
        self.revive_calls: List = []
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

    def record_revive(self, reviver_id, revived_id):
        self.revive_calls.append((reviver_id, revived_id))
        return True


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


def _service(bus=None, clock=None, **kwargs):
    bus = bus or _capturing_bus()
    clock = clock or FakeClock()
    resolver = StaticSlotResolver(
        {3: TeamAttribution(slot_number=3, team_id="team-a", team_name="Alpha")}
    )
    detector = FightDetector(
        config=AgentConfig(agent_id="fd", agent_type="fight_detector"),
        event_bus=_capturing_bus(),
    )
    return VisionIntelligenceService(
        bus=bus,
        slot_resolver=resolver,
        fight_detector=detector,
        clock=clock,
        **kwargs,
    )


def _intel_alerts(bus, kind=None):
    out = []
    for event_type, data in bus.published:
        if event_type == "intel_alert" and (kind is None or data.get("kind") == kind):
            out.append(data)
    return out


# ────────────────────────────────────────────────────────────────
# Pure geometry
# ────────────────────────────────────────────────────────────────


def test_zone_distances_signed_and_units():
    # Centre (100,100) radius 50: point at distance 30 -> +20 inside.
    d = zone_distances((130.0, 100.0), (100.0, 100.0), 50.0)
    assert d["distance_to_centre_px"] == 30.0
    assert d["distance_to_edge_px"] == 20.0
    assert d["inside_zone"] is True
    assert "distance_to_edge_m" not in d  # uncalibrated: pixels only
    # Outside point: signed edge distance goes negative.
    d2 = zone_distances((180.0, 100.0), (100.0, 100.0), 50.0)
    assert d2["distance_to_edge_px"] == -30.0
    assert d2["inside_zone"] is False
    # Calibrated: metres appear ALONGSIDE pixels.
    d3 = zone_distances((130.0, 100.0), (100.0, 100.0), 50.0, px_per_meter=2.0)
    assert d3["distance_to_edge_m"] == 10.0
    assert d3["distance_to_edge_px"] == 20.0
    # No geometry -> no output.
    assert zone_distances((0.0, 0.0), None, None) is None
    assert zone_distances((0.0, 0.0), (0.0, 0.0), 0) is None


def test_bearing_compass():
    assert bearing_degrees((0.0, 0.0), (0.0, -10.0)) == 0.0  # up = north
    assert bearing_degrees((0.0, 0.0), (10.0, 0.0)) == 90.0  # right = east
    assert bearing_degrees((0.0, 0.0), (0.0, 10.0)) == 180.0
    assert bearing_degrees((5.0, 5.0), (5.0, 5.0)) is None


def test_urgency_bands():
    assert urgency_band(5.0, 100.0, None) == "LOW"  # inside
    assert urgency_band(-50.0, None, 10.0) is None  # no timer -> no call
    # Moving: margin = time_to_close - ETA.
    assert urgency_band(-50.0, 200.0, 50.0) == "LOW"  # margin 150
    assert urgency_band(-50.0, 120.0, 50.0) == "MED"  # margin 70
    assert urgency_band(-50.0, 70.0, 50.0) == "HIGH"  # margin 20
    assert urgency_band(-50.0, 40.0, 50.0) == "CRITICAL"  # cannot make it
    # Not moving at all.
    assert urgency_band(-50.0, 30.0, None) == "CRITICAL"
    assert urgency_band(-50.0, 100.0, None) == "HIGH"
    assert urgency_band(-50.0, 300.0, None) == "MED"


def test_bleed_thresholds_scale_with_repeat_knocks():
    assert bleed_thresholds(0) == (20.0, 45.0)
    warn, critical = bleed_thresholds(1)
    assert warn == 15.0 and critical == 33.8
    assert bleed_urgency_level(10.0, prior_knocks=0) is None
    assert bleed_urgency_level(25.0, prior_knocks=0) == "warn"
    assert bleed_urgency_level(50.0, prior_knocks=0) == "critical"
    # A repeat knock bleeds faster: 16 s is already a warning.
    assert bleed_urgency_level(16.0, prior_knocks=1) == "warn"


def test_classify_rotation_start():
    assert classify_rotation_start(110.0, 100.0, 100.0) == "early"
    assert classify_rotation_start(160.0, 100.0, 100.0) == "late"
    assert classify_rotation_start(110.0, None, 100.0) is None
    assert classify_rotation_start(110.0, 100.0, None) is None
    assert classify_rotation_start(500.0, 100.0, 100.0) is None


# ────────────────────────────────────────────────────────────────
# Zone state: distances + zone pull
# ────────────────────────────────────────────────────────────────


def test_zone_state_team_distances():
    state = ZoneAnalyticsState()
    state.update_zone({"safe_center": [100, 100], "safe_radius": 50}, ts=BASE)
    snap = state.update_team_positions(
        "team-a", {"teammate_0": [130, 100], "teammate_1": [130, 100]}, ts=BASE
    )
    assert snap["distance_to_centre_px"] == 30.0
    assert snap["distance_to_edge_px"] == 20.0
    assert snap["inside_zone"] is True
    # No zone geometry yet -> distances absent, not invented.
    state2 = ZoneAnalyticsState()
    snap2 = state2.update_team_positions("team-a", {"teammate_0": [0, 0]}, ts=BASE)
    assert "distance_to_edge_px" not in snap2


def test_zone_pull_winners_losers_and_history():
    state = ZoneAnalyticsState()
    state.update_team_positions("team-in", {"teammate_0": [100, 100]}, ts=BASE)
    state.update_team_positions("team-out", {"teammate_0": [300, 100]}, ts=BASE)
    # No zone yet -> no snapshot.
    assert state.last_zone_pull is None
    state.update_zone({"safe_center": [100, 100], "safe_radius": 50}, ts=BASE + 10)
    pull = state.last_zone_pull
    assert pull is not None
    assert pull["winners"] == ["team-in"]
    assert pull["losers"] == ["team-out"]
    by_id = {e["team_id"]: e for e in pull["teams"]}
    assert by_id["team-out"]["distance_to_edge_px"] == -150.0
    # Same geometry again -> no NEW snapshot appended.
    state.update_zone({"safe_center": [100, 100], "safe_radius": 50}, ts=BASE + 20)
    assert len(state.zone_pull_history) == 1
    # New reveal appends to queryable history.
    state.update_zone({"safe_center": [300, 100], "safe_radius": 40}, ts=BASE + 30)
    assert len(state.zone_pull_history) == 2
    assert state.zone_pull_snapshot()["last"]["winners"] == ["team-out"]


def test_zone_pull_no_positions_no_snapshot():
    state = ZoneAnalyticsState()
    state.update_zone({"safe_center": [0, 0], "safe_radius": 10}, ts=BASE)
    assert state.last_zone_pull is None
    assert state.zone_pull_history == []


# ────────────────────────────────────────────────────────────────
# Rotations (tracker unit level)
# ────────────────────────────────────────────────────────────────


def test_rotation_tracker_no_data_no_output():
    clock = FakeClock()
    tracker = RotationTracker(ZoneClock(), clock=clock)
    snap = tracker.snapshot(clock(), lambda tid: None)
    assert snap["teams"] == {}
    assert snap["time_to_zone_close_s"] is None


def test_rotation_bearing_speed_and_start_classification():
    clock = FakeClock()
    zone_clock = ZoneClock()
    # Phase starts at BASE with a 200 s timer read on the reset edge.
    zone_clock.observe({"zone_phase": 2, "timer_seconds": 200}, ts=BASE)
    tracker = RotationTracker(zone_clock, clock=clock)
    assert tracker.add_position("team-a", 0, 0, BASE, "observed_minimap") is None
    # Small shuffle below the start displacement: still no rotation.
    assert tracker.add_position("team-a", 10, 0, BASE + 10, "observed_minimap") is None
    # A real move east: rotation starts, bearing 90, speed real.
    started = tracker.add_position("team-a", 60, 0, BASE + 20, "observed_minimap")
    assert started is not None
    assert started["classification"] == "early"  # started before midpoint
    assert started["classification_label"] == "heuristic"
    assert started["inferred"] is True
    snap = tracker.snapshot(BASE + 20, lambda tid: None)
    entry = snap["teams"]["team-a"]
    assert entry["bearing_deg"] == 90.0
    assert entry["speed_px_s"] == pytest.approx(5.0)
    assert entry["urgency"] is None  # no zone-edge distance known
    assert len(entry["path"]) == 3


def test_rotation_urgency_from_zone_clock_and_edge():
    clock = FakeClock()
    zone_clock = ZoneClock()
    zone_clock.observe({"zone_phase": 1, "timer_seconds": 100}, ts=BASE)
    tracker = RotationTracker(zone_clock, clock=clock)
    tracker.add_position("team-a", 0, 0, BASE, "observed_minimap")
    snap = tracker.snapshot(
        BASE + 10, lambda tid: {"distance_to_edge_px": -50.0, "inside_zone": False}
    )
    entry = snap["teams"]["team-a"]
    # time_to_close = 90 s, not moving measurably (single sample) ->
    # ETA None -> HIGH band (<=120 s, not moving).
    assert entry["urgency"] == "HIGH"
    assert entry["urgency_inputs"]["time_to_zone_close_s"] == 90.0


def test_vehicle_rotation_mode_inference():
    clock = FakeClock()
    tracker = RotationTracker(ZoneClock(), clock=clock)
    # Fast sustained movement (20 px/s) with a vehicle marker visible.
    tracker.add_position("team-a", 0, 0, BASE, "observed_minimap")
    tracker.add_vehicle_markers("team-a", 1, BASE)
    for i in range(1, 5):
        tracker.add_position("team-a", 20 * i, 0, BASE + i, "observed_minimap")
    snap = tracker.snapshot(BASE + 4, lambda tid: None)
    assert snap["teams"]["team-a"]["rotation_mode"] == "vehicle"
    assert snap["teams"]["team-a"]["rotation_mode_inferred"] is True

    # Slow movement -> on foot, even with the same speed samples absent.
    tracker2 = RotationTracker(ZoneClock(), clock=clock)
    tracker2.add_position("team-a", 0, 0, BASE, "observed_minimap")
    for i in range(1, 5):
        tracker2.add_position("team-a", 2 * i, 0, BASE + i, "observed_minimap")
    snap2 = tracker2.snapshot(BASE + 4, lambda tid: None)
    assert snap2["teams"]["team-a"]["rotation_mode"] == "on_foot"

    # No movement data at all -> no mode claimed.
    tracker3 = RotationTracker(ZoneClock(), clock=clock)
    tracker3.add_position("team-a", 0, 0, BASE, "observed_minimap")
    snap3 = tracker3.snapshot(BASE, lambda tid: None)
    assert snap3["teams"]["team-a"]["rotation_mode"] is None


# ────────────────────────────────────────────────────────────────
# Team shape
# ────────────────────────────────────────────────────────────────


def test_compute_team_shape_split_and_isolated():
    # Three clustered + one far away: spread large, one isolated.
    markers = {
        "teammate_0": (0.0, 0.0),
        "teammate_1": (10.0, 0.0),
        "teammate_2": (5.0, 8.0),
        "self_0": (200.0, 0.0),
        "enemy_0": (999.0, 999.0),  # anonymous: never part of the shape
    }
    shape = compute_team_shape(markers)
    assert shape["markers"] == 4
    assert shape["is_split"] is True
    assert shape["isolated"]["marker"] == "self_0"
    # Tight squad: no split, nobody isolated.
    tight = compute_team_shape(
        {"teammate_0": (0.0, 0.0), "teammate_1": (8.0, 0.0), "self_0": (4.0, 6.0)}
    )
    assert tight["is_split"] is False
    assert tight["isolated"] is None
    # Fewer than 2 own markers -> no shape exists.
    assert compute_team_shape({"self_0": (0.0, 0.0)}) is None
    assert compute_team_shape({}) is None


def test_poi_naming_only_for_normalized_coordinates():
    class FakePOI:
        def nearest(self, map_id, x, y, max_radius=0.05):
            return {"name": "Pochinki", "map": map_id, "x": x, "y": y,
                    "type": "town", "tier": 1}

    d = DirectorDerivations(clock=FakeClock(), poi_database=FakePOI())
    assert (
        d.poi_name_for("erangel", 0.5, 0.5, "map_normalized") == "Pochinki"
    )
    # Minimap pixels are NOT full-map coordinates: naming would lie.
    assert d.poi_name_for("erangel", 130.0, 100.0, "minimap_region_pixels") is None
    assert d.poi_name_for(None, 0.5, 0.5, "map_normalized") is None
    # Area label falls back honestly to the team's vicinity.
    assert d.area_label_for("team-a", "Alpha") == "Alpha vicinity"
    assert d.area_label_for(None, None) is None


# ────────────────────────────────────────────────────────────────
# Fight forecasting
# ────────────────────────────────────────────────────────────────


def test_third_party_alert_needs_three_killer_teams():
    f = FightForecaster(clock=FakeClock())
    assert f.record_combat(BASE, "kill", "A", "X") is None
    assert f.record_combat(BASE + 5, "kill", "B", "X") is None
    alert = f.record_combat(BASE + 10, "knock", "C", "A")
    assert alert is not None
    assert alert["kind"] == "third_party"
    assert set(alert["killer_teams"]) == {"A", "B", "C"}
    assert "X" in alert["teams"]  # victim teams are involved too
    # Same cluster signature again -> no duplicate alert.
    assert f.record_combat(BASE + 15, "kill", "A", "B") is None
    # Unknown teams never count toward the cluster.
    f2 = FightForecaster(clock=FakeClock())
    f2.record_combat(BASE, "kill", None, "X")
    f2.record_combat(BASE + 1, "kill", None, "Y")
    assert f2.record_combat(BASE + 2, "kill", "A", "Z") is None


def test_fight_heat_decays_and_needs_area():
    f = FightForecaster(clock=FakeClock())
    f.record_combat(BASE, "kill", "A", "B", area_label="Alpha vicinity")
    f.record_combat(BASE, "kill", "A", "B", area_label=None)  # no area claim
    heat = f.heat_snapshot(BASE)
    assert len(heat) == 1
    assert heat[0]["area"] == "Alpha vicinity"
    assert heat[0]["heat"] == pytest.approx(1.0)
    assert heat[0]["event_count"] == 1
    # One half-life later the same single event weighs 0.5.
    assert f.heat_snapshot(BASE + 60)[0]["heat"] == pytest.approx(0.5)
    # Fully decayed out of the window -> nothing.
    assert f.heat_snapshot(BASE + 10_000) == []


def test_gatekeep_requires_all_position_legs():
    f = FightForecaster(clock=FakeClock())
    inside_edge = {"inside_zone": True, "distance_to_edge_px": 10.0}
    outside = {"inside_zone": False, "distance_to_edge_px": -40.0}
    # All legs present -> alert.
    alert = f.evaluate_gatekeep(BASE, "team-a", inside_edge, {"Beta": outside})
    assert alert is not None and alert["kind"] == "gatekeep"
    assert alert["outside_teams"] == ["Beta"]
    assert alert["inferred"] is True
    # Observed team deep inside (not on the edge) -> no call.
    f2 = FightForecaster(clock=FakeClock())
    deep = {"inside_zone": True, "distance_to_edge_px": 100.0}
    assert f2.evaluate_gatekeep(BASE, "team-a", deep, {"Beta": outside}) is None
    # Fight team's position unknown -> no call (the critical honesty case).
    f3 = FightForecaster(clock=FakeClock())
    assert f3.evaluate_gatekeep(BASE, "team-a", inside_edge, {"Beta": None}) is None
    # Fight team actually inside too -> no gatekeep opportunity.
    f4 = FightForecaster(clock=FakeClock())
    assert f4.evaluate_gatekeep(BASE, "team-a", inside_edge, {"Beta": inside_edge}) is None
    # Observed position unknown -> no call.
    f5 = FightForecaster(clock=FakeClock())
    assert f5.evaluate_gatekeep(BASE, "team-a", None, {"Beta": outside}) is None


# ────────────────────────────────────────────────────────────────
# Airdrops
# ────────────────────────────────────────────────────────────────


def test_airdrop_log_semantics_and_contested():
    t = AirdropTracker()
    entry = t.record_crate(BASE, 3, "team-a", "Alpha", source="loot_detector")
    assert entry["spotted_on"] == "spotted on Alpha's feed / near Alpha"
    assert entry["contested"] is False
    # Combat on a DIFFERENT slot does not contest it.
    t.note_combat(7, BASE + 10)
    assert t.snapshot()[0]["contested"] is False
    # Combat on the SAME slot within 60 s does.
    t.note_combat(3, BASE + 30)
    assert t.snapshot()[0]["contested"] is True
    # Combat long after the window does not contest a fresh crate.
    t2 = AirdropTracker()
    t2.note_combat(3, BASE)
    late = t2.record_crate(BASE + 120, 3, "team-a", "Alpha", source="spotter")
    assert late["contested"] is False
    # No team known -> no spotting claim is fabricated.
    anon = t2.record_crate(BASE + 200, None, None, None, source="spotter")
    assert anon["spotted_on"] is None


# ────────────────────────────────────────────────────────────────
# Knock sessions
# ────────────────────────────────────────────────────────────────


def test_knock_session_outcomes():
    k = KnockSessionTracker(clock=FakeClock())
    k.start("team-a_player_2", BASE, "team-a", "Alpha", "team-a_player_2",
            "team_status_transition")
    closed = k.close("team-a_player_2", BASE + 30, "revived",
                     inferred=True, source="team_status_transition")
    assert closed["duration_s"] == 30.0
    assert closed["outcome"] == "revived"
    assert closed["inferred"] is True

    k.start("team-a_player_1", BASE, "team-a", "Alpha", "team-a_player_1",
            "team_status_transition")
    bled = k.close("team-a_player_1", BASE + 50, "bled_out",
                   inferred=True, source="team_status_transition")
    assert bled["outcome"] == "bled_out"

    k.start("name:Victim", BASE, None, "Beta", "Victim", "knock_event")
    finished = k.finish_by_kill("Victim", BASE + 12)
    assert finished["outcome"] == "finished"
    assert finished["inferred"] is False
    # No session -> no output.
    assert k.close("nope", BASE, "revived", True, "x") is None
    assert k.finish_by_kill("Ghost", BASE) is None
    assert k.snapshot(BASE)["active"] == []


def test_knock_bleed_urgency_alerts_once_per_threshold():
    k = KnockSessionTracker(clock=FakeClock())
    k.start("team-a_player_1", BASE, "team-a", "Alpha", "team-a_player_1",
            "team_status_transition")
    assert k.urgency_alerts(BASE + 10) == []
    alerts = k.urgency_alerts(BASE + 25)
    assert len(alerts) == 1
    assert alerts[0]["kind"] == "bleedout_urgency"
    assert alerts[0]["level"] == "warn"
    assert alerts[0]["label"] == "heuristic"
    assert k.urgency_alerts(BASE + 26) == []  # same threshold: no spam
    crit = k.urgency_alerts(BASE + 50)
    assert len(crit) == 1 and crit[0]["level"] == "critical"


# ────────────────────────────────────────────────────────────────
# Service integration
# ────────────────────────────────────────────────────────────────


async def test_service_no_data_no_intel():
    svc = _service()
    assert svc.intel_rotations()["teams"] == {}
    assert svc.intel_fights()["heat"] == []
    assert svc.intel_fights()["alerts"] == []
    assert svc.intel_airdrops()["airdrops"] == []
    assert svc.intel_knocks()["active"] == []
    assert svc.intel_zones()["last"] is None
    observed = svc.snapshot()["observed"]
    assert observed["hp"] is None and observed["boost"] is None
    assert observed["alive_count"] is None and observed["team_status"] is None


async def test_service_zone_pull_alert_and_distances():
    bus = _capturing_bus()
    svc = _service(bus=bus)
    await svc.handle_record(_record("position_update", {
        "observer_slot": 3,
        "player_positions": {"teammate_0": [100, 100], "teammate_1": [104, 100]},
        "timestamp": BASE,
    }))
    await svc.handle_record(_record("zone_event", {
        "zone_phase": 3, "safe_center": [100, 100], "safe_radius": 50,
        "phase_source": "minimap_geometry", "timestamp": BASE + 5,
    }))
    alerts = _intel_alerts(bus, "zone_pull")
    assert len(alerts) == 1
    assert alerts[0]["winners"] == ["team-a"]
    assert alerts[0]["losers"] == []
    zones = svc.intel_zones()
    assert zones["last"]["winners"] == ["team-a"]
    assert len(zones["history"]) == 1
    # After the reveal, per-team distances are live in the zone state.
    await svc.handle_record(_record("position_update", {
        "observer_slot": 3,
        "player_positions": {"teammate_0": [130, 100], "teammate_1": [130, 100]},
        "timestamp": BASE + 6,
    }))
    team = svc.zone_state.teams["team-a"]
    assert team["distance_to_edge_px"] == 20.0
    assert team["distance_to_centre_px"] == 30.0


async def test_service_rotation_flow_and_vehicle_mode():
    bus = _capturing_bus()
    clock = FakeClock()
    svc = _service(bus=bus, clock=clock)
    # Centroid moves east 20 px/s with a vehicle marker present.
    for i in range(5):
        clock.now = BASE + i
        await svc.handle_record(_record("position_update", {
            "observer_slot": 3,
            "player_positions": {
                "teammate_0": [20 * i, 100], "teammate_1": [20 * i + 4, 100],
            },
            "vehicle_markers": 1,
            "timestamp": BASE + i,
        }))
    rotations = [d for t, d in bus.published if t == "rotation_event"]
    assert len(rotations) == 1
    assert rotations[0]["team_id"] == "team-a"
    entry = svc.intel_rotations()["teams"]["team-a"]
    assert entry["bearing_deg"] == 90.0
    assert entry["speed_px_s"] == pytest.approx(20.0)
    assert entry["rotation_mode"] == "vehicle"
    assert len(entry["path"]) == 5


async def test_service_split_and_isolated_alerts():
    bus = _capturing_bus()
    svc = _service(bus=bus)
    await svc.handle_record(_record("position_update", {
        "observer_slot": 3,
        "player_positions": {
            "teammate_0": [0, 0], "teammate_1": [10, 0],
            "teammate_2": [5, 8], "self_0": [200, 0],
            "enemy_0": [999, 999],
        },
        "timestamp": BASE,
    }))
    kinds = {a["kind"] for a in _intel_alerts(bus)}
    assert "split" in kinds and "isolated" in kinds
    iso = _intel_alerts(bus, "isolated")[0]
    assert iso["marker"] == "self_0"
    assert iso["team_name"] == "Alpha"  # observed slot's team, not a guess
    shape = svc.intel_rotations()["teams"]["team-a"]["shape"]
    assert shape["is_split"] is True
    # Minimap pixels cannot name a POI — the field stays honestly null.
    assert shape["area_poi"] is None


async def test_service_third_party_and_heat():
    bus = _capturing_bus()
    svc = _service(bus=bus)
    for i, (killer, kteam) in enumerate(
        [("p1", "Beta"), ("p2", "Gamma"), ("p3", "Delta")]
    ):
        await svc.handle_record(_record("kill_event", {
            "observer_slot": 3,
            "killer": killer, "killerTeam": kteam,
            "victim": "vx", "victimTeam": "Alpha",
            "timestamp": BASE + i * 5,
        }))
    alerts = _intel_alerts(bus, "third_party")
    assert len(alerts) == 1
    assert set(alerts[0]["killer_teams"]) == {"Beta", "Gamma", "Delta"}
    fights = svc.intel_fights()
    assert fights["heat"][0]["area"] == "Alpha vicinity"
    assert fights["heat"][0]["event_count"] == 3
    assert fights["alerts"][0]["kind"] == "third_party"


async def test_service_gatekeep_positive_and_negative():
    # Negative: a fight team with NO known position -> no gatekeep call.
    bus = _capturing_bus()
    svc = _service(bus=bus)
    await svc.handle_record(_record("zone_event", {
        "zone_phase": 4, "safe_center": [100, 100], "safe_radius": 50,
        "timestamp": BASE,
    }))
    await svc.handle_record(_record("position_update", {
        "observer_slot": 3,
        "player_positions": {"teammate_0": [140, 100], "teammate_1": [142, 100]},
        "timestamp": BASE + 1,
    }))
    await svc.handle_record(_record("kill_event", {
        "killer": "b1", "killerTeam": "Beta", "victim": "g1",
        "victimTeam": "Gamma", "timestamp": BASE + 2,
    }))
    assert _intel_alerts(bus, "gatekeep") == []

    # Positive: Beta has a LABELLED position outside; Alpha sits on
    # the edge (10 px inside) -> the opportunity is real.
    bus2 = _capturing_bus()
    svc2 = _service(bus=bus2)
    await svc2.handle_record(_record("zone_event", {
        "zone_phase": 4, "safe_center": [100, 100], "safe_radius": 50,
        "timestamp": BASE,
    }))
    await svc2.handle_record(_record("position_update", {
        "observer_slot": 3,
        "player_positions": {"teammate_0": [140, 100], "teammate_1": [142, 100]},
        "team_positions": {"Beta": [260, 100]},
        "timestamp": BASE + 1,
    }))
    await svc2.handle_record(_record("kill_event", {
        "killer": "b1", "killerTeam": "Beta", "victim": "g1",
        "victimTeam": "Gamma", "timestamp": BASE + 2,
    }))
    alerts = _intel_alerts(bus2, "gatekeep")
    assert len(alerts) == 1
    assert alerts[0]["outside_teams"] == ["Beta"]
    assert alerts[0]["team_id"] == "team-a"


async def test_service_airdrop_loot_spotter_and_contested():
    bus = _capturing_bus()
    svc = _service(bus=bus)
    await svc.handle_record(_record("loot_event", {
        "observer_slot": 3, "loot_type": "air_drop",
        "position": [500, 300], "timestamp": BASE,
    }))
    # A death crate is NOT an airdrop.
    await svc.handle_record(_record("loot_event", {
        "observer_slot": 3, "loot_type": "death_box", "timestamp": BASE + 1,
    }))
    drops = svc.intel_airdrops()["airdrops"]
    assert len(drops) == 1
    assert drops[0]["spotted_on"] == "spotted on Alpha's feed / near Alpha"
    assert drops[0]["contested"] is False
    # Combat on the same slot within 60 s contests it.
    await svc.handle_record(_record("kill_event", {
        "observer_slot": 3, "killer": "x", "killerTeam": "Beta",
        "victim": "y", "victimTeam": "Gamma", "timestamp": BASE + 30,
    }))
    assert svc.intel_airdrops()["airdrops"][0]["contested"] is True
    # Spotter-tagged airdrop on the mapped slot.
    await svc.handle_record(_record("airdrop_spotted", {
        "observer_slot": 3, "location": "Pochinki", "timestamp": BASE + 40,
    }))
    drops = svc.intel_airdrops()["airdrops"]
    assert drops[0]["source"] == "spotter"
    assert drops[0]["location_name"] == "Pochinki"
    # Spotter event on an UNMAPPED slot is dropped, never guessed.
    await svc.handle_record(_record("airdrop_spotted", {
        "observer_slot": 9, "timestamp": BASE + 50,
    }))
    assert len(svc.intel_airdrops()["airdrops"]) == 2
    assert svc.stats.dropped_unmapped == 1


async def test_service_revive_inference_and_record_revive_wiring():
    bus = _capturing_bus()
    team_tracker = FakeTeamTracker()
    svc = _service(
        bus=bus,
        tracker_provider=lambda tid, name: TeamTrackers(
            team_tracker=team_tracker
        ),
    )

    async def status(slots, ts):
        await svc.handle_record(_record("team_status_update", {
            "observer_slot": 3, "slots": slots, "timestamp": ts,
        }))

    await status(["alive", "alive", "alive", "alive"], BASE)
    await status(["alive", "knocked", "alive", "alive"], BASE + 5)
    assert len(svc.intel_knocks()["active"]) == 1
    await status(["alive", "alive", "alive", "alive"], BASE + 35)
    # KNOCKED -> ALIVE with no death in between: inferred revive.
    revives = [d for t, d in bus.published if t == "revive_event"]
    assert len(revives) == 1
    assert revives[0]["inferred"] is True
    assert revives[0]["source"] == "team_status_transition"
    assert revives[0]["team_id"] == "team-a"
    assert revives[0]["knock_duration_s"] == 30.0
    # TeamTracker.record_revive finally has a caller; the reviver is
    # unobserved, so the unknown-reviver id credits nobody.
    assert team_tracker.revive_calls == [("", "team-a_player_2")]
    completed = svc.intel_knocks()["completed"]
    assert completed[0]["outcome"] == "revived"

    # KNOCKED -> DEAD silently: bled out (inferred), no revive event.
    await status(["knocked", "alive", "alive", "alive"], BASE + 100)
    await status(["dead", "alive", "alive", "alive"], BASE + 160)
    completed = svc.intel_knocks()["completed"]
    assert completed[0]["outcome"] == "bled_out"
    assert completed[0]["inferred"] is True
    assert len([d for t, d in bus.published if t == "revive_event"]) == 1


async def test_service_bleedout_urgency_alert():
    bus = _capturing_bus()
    svc = _service(bus=bus)

    async def status(slots, ts):
        await svc.handle_record(_record("team_status_update", {
            "observer_slot": 3, "slots": slots, "timestamp": ts,
        }))

    await status(["alive", "alive", "alive", "alive"], BASE)
    await status(["knocked", "alive", "alive", "alive"], BASE + 1)
    await status(["knocked", "alive", "alive", "alive"], BASE + 30)
    alerts = _intel_alerts(bus, "bleedout_urgency")
    assert len(alerts) == 1
    assert alerts[0]["level"] == "warn"
    assert alerts[0]["knock_duration_s"] == 29.0
    active = svc.intel_knocks()["active"]
    assert len(active) == 1 and active[0]["player"] == "team-a_player_1"


async def test_service_knock_event_session_finished_by_kill():
    svc = _service()
    await svc.handle_record(_record("knock_event", {
        "killer": "k1", "killerTeam": "Alpha",
        "victim": "VictimOne", "victimTeam": "Beta", "timestamp": BASE,
    }))
    active = svc.intel_knocks()["active"]
    assert len(active) == 1 and active[0]["player"] == "VictimOne"
    await svc.handle_record(_record("kill_event", {
        "killer": "k1", "killerTeam": "Alpha",
        "victim": "VictimOne", "victimTeam": "Beta", "timestamp": BASE + 8,
    }))
    assert svc.intel_knocks()["active"] == []
    assert svc.intel_knocks()["completed"][0]["outcome"] == "finished"


async def test_service_observed_snapshot_extension():
    svc = _service()
    await svc.handle_record(_record("hp_update", {
        "observer_slot": 3, "player_id": "player_1", "hp_pct": 55,
        "previous_hp_pct": 100, "boost": 80, "is_knocked": False,
        "timestamp": BASE,
    }))
    await svc.handle_record(_record("team_status_update", {
        "observer_slot": 3,
        "slots": ["alive", "knocked", "alive", "alive"],
        "alive_count": 4, "timestamp": BASE + 1,
    }))
    observed = svc.snapshot()["observed"]
    assert observed["team_id"] == "team-a"
    assert observed["team_name"] == "Alpha"
    assert observed["slot_number"] == 3
    assert observed["hp"] == {"player_1": 55}
    assert observed["boost"] == {"player_1": 80}
    assert observed["alive_count"] == 4
    assert observed["team_status"] == ["alive", "knocked", "alive", "alive"]
    # Existing fields are untouched.
    snap = svc.snapshot()
    assert "active_fights" in snap and "zone" in snap and "stats" in snap


# ────────────────────────────────────────────────────────────────
# Intel API
# ────────────────────────────────────────────────────────────────


@pytest.fixture()
def intel_client(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from fastapi import FastAPI

    from app.api.v1.vision_intelligence import router as router_module

    svc = _service()
    monkeypatch.setattr(
        router_module, "get_vision_intelligence_service", lambda: svc
    )
    app = FastAPI()
    app.include_router(router_module.router, prefix="/api/v1")
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test"), svc


def _token(role: str = "viewer") -> str:
    from app.core.security import security_manager

    return security_manager.create_access_token(
        user_id=f"u-{role}", username=f"test-{role}", role=role
    )


async def test_intel_api_auth_and_shapes(intel_client):
    client, svc = intel_client
    for path in (
        "rotations", "fights", "airdrops", "knocks", "zones",
    ):
        resp = await client.get(f"/api/v1/vision-intelligence/intel/{path}")
        assert resp.status_code == 401, path
    headers = {"Authorization": f"Bearer {_token()}"}
    resp = await client.get(
        "/api/v1/vision-intelligence/intel/rotations", headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["running"] is True
    assert resp.json()["teams"] == {}
    resp = await client.get(
        "/api/v1/vision-intelligence/intel/zones", headers=headers
    )
    assert resp.json()["last"] is None and resp.json()["history"] == []
    resp = await client.get(
        "/api/v1/vision-intelligence/intel/knocks", headers=headers
    )
    assert resp.json()["active"] == []


async def test_intel_api_service_missing_is_honest(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from fastapi import FastAPI

    from app.api.v1.vision_intelligence import router as router_module

    monkeypatch.setattr(
        router_module, "get_vision_intelligence_service", lambda: None
    )
    app = FastAPI()
    app.include_router(router_module.router, prefix="/api/v1")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/vision-intelligence/intel/airdrops",
            headers={"Authorization": f"Bearer {_token()}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is False
    assert body["airdrops"] == []
