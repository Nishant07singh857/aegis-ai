# backend/tests/unit/test_phase31_hooks.py
"""Phase 31 (coordinator integration) — service.py hooks only.

Worker modules (rotation_intake, payoff, zone_beat, lull,
center_hold, storyline gap) are unit-tested by their own suites
(99 tests). These tests prove the HOOKS: the live director
actually consumes them end to end (bus -> director -> decision
log / program state), and that the grammar guards hold:
zone beats never cut a fight, lull never auto-cuts, the clutch
lock needs the full hold margin, gap uplift stays inside the W
cap, and winner preference is a tie-break only (scores untouched).

No commit/push scope: tests only.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from app.core.event_bus import EventBus, EventPriority
from app.services.director_service import DirectorService
from app.services.live_director import LiveDirector
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
    def __init__(self, mode: str = "full_auto", phase: str = "mid_game",
                 standings: Any = None):
        self.clock = FakeClock()
        self.phase = phase
        self.bus = _real_bus()
        self.ds = DirectorService()
        self.ds._state["mode"] = mode
        self.live = LiveDirector(
            bus=self.bus,
            director_service=self.ds,
            slot_resolver=StaticSlotResolver(SLOTS),
            phase_provider=lambda: self.phase,
            clock=self.clock,
            standings_provider=(lambda: standings) if standings is not None else None,
        )
        self.live.start()

    async def publish(self, event_type: str, data: Dict[str, Any]) -> None:
        await self.bus.publish(
            event_type=event_type, data=data,
            priority=EventPriority.HIGH, source="test", match_id="m-1",
        )

    async def fight(self, fight_id: str, teams: List[str],
                    intensity: float, **extra) -> None:
        await self.publish("fight.detected", {
            "fight_id": fight_id, "teams_involved": teams,
            "intensity": intensity, "fight_type": "mid_range",
            "location": {"x": 10.0, "y": 10.0}, "match_id": "m-1",
            **extra,
        })

    async def intel(self, kind: str, **data) -> None:
        await self.publish("intel_alert", {"kind": kind, **data})

    def log_rules(self) -> List[str]:
        return [e.get("rule") for e in self.live.decision_log(limit=200)]


def _standings(gap: float) -> List[Dict[str, Any]]:
    # rank1 50 pts, rank2 50-gap pts, rank3 far behind.
    return [
        {"team_id": "team-a", "rank": 1, "total_points": 50},
        {"team_id": "team-b", "rank": 2, "total_points": 50 - gap},
        {"team_id": "team-c", "rank": 3, "total_points": 10},
    ]


# ── Item 3: championship gap ──────────────────────────────────────

def test_gap_uplift_applies_inside_cap_and_zero_base_stays_zero() -> None:
    rig = Rig(standings=_standings(3))
    rig.live._refresh_storyline_context(1000.0)
    assert rig.live._story_championship_gap == 3
    rig.live._fights["f1"] = {
        "fight_id": "f1", "teams": ["team-b", "team-c"],
        "intensity": 50.0, "is_clutch": False,
        "started_at": 1000.0, "last_activity_at": 1000.0,
    }
    scored = rig.live._score_team("team-b", 1000.0)
    factors = {f["factor"]: f["uplift"]
               for f in scored["storyline"]["factors"]}
    assert factors.get("championship_close_gap") == 0.05
    # rank2 uplift 0.06 + gap 0.05 -> W 1.11, never 2x, never over cap.
    assert scored["storyline"]["multiplier"] == 1.11
    assert scored["score"] == 55.5
    # A signalless team keeps score 0 no matter what W says.
    assert rig.live._score_team("team-d", 1000.0)["score"] == 0.0


def test_gap_above_five_gives_no_uplift() -> None:
    rig = Rig(standings=_standings(6))
    rig.live._refresh_storyline_context(1000.0)
    assert rig.live._story_championship_gap == 6
    rig.live._fights["f1"] = {
        "fight_id": "f1", "teams": ["team-b", "team-c"],
        "intensity": 50.0, "is_clutch": False,
        "started_at": 1000.0, "last_activity_at": 1000.0,
    }
    scored = rig.live._score_team("team-b", 1000.0)
    factors = {f["factor"] for f in scored["storyline"]["factors"]}
    assert "championship_close_gap" not in factors
    assert scored["storyline"]["multiplier"] == 1.06


# ── Item 4: zone beat ─────────────────────────────────────────────

async def test_zone_beat_full_auto_map_and_hold_then_expiry() -> None:
    rig = Rig()
    before = rig.ds.snapshot()["active_camera"]
    await rig.intel("zone_pull", winners=["team-a"], losers=["team-b"],
                    phase="mid_game", timestamp=1000.0)
    snap = rig.ds.snapshot()
    assert snap["scene"] == "map_cam"
    assert snap["active_camera"] == before  # scene only, no team cut
    assert rig.live._pending_zone_beat is not None
    assert rig.live._pending_zone_beat["duration_s"] == 15.0
    # Still inside the beat: evaluation holds, PGM stays on map.
    rig.clock.advance(5)
    assert await rig.live.evaluate_now() is None
    assert rig.live._pending_zone_beat is not None
    # After 15s the beat clears and normal evaluation resumes: the
    # precombat challenger is rejected on margin, so NO cut follows.
    rig.clock.advance(11)
    entry = await rig.live.evaluate_now()
    assert rig.live._pending_zone_beat is None
    assert entry is not None and entry["outcome"] == "rejected_challenger"
    assert rig.ds.snapshot()["scene"] == "map_cam"
    assert rig.ds.snapshot()["active_camera"] == before


async def test_zone_beat_fight_means_graphic_only_no_cut() -> None:
    rig = Rig()
    await rig.fight("f1", ["team-b", "team-c"], 80.0)
    pgm_before = rig.ds.snapshot()["active_camera"]
    scene_before = rig.ds.snapshot()["scene"]
    await rig.intel("zone_pull", winners=["team-a"], losers=["team-b"],
                    phase="mid_game", timestamp=1000.0)
    snap = rig.ds.snapshot()
    assert snap["scene"] == scene_before
    assert snap["active_camera"] == pgm_before
    assert rig.live._pending_zone_beat is None
    assert "zone_reveal_graphic_only_no_cut" in rig.log_rules()


async def test_zone_beat_aborted_by_hot_fight() -> None:
    rig = Rig()
    await rig.intel("zone_pull", winners=["team-a"], losers=["team-b"],
                    phase="mid_game", timestamp=1000.0)
    assert rig.live._pending_zone_beat is not None
    await rig.fight("f1", ["team-b", "team-c"], 80.0)
    assert rig.live._pending_zone_beat is None
    assert "zone_beat_aborted_hot_fight" in rig.log_rules()


async def test_zone_pull_terms_differentiated() -> None:
    rig = Rig()
    await rig.intel("zone_pull", winners=["team-a"], losers=["team-b"],
                    phase="mid_game", timestamp=1000.0)
    assert "zone_pull" in rig.live._intel_zone_pressure.get("team-a", {})
    assert "zone_pull" in rig.live._intel_precombat.get("team-b", {})
    # Losers no longer get the (old, flattened) zone_pressure term.
    assert "zone_pull" not in rig.live._intel_zone_pressure.get("team-b", {})


# ── Item 8 (consumer): center hold ────────────────────────────────

async def test_center_hold_consumer_zone_pressure_and_suggestion() -> None:
    rig = Rig(mode="semi_auto")
    await rig.intel(
        "center_hold", team_id="team-b",
        suggestion={"reason": "Center hold: team-b stationary on compound."},
    )
    assert rig.live._intel_zone_pressure["team-b"]["center_hold"] == 10.0
    sugg = [s for s in rig.ds.list_suggestions()
            if s["payload"].get("team_id") == "team-b"]
    assert sugg, "center-hold semi_auto suggestion missing"
    assert sugg[0]["payload"]["scene"] == "main"


# ── Item 2: clutch lock ───────────────────────────────────────────

async def test_clutch_incumbent_lock_needs_full_hold_margin() -> None:
    rig = Rig()
    rig.ds._state["active_camera"] = 1
    rig.live._last_known_slot = 1
    rig.live._scene_started_at = 900.0  # age 100: outside hold window
    rig.live._fights["f1"] = {
        "fight_id": "f1", "teams": ["team-a", "team-b"],
        "intensity": 50.0, "is_clutch": True,
        "started_at": 900.0, "last_activity_at": 1000.0,
    }
    rig.live._current_fight_id = "f1"
    # Challenger scores 90 vs incumbent ~70: margin 20 beats the
    # clutch-challenger 5 and the default 15, but NOT the lock's 40.
    rig.live._fights["f2"] = {
        "fight_id": "f2", "teams": ["team-c", "team-d"],
        "intensity": 90.0, "is_clutch": False,
        "started_at": 1000.0, "last_activity_at": 1000.0,
    }
    entry = await rig.live.evaluate_now()
    assert entry is not None
    assert entry["rule"] == "margin_clutch_incumbent_lock_40"
    assert entry["outcome"] == "rejected_challenger"
    assert rig.ds.snapshot()["active_camera"] == 1


# ── Item 5: payoff preference (tie-break only) ─────────────────────

async def test_payoff_preference_breaks_first_place_tie_only() -> None:
    rig = Rig()
    rig.ds._state["active_camera"] = 4
    rig.live._last_known_slot = 4
    rig.live._scene_started_at = 900.0  # incumbent stale: 50 - 35 = 15
    rig.live._eliminated_teams = {"team-a"}
    rig.live._fights["f1"] = {
        "fight_id": "f1", "teams": ["team-a", "team-c"],
        "intensity": 40.0, "is_clutch": False,
        "started_at": 900.0, "last_activity_at": 970.0,  # 30s idle
    }
    # Tied challengers on heat alone (50 each); team-a sorts first
    # by team_id, so without the preference the switch goes to slot 1.
    rig.live._combat_heat["team-a"] = [(1000.0, 50.0)]
    rig.live._combat_heat["team-c"] = [(1000.0, 50.0)]
    entry = await rig.live.evaluate_now()
    assert entry is not None
    assert entry["outcome"] == "switch"
    assert rig.ds.snapshot()["active_camera"] == 3  # team-c wins the tie
    assert rig.live._payoff_preference is None  # one-shot: consumed


async def test_payoff_no_wipe_observation_no_preference() -> None:
    rig = Rig()
    rig.ds._state["active_camera"] = 4
    rig.live._last_known_slot = 4
    rig.live._scene_started_at = 900.0
    rig.live._fights["f1"] = {
        "fight_id": "f1", "teams": ["team-a", "team-c"],
        "intensity": 40.0, "is_clutch": False,
        "started_at": 900.0, "last_activity_at": 970.0,
    }
    rig.live._combat_heat["team-a"] = [(1000.0, 50.0)]
    rig.live._combat_heat["team-c"] = [(1000.0, 50.0)]
    entry = await rig.live.evaluate_now()
    assert entry is not None
    assert entry["outcome"] == "switch"
    assert rig.ds.snapshot()["active_camera"] == 1  # team-a, id order


# ── Item 5: elimination banner ────────────────────────────────────

async def test_elimination_banner_semi_auto_suggestion() -> None:
    rig = Rig(mode="semi_auto")
    await rig.publish("team_wipe_event", {"team_id": "team-b"})
    assert "team-b" in rig.live._eliminated_teams
    sugg = [s for s in rig.ds.list_suggestions()
            if s["type"] == "lower_third"]
    assert sugg, "elimination banner suggestion missing"
    assert sugg[0]["payload"]["title"] == "ELIMINATED"
    assert "Team B ELIMINATED" in sugg[0]["payload"]["subtitle"]
    assert "elimination_banner" in [
        e.get("outcome") for e in rig.live.decision_log(limit=200)]


# ── Item 7: lull loop ─────────────────────────────────────────────

async def test_lull_semi_auto_suggests_never_cuts() -> None:
    rig = Rig(mode="semi_auto", phase="loot")
    before = rig.ds.snapshot()
    entry = await rig.live.evaluate_now()
    assert entry is not None and entry["outcome"] == "lull_suggestion"
    assert rig.ds.snapshot()["scene"] == before["scene"]
    assert rig.ds.snapshot()["active_camera"] == before["active_camera"]
    sugg = rig.ds.list_suggestions()
    assert sugg and sugg[0]["payload"]["scene"] == "map_cam"
    # Leg in progress: no second suggestion until the leg ends.
    assert await rig.live.evaluate_now() is None
    rig.clock.advance(21)
    entry2 = await rig.live.evaluate_now()
    assert entry2 is not None and entry2["outcome"] == "lull_suggestion"
    assert entry2["reason"].startswith("Lull loop")


async def test_lull_full_auto_silent() -> None:
    rig = Rig(mode="full_auto", phase="loot")
    assert await rig.live.evaluate_now() is None
    assert rig.ds.list_suggestions() == []


async def test_lull_aborts_when_fight_starts() -> None:
    rig = Rig(mode="semi_auto", phase="loot")
    await rig.live.evaluate_now()  # map leg suggestion queued
    rig.clock.advance(21)
    await rig.fight("f1", ["team-b", "team-c"], 80.0)
    # The fight decision path runs; no new lull suggestion is made.
    rules = rig.log_rules()
    assert "lull_loop_15_30s" not in rules[-1:]
