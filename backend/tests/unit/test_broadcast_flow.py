# backend/tests/unit/test_broadcast_flow.py
"""Unit tests for the Phase 10 broadcast flow (Worker W2):

  * pre-show service: step transitions, invalid step rejection, countdown
    target set/clear, WS message shape
  * WWCD ceremony service: start -> advance through all steps -> done,
    invalid transitions (advance before start, advance past done, start
    while running, start without winner), reset clears state, WS message
    shape
  * match-point engine: points_threshold logic (below/at/above), disabled
    switch, final_day_chicken gating (before final day -> nobody; on final
    day -> threshold teams with smash reason), config validation
  * zone timer: no_data honesty, phase schedule validation, advance through
    the schedule to complete, remaining-time math (computed from started_at),
    reset restarts phase 1, WS message shape

Service-level only: fresh service instances per test (never the process
singletons), async via pytest-asyncio auto mode. No network, no DB, no
real credentials.
"""

from __future__ import annotations

import time

import pytest

from app.services.ceremony import (
    STEPS as CEREMONY_STEPS,
    WS_MESSAGE_TYPE as CEREMONY_WS_TYPE,
    CeremonyService,
    InvalidCeremonyTransitionError,
)
from app.services.match_point import (
    MatchPointService,
    InvalidMatchPointConfigError,
)
from app.services.preshow import (
    STEPS as PRESHOW_STEPS,
    WS_MESSAGE_TYPE as PRESHOW_WS_TYPE,
    InvalidPreshowStepError,
    PreshowService,
)
from app.services.zone import (
    WS_MESSAGE_TYPE as ZONE_WS_TYPE,
    InvalidZoneStateError,
    ZoneService,
)


# ── shared listener fixture ──────────────────────────────────────


def _listener_into(messages: list):
    async def _listener(message: dict) -> None:
        messages.append(message)

    return _listener


# ══════════════════════════════════════════════════════════════════
# A. Pre-show
# ══════════════════════════════════════════════════════════════════


@pytest.fixture()
def preshow() -> PreshowService:
    return PreshowService()


@pytest.fixture()
def preshow_messages() -> list:
    return []


def test_preshow_starts_at_countdown_with_no_target(preshow: PreshowService):
    state = preshow.snapshot()
    assert state["step"] == "countdown"
    assert state["countdown_target"] is None  # honest: no target invented


async def test_preshow_step_transitions(preshow: PreshowService):
    for step in ("desk_intro", "match_intro", "starting"):
        state = await preshow.set_step(step)
        assert state["step"] == step
    # reset goes back to countdown
    state = await preshow.reset()
    assert state["step"] == "countdown"
    assert state["countdown_target"] is None


async def test_preshow_invalid_step_rejected(preshow: PreshowService):
    with pytest.raises(InvalidPreshowStepError):
        await preshow.set_step("halftime_show")
    # state untouched
    assert preshow.snapshot()["step"] == "countdown"


async def test_preshow_countdown_target_set_and_parsed(
    preshow: PreshowService,
):
    state = await preshow.set_countdown_target("2026-10-18T20:00:00+05:30")
    assert state["countdown_target"] == "2026-10-18T20:00:00+05:30"


async def test_preshow_countdown_target_naive_assumed_utc(
    preshow: PreshowService,
):
    state = await preshow.set_countdown_target("2026-10-18T20:00:00")
    assert state["countdown_target"].endswith("+00:00")


async def test_preshow_invalid_target_rejected(preshow: PreshowService):
    with pytest.raises(InvalidPreshowStepError):
        await preshow.set_countdown_target("tomorrow evening")


async def test_preshow_same_step_is_silent_noop(
    preshow: PreshowService, preshow_messages: list
):
    preshow.add_listener(_listener_into(preshow_messages))
    await preshow.set_step("countdown")  # already there
    assert preshow_messages == []


async def test_preshow_ws_message_shape(
    preshow: PreshowService, preshow_messages: list
):
    preshow.add_listener(_listener_into(preshow_messages))
    await preshow.set_step("desk_intro")
    assert len(preshow_messages) == 1
    message = preshow_messages[0]
    assert message["type"] == PRESHOW_WS_TYPE == "preshow.state"
    assert message["data"]["step"] == "desk_intro"
    assert "steps" in message["data"]
    assert set(PRESHOW_STEPS) == {
        "countdown",
        "desk_intro",
        "match_intro",
        "starting",
    }


# ══════════════════════════════════════════════════════════════════
# B. WWCD ceremony
# ══════════════════════════════════════════════════════════════════


@pytest.fixture()
def ceremony() -> CeremonyService:
    return CeremonyService()


@pytest.fixture()
def ceremony_messages() -> list:
    return []


def test_ceremony_starts_idle(ceremony: CeremonyService):
    state = ceremony.snapshot()
    assert state["step"] == "idle"
    assert state["winner"]["team_name"] is None
    assert state["stats"] is None
    assert state["interview"] is None


async def test_ceremony_full_run(ceremony: CeremonyService):
    state = await ceremony.start(
        winner_team_id="team-8bit",
        team_name="8Bit",
        stats={"placement_points": 10, "finishes": 14},
        interview={"player": "Sarang", "quote": "We just played our game."},
    )
    assert state["step"] == "winner_announce"
    assert state["winner"]["team_name"] == "8Bit"
    assert state["stats"]["finishes"] == 14

    expected = ["stats_rundown", "interview", "standings", "done"]
    for step in expected:
        state = await ceremony.advance()
        assert state["step"] == step

    # winner payloads survive the whole ceremony
    assert state["winner"]["team_name"] == "8Bit"
    assert state["interview"]["player"] == "Sarang"


async def test_ceremony_start_without_winner_rejected(ceremony: CeremonyService):
    with pytest.raises(InvalidCeremonyTransitionError):
        await ceremony.start()
    assert ceremony.snapshot()["step"] == "idle"


async def test_ceremony_advance_before_start_rejected(ceremony: CeremonyService):
    with pytest.raises(InvalidCeremonyTransitionError):
        await ceremony.advance()


async def test_ceremony_advance_past_done_rejected(ceremony: CeremonyService):
    await ceremony.start(team_name="8Bit")
    for _ in range(4):
        await ceremony.advance()
    assert ceremony.snapshot()["step"] == "done"
    with pytest.raises(InvalidCeremonyTransitionError):
        await ceremony.advance()


async def test_ceremony_double_start_rejected(ceremony: CeremonyService):
    await ceremony.start(team_name="8Bit")
    with pytest.raises(InvalidCeremonyTransitionError):
        await ceremony.start(team_name="K9")


async def test_ceremony_reset_clears_everything(ceremony: CeremonyService):
    await ceremony.start(
        winner_team_id="x", team_name="8Bit", stats={"finishes": 9}
    )
    await ceremony.advance()
    state = await ceremony.reset()
    assert state["step"] == "idle"
    assert state["winner"]["team_name"] is None
    assert state["winner"]["team_id"] is None
    assert state["stats"] is None
    assert state["interview"] is None
    # can start again after reset
    state = await ceremony.start(team_name="K9")
    assert state["step"] == "winner_announce"
    assert state["winner"]["team_name"] == "K9"


async def test_ceremony_honest_null_stats(ceremony: CeremonyService):
    """Starting with only an identity never invents stats/interview."""
    state = await ceremony.start(team_name="8Bit")
    assert state["stats"] is None
    assert state["interview"] is None


async def test_ceremony_ws_message_shape(
    ceremony: CeremonyService, ceremony_messages: list
):
    ceremony.add_listener(_listener_into(ceremony_messages))
    await ceremony.start(team_name="8Bit")
    await ceremony.advance()
    assert [m["type"] for m in ceremony_messages] == [
        CEREMONY_WS_TYPE,
        CEREMONY_WS_TYPE,
    ]
    assert CEREMONY_WS_TYPE == "ceremony.step"
    assert ceremony_messages[-1]["data"]["step"] == "stats_rundown"
    assert CEREMONY_STEPS == (
        "idle",
        "winner_announce",
        "stats_rundown",
        "interview",
        "standings",
        "done",
    )


# ══════════════════════════════════════════════════════════════════
# C. Match point / smash rule
# ══════════════════════════════════════════════════════════════════


@pytest.fixture()
def match_point() -> MatchPointService:
    return MatchPointService()


@pytest.fixture()
def match_point_messages() -> list:
    return []


def _standings() -> list:
    return [
        {"team_id": "t1", "team_name": "Alpha", "total_points": 52.0},
        {"team_id": "t2", "team_name": "Bravo", "total_points": 40.0},
        {"team_id": "t3", "team_name": "Charlie", "total_points": 39.5},
        {"team_id": "t4", "team_name": "Delta", "total_points": 0.0},
    ]


async def test_match_point_threshold_boundary(match_point: MatchPointService):
    """Default threshold is 40: at/above qualifies, below does not."""
    state = await match_point.evaluate(_standings())
    names = [t["team_name"] for t in state["last_evaluation"]["on_match_point"]]
    assert names == ["Alpha", "Bravo"]  # 52 and 40; 39.5 and 0 excluded
    for entry in state["last_evaluation"]["on_match_point"]:
        assert "reason" in entry and entry["reason"]


async def test_match_point_no_one_qualifies(match_point: MatchPointService):
    state = await match_point.evaluate(
        [{"team_id": "t9", "team_name": "Zed", "total_points": 5.0}]
    )
    assert state["last_evaluation"]["on_match_point"] == []
    assert "No team" in state["last_evaluation"]["note"]


async def test_match_point_disabled(match_point: MatchPointService):
    await match_point.set_config(enabled=False)
    state = await match_point.evaluate(_standings())
    assert state["last_evaluation"]["on_match_point"] == []
    assert "disabled" in state["last_evaluation"]["note"].lower()


async def test_match_point_config_reconfigures_threshold(
    match_point: MatchPointService,
):
    await match_point.set_config(points_threshold=50)
    state = await match_point.evaluate(_standings())
    names = [t["team_name"] for t in state["last_evaluation"]["on_match_point"]]
    assert names == ["Alpha"]


async def test_match_point_final_day_chicken_gates_on_day(
    match_point: MatchPointService,
):
    await match_point.set_config(
        mode="final_day_chicken", points_threshold=40, final_day_number=3
    )
    # day 1: nobody, with an honest activation note
    state = await match_point.evaluate(_standings(), day=1)
    assert state["last_evaluation"]["on_match_point"] == []
    assert "day 3" in state["last_evaluation"]["note"]
    # day 3: threshold teams qualify with the smash reason
    state = await match_point.evaluate(_standings(), day=3)
    names = [t["team_name"] for t in state["last_evaluation"]["on_match_point"]]
    assert names == ["Alpha", "Bravo"]
    assert all(
        "championship" in t["reason"].lower()
        for t in state["last_evaluation"]["on_match_point"]
    )


async def test_match_point_day_defaults_to_configured_current_day(
    match_point: MatchPointService,
):
    await match_point.set_config(
        mode="final_day_chicken", final_day_number=3, current_day=3
    )
    state = await match_point.evaluate(_standings())
    assert state["last_evaluation"]["day"] == 3
    assert len(state["last_evaluation"]["on_match_point"]) == 2


async def test_match_point_invalid_config_rejected(match_point: MatchPointService):
    with pytest.raises(InvalidMatchPointConfigError):
        await match_point.set_config(mode="first_to_five_kills")
    with pytest.raises(InvalidMatchPointConfigError):
        await match_point.set_config(points_threshold=0)
    with pytest.raises(InvalidMatchPointConfigError):
        await match_point.set_config(points_threshold=-10)
    with pytest.raises(InvalidMatchPointConfigError):
        await match_point.set_config(nonsense_key=True)
    with pytest.raises(InvalidMatchPointConfigError):
        await match_point.set_config(enabled="yes")
    # config untouched by the rejections
    assert match_point.get_config()["mode"] == "points_threshold"


async def test_match_point_sorted_descending(match_point: MatchPointService):
    state = await match_point.evaluate(_standings())
    points = [
        t["total_points"] for t in state["last_evaluation"]["on_match_point"]
    ]
    assert points == sorted(points, reverse=True)


def test_match_point_initial_status_honest(match_point: MatchPointService):
    """Before any evaluation the state is honestly unevaluated."""
    state = match_point.snapshot()
    assert state["last_evaluation"] is None
    assert state["config"]["enabled"] is True


async def test_match_point_ws_message_shape(
    match_point: MatchPointService, match_point_messages: list
):
    match_point.add_listener(_listener_into(match_point_messages))
    await match_point.evaluate(_standings())
    assert len(match_point_messages) == 1
    assert match_point_messages[0]["type"] == "matchpoint.state"
    assert "config" in match_point_messages[0]["data"]
    assert "last_evaluation" in match_point_messages[0]["data"]


# ══════════════════════════════════════════════════════════════════
# D. Zone timer
# ══════════════════════════════════════════════════════════════════


@pytest.fixture()
def zone() -> ZoneService:
    return ZoneService()


@pytest.fixture()
def zone_messages() -> list:
    return []


def _phases() -> list:
    return [
        {"name": "Zone 1", "duration_seconds": 90},
        {"name": "Zone 2", "duration_seconds": 60},
    ]


def test_zone_initial_state_is_honest_no_data(zone: ZoneService):
    state = zone.snapshot()
    assert state["status"] == "no_data"
    assert state["current_phase"] is None
    assert state["time_remaining_seconds"] is None


async def test_zone_set_phases_starts_phase_one(zone: ZoneService):
    state = await zone.set_phases(_phases())
    assert state["status"] == "active"
    assert state["current_phase"]["name"] == "Zone 1"
    assert state["current_phase"]["phase_number"] == 1
    assert state["current_phase"]["total_phases"] == 2
    remaining = state["time_remaining_seconds"]
    assert remaining is not None and 0 < remaining <= 90


async def test_zone_remaining_computed_from_started_at(zone: ZoneService):
    """remaining ~= duration - elapsed: the clock is computed, not faked."""
    await zone.set_phases([{"name": "Zone 1", "duration_seconds": 90}])
    before = time.monotonic()
    remaining = zone.time_remaining()
    after = time.monotonic()
    assert remaining is not None
    assert 90 - (after - before) - 1.0 <= remaining <= 90


async def test_zone_remaining_clamps_at_zero_not_negative(zone: ZoneService):
    zone._phase_started_at = time.monotonic() - 10_000  # long overrun
    zone._phases = [{"name": "Zone 1", "duration_seconds": 90}]
    zone._current_index = 0
    assert zone.time_remaining() == 0.0


async def test_zone_advance_walks_to_complete(zone: ZoneService):
    await zone.set_phases(_phases())
    state = await zone.advance()
    assert state["status"] == "active"
    assert state["current_phase"]["name"] == "Zone 2"
    state = await zone.advance()
    assert state["status"] == "complete"
    assert state["current_phase"] is None
    assert state["time_remaining_seconds"] is None


async def test_zone_advance_past_complete_rejected(zone: ZoneService):
    await zone.set_phases(_phases())
    await zone.advance()
    await zone.advance()
    with pytest.raises(InvalidZoneStateError):
        await zone.advance()


async def test_zone_advance_with_no_data_rejected(zone: ZoneService):
    with pytest.raises(InvalidZoneStateError):
        await zone.advance()


async def test_zone_reset_with_no_data_rejected(zone: ZoneService):
    with pytest.raises(InvalidZoneStateError):
        await zone.reset()


async def test_zone_reset_restarts_phase_one(zone: ZoneService):
    await zone.set_phases(_phases())
    await zone.advance()
    state = await zone.reset()
    assert state["status"] == "active"
    assert state["current_phase"]["name"] == "Zone 1"
    assert state["time_remaining_seconds"] is not None


async def test_zone_set_phases_replaces_schedule(zone: ZoneService):
    await zone.set_phases(_phases())
    await zone.advance()
    state = await zone.set_phases([{"name": "Zone A", "duration_seconds": 30}])
    assert state["current_phase"]["name"] == "Zone A"
    assert state["current_phase"]["total_phases"] == 1


async def test_zone_invalid_phases_rejected(zone: ZoneService):
    with pytest.raises(InvalidZoneStateError):
        await zone.set_phases([])
    with pytest.raises(InvalidZoneStateError):
        await zone.set_phases([{"name": "", "duration_seconds": 30}])
    with pytest.raises(InvalidZoneStateError):
        await zone.set_phases([{"name": "Zone 1", "duration_seconds": 0}])
    with pytest.raises(InvalidZoneStateError):
        await zone.set_phases([{"name": "Zone 1", "duration_seconds": -5}])
    with pytest.raises(InvalidZoneStateError):
        await zone.set_phases([{"name": "Zone 1"}])
    # untouched: still honestly no_data
    assert zone.snapshot()["status"] == "no_data"


async def test_zone_ws_message_shape(zone: ZoneService, zone_messages: list):
    zone.add_listener(_listener_into(zone_messages))
    await zone.set_phases(_phases())
    await zone.advance()
    assert [m["type"] for m in zone_messages] == [ZONE_WS_TYPE, ZONE_WS_TYPE]
    assert ZONE_WS_TYPE == "zone.tick"
    assert zone_messages[-1]["data"]["current_phase"]["name"] == "Zone 2"
