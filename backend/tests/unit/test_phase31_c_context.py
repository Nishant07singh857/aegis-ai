# backend/tests/unit/test_phase31_c_context.py
"""Phase 31 Worker C — championship gap + zone-reveal beat + lull
loop + center-hold (audit items 3, 4, 7, 8).

Pure-module tests for storyline / zone_beat / lull / center_hold,
plus one LiveDirector-level proof that the base-0 => score-0 rule
holds for a signalless team no matter how large its storyline
multiplier is (the rule lives in service.py's _score_team and is
factor-agnostic, so it covers the new championship uplift once the
coordinator wires the gap plumbing in _refresh_storyline_context —
see hook H1 in the worker report; the W-side itself is live in
storyline.py today).

REJECTED values are asserted as absent, not just untested: 2x
multiplier, 7s/8s map beats, 12–15s lull legs, ">85" thresholds,
metres, km/h, free-cam/drone, X-ray — none appear in any output.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from app.core.event_bus import EventBus, EventPriority
from app.services.director_service import DirectorService
from app.services.live_director import LiveDirector
from app.services.live_director.center_hold import (
    CENTER_HOLD_DWELL_MIN_S,
    CENTER_HOLD_ZONE_PRESSURE,
    evaluate_center_hold,
    evaluate_center_hold_from_zone_entry,
    stationary_dwell_from_tracker,
)
from app.services.live_director.config import LiveDirectorConfig
from app.services.live_director.lull import (
    DEFAULT_LEG_S,
    LEG_MAX_S,
    LEG_MIN_S,
    LullLoop,
    clamp_leg_duration,
)
from app.services.live_director.storyline import (
    CHAMPIONSHIP_GAP_MAX_POINTS,
    CHAMPIONSHIP_GAP_UPLIFT,
    championship_gap_from_standings,
    compute_storyline,
)
from app.services.live_director.zone_beat import (
    MAP_BEAT_DURATION_S,
    MAP_BEAT_MAX_S,
    MAP_BEAT_MIN_S,
    clamp_map_beat_duration,
    decide_zone_beat,
    zone_pull_terms,
)
from app.services.vision_intelligence.attribution import (
    StaticSlotResolver,
    TeamAttribution,
)

pytestmark = pytest.mark.unit

# ── shared LiveDirector rig (same pattern as test_live_director_p1) ──

SLOTS = {
    1: TeamAttribution(slot_number=1, team_id="team-a", team_name="Team A"),
    2: TeamAttribution(slot_number=2, team_id="team-b", team_name="Team B"),
    3: TeamAttribution(slot_number=3, team_id="team-c", team_name="Team C"),
    4: TeamAttribution(slot_number=4, team_id="team-d", team_name="Team D"),
}

STANDINGS = [
    {"team_id": "team-b", "team_name": "Team B", "rank": 1,
     "total_points": 50, "finishes": 30},
    {"team_id": "team-c", "team_name": "Team C", "rank": 6,
     "total_points": 12, "finishes": 8},
    {"team_id": "team-a", "team_name": "Team A", "rank": 4,
     "total_points": 30, "finishes": 18},
]


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
    def __init__(self, phase: str = "mid_game", **live_kwargs):
        self.clock = FakeClock()
        self.phase = phase
        self.bus = _real_bus()
        self.director_service = DirectorService()
        self.director_service._state["mode"] = "full_auto"
        self.live = LiveDirector(
            bus=self.bus,
            director_service=self.director_service,
            slot_resolver=StaticSlotResolver(SLOTS),
            phase_provider=lambda: self.phase,
            clock=self.clock,
            **live_kwargs,
        )
        self.live.start()


# ══════════════════════════════════════════════════════════════
# 1. CHAMPIONSHIP CLOSE-GAP UPLIFT (audit item 3)
# ══════════════════════════════════════════════════════════════


def test_gap_helper_from_standings_table():
    rows = [
        {"team_id": "t1", "rank": 1, "total_points": 50},
        {"team_id": "t2", "rank": 2, "total_points": 45},
        {"team_id": "t3", "rank": 3, "total_points": 30},
    ]
    assert championship_gap_from_standings(rows) == 5.0
    rows[1]["total_points"] = 44
    assert championship_gap_from_standings(rows) == 6.0


def test_gap_helper_missing_leg_is_none_not_zero():
    assert championship_gap_from_standings([]) is None
    assert championship_gap_from_standings(None) is None
    assert championship_gap_from_standings(
        [{"team_id": "t1", "rank": 1, "total_points": 50}]
    ) is None
    assert championship_gap_from_standings([
        {"team_id": "t1", "rank": 1, "total_points": 50},
        {"team_id": "t2", "rank": 2},  # no points
    ]) is None
    assert championship_gap_from_standings([
        {"team_id": "t1", "rank": 1, "total_points": 50},
        {"team_id": "t2", "rank": 2, "total_points": "n/a"},
    ]) is None


def test_gap_five_gives_both_top2_plus_005():
    cfg = LiveDirectorConfig()
    for rank in (1, 2):
        story = compute_storyline(
            "team-x",
            standings_row={"rank": rank, "total_points": 50},
            championship_gap_points=5.0,
            config=cfg,
        )
        factors = {f["factor"]: f for f in story["factors"]}
        assert factors["championship_close_gap"]["uplift"] == 0.05
        assert story["uplift"] >= 0.05
    # Rank uplifts still apply on top: rank1 0.10 + gap 0.05 = 1.15.
    rank1 = compute_storyline(
        "team-x",
        standings_row={"rank": 1, "total_points": 50},
        championship_gap_points=5.0,
        config=cfg,
    )
    assert rank1["multiplier"] == 1.15
    rank2 = compute_storyline(
        "team-x",
        standings_row={"rank": 2, "total_points": 45},
        championship_gap_points=5.0,
        config=cfg,
    )
    assert rank2["multiplier"] == 1.11  # rank2 0.06 + gap 0.05


def test_gap_six_gives_nothing_and_rank3_never():
    cfg = LiveDirectorConfig()
    story = compute_storyline(
        "team-x",
        standings_row={"rank": 1, "total_points": 50},
        championship_gap_points=6.0,
        config=cfg,
    )
    assert "championship_close_gap" not in {
        f["factor"] for f in story["factors"]
    }
    assert story["multiplier"] == 1.10  # rank1 uplift only
    rank3 = compute_storyline(
        "team-x",
        standings_row={"rank": 3, "total_points": 40},
        championship_gap_points=5.0,
        config=cfg,
    )
    assert "championship_close_gap" not in {
        f["factor"] for f in rank3["factors"]
    }
    # Missing gap contributes nothing, even for rank 1.
    no_gap = compute_storyline(
        "team-x",
        standings_row={"rank": 1, "total_points": 50},
        config=cfg,
    )
    assert "championship_close_gap" not in {
        f["factor"] for f in no_gap["factors"]
    }
    # No standings row at all -> no gap uplift either.
    no_row = compute_storyline("team-x", championship_gap_points=5.0, config=cfg)
    assert no_row == {"multiplier": 1.0, "uplift": 0.0, "factors": []}


def test_championship_uplift_never_breaks_cap_135_and_never_2x():
    cfg = LiveDirectorConfig()
    story = compute_storyline(
        "team-x",
        standings_row={"rank": 1, "total_points": 99},
        championship_gap_points=5.0,
        on_match_point=True,
        recent_finishes=5,
        narrative_flags=["rivalry", "comeback"],
        win_probability=55.0,
        win_prob_is_leader=True,
        config=cfg,
    )
    assert story["multiplier"] == cfg.storyline_max_multiplier == 1.35
    assert story["multiplier"] < 2.0  # the spec's 2x is rejected
    assert CHAMPIONSHIP_GAP_UPLIFT == 0.05
    assert CHAMPIONSHIP_GAP_MAX_POINTS == 5.0


async def test_zero_base_team_stays_zero_despite_storyline():
    # The W-side for a close race is real (rank1 0.10 + gap 0.05),
    # but a team with no fight signal has base 0, so its score is
    # exactly 0 — storyline can never manufacture a switch. This is
    # the service.py rule; the championship factor rides the same
    # multiplier, so the rule covers it once the gap is wired in.
    story = compute_storyline(
        "team-b",
        standings_row={"rank": 1, "total_points": 50},
        championship_gap_points=5.0,
        config=LiveDirectorConfig(),
    )
    assert story["multiplier"] == 1.15
    rig = Rig(standings_provider=lambda: list(STANDINGS))
    rig.live.note_narrative_flag("team-b", "rivalry")
    assert await rig.live.evaluate_now() is None  # no candidate
    assert rig.live.decision_log() == []
    scored = rig.live._score_team("team-b", rig.clock())
    assert scored["base_score"] == 0.0
    assert scored["score"] == 0.0
    assert scored["storyline"]["multiplier"] > 1.0


# ══════════════════════════════════════════════════════════════
# 2. ZONE-REVEAL ON-SCREEN ANSWER (audit item 4)
# ══════════════════════════════════════════════════════════════

PULL = {
    "phase": 3,
    "timestamp": 5000.0,
    "winners": ["team-a", "team-b"],
    "losers": ["team-c"],
}


def test_zone_beat_no_fight_no_final_is_15s_map_beat():
    beat = decide_zone_beat(PULL, fight_active=False, is_final_circle=False)
    assert beat["action"] == "map_beat"
    assert beat["scene"] == "map_cam"
    assert beat["duration_s"] == 15.0
    assert beat["cuts_to_team"] is False
    assert beat["graphic"] == {"winners": ["team-a", "team-b"],
                               "losers": ["team-c"]}


def test_zone_beat_fight_active_is_graphic_only_never_cut():
    beat = decide_zone_beat(PULL, fight_active=True, is_final_circle=False)
    assert beat["action"] == "graphic_only"
    assert beat["scene"] is None
    assert beat["duration_s"] is None
    assert beat["cuts_to_team"] is False
    assert beat["graphic"]["losers"] == ["team-c"]


def test_zone_beat_final_circle_is_graphic_only_no_beat():
    beat = decide_zone_beat(PULL, fight_active=False, is_final_circle=True)
    assert beat["action"] == "graphic_only"
    assert beat["scene"] is None
    assert beat["cuts_to_team"] is False


def test_zone_beat_no_pull_or_no_teams_is_none():
    assert decide_zone_beat(
        None, fight_active=False, is_final_circle=False
    )["action"] == "none"
    assert decide_zone_beat(
        {"winners": [], "losers": []},
        fight_active=False, is_final_circle=False,
    )["action"] == "none"


def test_map_beat_duration_clamp_never_7s_8s_never_over_30s():
    assert clamp_map_beat_duration(7) == 15.0
    assert clamp_map_beat_duration(8) == 15.0
    assert clamp_map_beat_duration(15) == 15.0
    assert clamp_map_beat_duration(20) == 20.0
    assert clamp_map_beat_duration(30) == 30.0
    assert clamp_map_beat_duration(45) == 30.0
    assert clamp_map_beat_duration(None) == 15.0
    assert clamp_map_beat_duration("junk") == 15.0
    assert MAP_BEAT_DURATION_S == 15.0
    assert MAP_BEAT_MIN_S == 15.0
    assert MAP_BEAT_MAX_S == 30.0


def test_zone_pull_terms_differentiate_winners_losers():
    # Appendix B finding 6, differentiated WITHOUT touching live
    # scoring: winners keep zone_pressure +10, losers move to
    # precombat +10 (same magnitude, different term).
    terms = zone_pull_terms(PULL)
    by_team = {t["team_id"]: t for t in terms["terms"]}
    assert by_team["team-a"] == {
        "team_id": "team-a", "term": "zone_pressure",
        "points": 10.0, "role": "winner",
    }
    assert by_team["team-c"] == {
        "team_id": "team-c", "term": "precombat",
        "points": 10.0, "role": "loser",
    }
    assert terms["overlap_team_ids"] == []


def test_zone_pull_terms_overlap_team_gets_winner_term_only():
    # No team inside the new circle: zone_state lists the closest
    # team as BOTH winner and loser. One signal, one term — never
    # double-counted.
    pull = {"winners": ["team-x"], "losers": ["team-x", "team-y"]}
    terms = zone_pull_terms(pull)
    by_team = {t["team_id"]: t for t in terms["terms"]}
    assert by_team["team-x"]["term"] == "zone_pressure"
    assert by_team["team-y"]["term"] == "precombat"
    assert terms["overlap_team_ids"] == ["team-x"]
    assert terms["overlap_note"] is not None
    assert zone_pull_terms(None)["terms"] == []


def test_zone_beat_accepts_dict_shaped_teams():
    pull = {"winners": [{"team_id": "team-a"}],
            "losers": [{"team_id": "team-c"}]}
    beat = decide_zone_beat(pull, fight_active=False, is_final_circle=False)
    assert beat["graphic"] == {"winners": ["team-a"], "losers": ["team-c"]}


# ══════════════════════════════════════════════════════════════
# 3. LULL LOOP (audit item 7)
# ══════════════════════════════════════════════════════════════


def test_lull_loop_cycles_map_standings_pov_with_15_30s_legs():
    loop = LullLoop(leg_duration_s=20.0)
    first = loop.tick(1000.0, phase="loot", fight_active=False,
                      precombat_active=False, top_team_id="team-b",
                      top_team_slot=2)
    assert first["action"] == "suggest"
    assert first["leg"] == "map" and first["scene"] == "map_cam"
    assert first["auto_cut"] is False
    assert first["mode"] == "suggestion_only"
    # Still on the leg: nothing new.
    assert loop.tick(1010.0, phase="loot", fight_active=False,
                     precombat_active=False)["action"] == "none"
    second = loop.tick(1020.0, phase="loot", fight_active=False,
                       precombat_active=False, top_team_id="team-b",
                       top_team_slot=2)
    assert second["leg"] == "standings" and second["scene"] == "standings"
    third = loop.tick(1040.0, phase="loot", fight_active=False,
                      precombat_active=False, top_team_id="team-b",
                      top_team_slot=2)
    assert third["leg"] == "top_team_pov"
    assert third["scene"] == "main"
    assert third["active_camera"] == 2
    assert third["team_id"] == "team-b"
    assert third["auto_cut"] is False
    # Cycle wraps back to map.
    fourth = loop.tick(1060.0, phase="loot", fight_active=False,
                       precombat_active=False, top_team_id="team-b",
                       top_team_slot=2)
    assert fourth["leg"] == "map"


def test_lull_loop_leg_clamp_rejects_spec_12_15s_legs():
    assert clamp_leg_duration(12) == 15.0
    assert clamp_leg_duration(14) == 15.0
    assert clamp_leg_duration(8) == 15.0
    assert clamp_leg_duration(45) == 30.0
    assert clamp_leg_duration(None) == DEFAULT_LEG_S
    assert LEG_MIN_S == 15.0 and LEG_MAX_S == 30.0
    loop = LullLoop(leg_duration_s=12)
    assert loop.leg_duration_s == 15.0
    first = loop.tick(1000.0, phase="early", fight_active=False,
                      precombat_active=False)
    assert first["duration_s"] == 15.0


def test_lull_loop_only_in_loot_early():
    loop = LullLoop()
    for phase in ("plane", "parachute", "mid_game", "late_game",
                  "final_circle", "unknown", "ended"):
        out = loop.tick(1000.0, phase=phase, fight_active=False,
                        precombat_active=False)
        assert out["action"] == "none", phase
        assert out["reason"] == "phase_not_lull"
    assert loop.tick(1000.0, phase="early", fight_active=False,
                     precombat_active=False)["action"] == "suggest"


def test_lull_loop_fight_or_precombat_aborts_immediately():
    loop = LullLoop()
    loop.tick(1000.0, phase="loot", fight_active=False,
              precombat_active=False)
    aborted = loop.tick(1005.0, phase="loot", fight_active=True,
                        precombat_active=False)
    assert aborted["action"] == "abort"
    assert aborted["aborted_leg"] == "map"
    # Next lull restarts at map, not mid-cycle.
    nxt = loop.tick(1010.0, phase="loot", fight_active=False,
                    precombat_active=False)
    assert nxt["leg"] == "map"
    loop2 = LullLoop()
    loop2.tick(1000.0, phase="loot", fight_active=False,
               precombat_active=False)
    aborted2 = loop2.tick(1005.0, phase="loot", fight_active=False,
                          precombat_active=True)
    assert aborted2["action"] == "abort"
    assert aborted2["reason"] == "precombat_active"


def test_lull_loop_pov_leg_skipped_without_top_team_never_invented():
    loop = LullLoop(leg_duration_s=15.0)
    assert loop.tick(1000.0, phase="loot", fight_active=False,
                     precombat_active=False)["leg"] == "map"
    assert loop.tick(1015.0, phase="loot", fight_active=False,
                     precombat_active=False)["leg"] == "standings"
    # No top team known: POV leg skipped, cycle returns to map —
    # no team is ever invented for the POV leg.
    assert loop.tick(1030.0, phase="loot", fight_active=False,
                     precombat_active=False)["leg"] == "map"


def test_lull_loop_pov_leg_carries_unresolved_slot_honestly():
    loop = LullLoop(leg_duration_s=15.0)
    loop.tick(1000.0, phase="loot", fight_active=False,
              precombat_active=False, top_team_id="team-b")
    loop.tick(1015.0, phase="loot", fight_active=False,
              precombat_active=False, top_team_id="team-b")
    pov = loop.tick(1030.0, phase="loot", fight_active=False,
                    precombat_active=False, top_team_id="team-b",
                    top_team_slot=None)
    assert pov["leg"] == "top_team_pov"
    assert pov["team_id"] == "team-b"
    assert pov["active_camera"] is None
    assert pov["slot_unresolved"] is True


# ══════════════════════════════════════════════════════════════
# 4. CENTER-HOLD DERIVATION (audit item 8)
# ══════════════════════════════════════════════════════════════

COMPOUND_POI = {"name": "Mansion", "type": "compound"}
CITY_POI = {"name": "Pochinki", "type": "city"}


def _hold_kwargs(**overrides):
    kwargs = {
        "team_id": "team-b",
        "distance_to_centre_px": 20.0,
        "distance_to_edge_px": 80.0,
        "inside_zone": True,
        "safe_radius_px": 100.0,
        "dwell_s": 90.0,
        "poi": COMPOUND_POI,
        "favoured_team_id": "team-b",
    }
    kwargs.update(overrides)
    return kwargs


def test_center_hold_fires_on_all_four_gates():
    alert = evaluate_center_hold(**_hold_kwargs())
    assert alert is not None
    assert alert["kind"] == "center_hold"
    assert alert["team_id"] == "team-b"
    assert alert["poi_type"] == "compound"
    assert alert["poi_name"] == "Mansion"
    assert alert["inside_zone"] is True
    assert alert["is_favoured"] is True
    assert alert["inferred"] is True
    assert alert["units"] == "minimap_pixels"
    assert "distance_to_centre_m" not in alert  # px only, no metres
    assert alert["suggested_weight"] == {
        "term": "zone_pressure", "points": CENTER_HOLD_ZONE_PRESSURE,
    }
    assert alert["suggestion"]["auto_cut"] is False
    assert alert["suggestion"]["team_id"] == "team-b"
    assert CENTER_HOLD_ZONE_PRESSURE == 10.0


def test_center_hold_each_missing_gate_kills_it():
    # Outside the zone.
    assert evaluate_center_hold(**_hold_kwargs(inside_zone=False)) is None
    # Outside the centre band (centre 20px, radius 100 -> band 50px).
    assert evaluate_center_hold(
        **_hold_kwargs(distance_to_centre_px=50.1)) is None
    assert evaluate_center_hold(
        **_hold_kwargs(distance_to_centre_px=50.0)) is not None
    # Dwell too short (needs 60s).
    assert evaluate_center_hold(**_hold_kwargs(dwell_s=59.9)) is None
    assert evaluate_center_hold(**_hold_kwargs(dwell_s=60.0)) is not None
    # Not a compound POI.
    assert evaluate_center_hold(**_hold_kwargs(poi=CITY_POI)) is None
    assert evaluate_center_hold(**_hold_kwargs(poi=None)) is None
    # Missing geometry / dwell.
    assert evaluate_center_hold(
        **_hold_kwargs(distance_to_centre_px=None)) is None
    assert evaluate_center_hold(**_hold_kwargs(safe_radius_px=None)) is None
    assert evaluate_center_hold(**_hold_kwargs(safe_radius_px=0)) is None
    assert evaluate_center_hold(**_hold_kwargs(dwell_s=None)) is None
    # Unknown / empty team never holds.
    assert evaluate_center_hold(**_hold_kwargs(team_id="Unknown")) is None
    assert evaluate_center_hold(**_hold_kwargs(team_id="")) is None


def test_center_hold_favoured_is_context_not_gate():
    # Another team holds more markers inside, but THIS team still
    # sits on the centre compound, stationary: the hold is real.
    alert = evaluate_center_hold(
        **_hold_kwargs(favoured_team_id="team-a"))
    assert alert is not None
    assert alert["is_favoured"] is False
    assert alert["favoured_team_id"] == "team-a"
    # And favoured unknown is reported as None, not guessed.
    alert2 = evaluate_center_hold(**_hold_kwargs(favoured_team_id=None))
    assert alert2 is not None
    assert alert2["is_favoured"] is None


def test_center_hold_from_zone_entry_reads_zone_state_shape():
    entry = {
        "distance_to_centre_px": 20.0,
        "distance_to_edge_px": 80.0,
        "inside_zone": True,
        "safe_radius": 100.0,  # zone_state's key spelling
    }
    alert = evaluate_center_hold_from_zone_entry(
        team_id="team-b", zone_entry=entry, dwell_s=90.0,
        poi=COMPOUND_POI, favoured_team_id="team-b")
    assert alert is not None
    assert alert["kind"] == "center_hold"
    assert evaluate_center_hold_from_zone_entry(
        team_id="team-b", zone_entry=None, dwell_s=90.0,
        poi=COMPOUND_POI) is None


def test_stationary_dwell_from_rotation_tracker_state():
    # Active rotation -> moving -> no dwell.
    state = {"active_rotation": {"started_at": 900.0},
             "last_move_ts": 900.0, "anchor": (0.0, 0.0, 800.0)}
    assert stationary_dwell_from_tracker(state, 1000.0) is None
    # Quiet since last move.
    state2 = {"active_rotation": None, "last_move_ts": 900.0,
              "anchor": (0.0, 0.0, 800.0)}
    assert stationary_dwell_from_tracker(state2, 1000.0) == 100.0
    # Never moved: dwell since anchor.
    state3 = {"active_rotation": None, "last_move_ts": None,
              "anchor": (10.0, 20.0, 850.0)}
    assert stationary_dwell_from_tracker(state3, 1000.0) == 150.0
    # Nothing at all -> no dwell, no hold.
    assert stationary_dwell_from_tracker(None, 1000.0) is None
    assert stationary_dwell_from_tracker({}, 1000.0) is None
    # Dwell below the 60s hold floor kills the alert even on a compound.
    alert = evaluate_center_hold(
        **_hold_kwargs(dwell_s=stationary_dwell_from_tracker(
            {"active_rotation": None, "last_move_ts": 970.0,
             "anchor": (0.0, 0.0, 800.0)}, 1000.0)))
    assert alert is None
    assert CENTER_HOLD_DWELL_MIN_S == 60.0
