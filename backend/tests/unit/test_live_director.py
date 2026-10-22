# backend/tests/unit/test_live_director.py
"""Phase 25 (W1, P0-0) — Live Director proof tests.

These drive the REAL path, exactly as the phase brief requires:

  synthetic NormalizedEvents (knock for team B) + a fight promotion
  (fight.detected, the canonical topic FightDetector publishes) go onto
  a REAL EventBus instance (real in-process dispatch; only the Redis
  transport is mocked, the same pattern as tests/conftest.py) with the
  LiveDirector + a real DirectorService attached — no DEMO_MODE, no
  simulator — and the assertions check BOTH the decision log (score
  breakdowns, margins, deciding rule) AND that
  ``director_service.snapshot()["active_camera"]`` actually changed to
  team B's observer slot.

Plus: empty stream -> zero decisions; event inside the phase min-hold
timer -> hold logged, no switch, simultaneous fight handed to
``replay.candidate`` (never PiP); challenger below the absolute margin
-> rejected; final-circle lock; semi_auto suggestion flow; manual mode
silence; unknown teams dropped + counted; intel_alert first consumer
(preview/pre-positioning without a blind cut); PVW preview shape; the
decision-log API surface.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from app.core.event_bus import EventBus, EventPriority
from app.services.director_service import DirectorService
from app.services.feed_adapter.schemas import FeedEventType, NormalizedEvent
from app.services.live_director import LiveDirector, set_live_director
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
    """A real EventBus with only the Redis transport mocked (conftest
    pattern): publish/subscribe/dispatch/alias resolution are the real
    code paths."""
    bus = EventBus()
    bus._redis = AsyncMock()
    bus._redis.xadd = AsyncMock(return_value="1-0")
    bus._pubsub_redis = AsyncMock()
    bus._pubsub_redis.publish = AsyncMock(return_value=1)
    bus._consumer_redis = AsyncMock()
    bus._running = True
    return bus


class Rig:
    """LiveDirector + DirectorService + real bus, team A on PGM (slot 1)."""

    def __init__(self, phase: str = "mid_game"):
        self.clock = FakeClock()
        self.phase = phase
        self.bus = _real_bus()
        self.director_service = DirectorService()
        # Phase 28: fresh default is semi_auto; this suite tests the
        # full_auto firing path, so pin it explicitly (sync rig —
        # set_mode is async; same _state pattern as
        # test_suggestion_camera_integration).
        self.director_service._state["mode"] = "full_auto"
        self.live = LiveDirector(
            bus=self.bus,
            director_service=self.director_service,
            slot_resolver=StaticSlotResolver(SLOTS),
            phase_provider=lambda: self.phase,
            clock=self.clock,
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

    async def fight(
        self, fight_id: str, teams: List[str], intensity: float, **extra
    ) -> None:
        await self.publish(
            "fight.detected",
            {
                "fight_id": fight_id,
                "teams_involved": teams,
                "intensity": intensity,
                "fight_type": "mid_range",
                "location": {"x": 10.0, "y": 10.0},
                "match_id": "m-1",
                **extra,
            },
        )


@pytest.fixture
def rig() -> Rig:
    return Rig()


# ── THE proof: real path kill/knock + fight promotion -> camera switch ──


async def test_real_path_fight_promotion_switches_active_camera(rig: Rig):
    # Team A is on PGM: DirectorService default active_camera == 1.
    assert rig.director_service.snapshot()["active_camera"] == 1

    # 1) A real NormalizedEvent knock for team B (feed-adapter shape).
    knock = NormalizedEvent(
        event_type=FeedEventType.KNOCK,
        player="BPlayer",
        team="Team B",
        victim="CPlayer",
        victim_team="Team C",
        weapon="M416",
        match_id="m-1",
    )
    await rig.publish("knock_event", knock.to_bus_payload())
    # A lone knock is heat, not a cut: incumbent base still wins.
    assert rig.director_service.snapshot()["active_camera"] == 1

    # 2) FightDetector promotion for team B (canonical fight.detected).
    await rig.fight("fight-b", ["team-b", "team-c"], 85.0)

    snap = rig.director_service.snapshot()
    assert snap["active_camera"] == 2, "camera must move to team B's slot"
    assert snap["scene"] == "main"

    # Decision log: a switch entry with the full score breakdown.
    switches = [e for e in rig.live.decision_log() if e["outcome"] == "switch"]
    assert len(switches) == 1
    entry = switches[0]
    assert entry["rule"] == "margin_default_15"
    assert entry["margin_actual"] >= entry["margin_required"]
    by_team = {c["team_id"]: c for c in entry["candidates"]}
    assert by_team["team-b"]["slot"] == 2
    assert by_team["team-b"]["breakdown"]["active_combat"] == 85.0
    assert entry["incumbent"]["team_id"] == "team-a"
    assert entry["challenger"]["team_id"] == "team-b"

    # PVW preview carries the same stable shape W4 builds on.
    preview = snap["preview"]
    assert preview["slot"] == 2
    assert preview["team_id"] == "team-b"
    assert preview["scene"] == "main"
    assert preview["breakdown"]["active_combat"] == 85.0
    assert preview["score"] == 85.0


async def test_empty_stream_zero_decisions(rig: Rig):
    assert rig.live.decision_log() == []
    assert rig.live.status()["stats"]["decisions"] == 0
    assert rig.live.status()["stats"]["switches"] == 0
    # Even an explicit evaluation with no signals invents nothing.
    assert await rig.live.evaluate_now() is None
    assert rig.live.decision_log() == []
    snap = rig.director_service.snapshot()
    assert snap["active_camera"] == 1
    assert snap["preview"] is None


async def test_event_inside_min_hold_is_held_and_handed_to_replay(rig: Rig):
    replay_candidates: List[Dict[str, Any]] = []

    async def _collect(record):
        replay_candidates.append(record.event.data)

    rig.bus.subscribe(
        subscriber_id="test_replay_collector",
        callback=_collect,
        event_types=["replay.candidate"],
    )

    # Fight 1 involves the on-air team A: PGM is committed to it.
    await rig.fight("fight-1", ["team-a", "team-c"], 80.0)
    assert rig.director_service.snapshot()["active_camera"] == 1

    # 10 s later (mid_game min hold = 45 s): a hotter second fight.
    rig.clock.advance(10)
    await rig.fight("fight-2", ["team-b", "team-d"], 90.0)

    # Grammar: no cut inside the hold timer without a 40-point margin,
    # and NEVER a PiP box — fight-2 goes to the replay queue instead.
    assert rig.director_service.snapshot()["active_camera"] == 1
    held = [
        e
        for e in rig.live.decision_log()
        if e["outcome"] == "rejected_challenger"
        and e["rule"] == "margin_fight_hold_40"
    ]
    assert held, rig.live.decision_log()
    assert held[-1]["margin_required"] == 40.0
    assert len(replay_candidates) == 1
    assert replay_candidates[0]["fight_id"] == "fight-2"
    assert set(replay_candidates[0]["teams"]) == {"team-b", "team-d"}
    assert "window" in replay_candidates[0]


async def test_challenger_below_margin_rejected(rig: Rig):
    # Intensity 60 vs on-air base 50: margin 10 < default 15.
    await rig.fight("fight-weak", ["team-b", "team-c"], 60.0)
    assert rig.director_service.snapshot()["active_camera"] == 1
    entries = rig.live.decision_log()
    assert entries[-1]["outcome"] == "rejected_challenger"
    assert entries[-1]["rule"] == "margin_default_15"
    assert entries[-1]["margin_actual"] == 10.0
    # ...but the preview still pre-positions the challenger (PVW).
    preview = rig.director_service.snapshot()["preview"]
    assert preview["slot"] == 2
    assert preview["score"] == 60.0


async def test_final_circle_lock_until_fight_resolves():
    rig = Rig(phase="final_circle")
    await rig.fight("fight-1", ["team-a", "team-c"], 80.0)
    rig.clock.advance(10)
    # Even a clutch challenger at intensity 100 cannot break the lock.
    await rig.fight("fight-2", ["team-b", "team-d"], 100.0, is_clutch=True)
    assert rig.director_service.snapshot()["active_camera"] == 1
    rules = [e["rule"] for e in rig.live.decision_log()]
    assert "final_circle_lock_until_fight_resolves" in rules


async def test_semi_auto_proposes_suggestion_never_writes(rig: Rig):
    ds = rig.director_service
    await ds.set_mode("manual")
    await ds.set_scene("standings")
    await ds.set_mode("semi_auto")

    await rig.fight("fight-b", ["team-b", "team-c"], 85.0)

    # No direct write in semi_auto...
    snap = ds.snapshot()
    assert snap["active_camera"] == 1
    assert snap["scene"] == "standings"
    # ...only a suggestion in the existing 60 s approval flow, naming
    # the target slot/team in its reason.
    suggestions = ds.list_suggestions()
    assert len(suggestions) == 1
    # Phase 25 integration: the suggestion payload now also
    # carries the target camera + team, so approving it applies
    # scene AND camera as one write (previously camera-less).
    assert suggestions[0]["payload"] == {
        "scene": "main",
        "active_camera": 2,
        "team_id": "team-b",
    }
    assert "slot 2" in suggestions[0]["reason"]
    assert "team-b" in suggestions[0]["reason"]
    entries = rig.live.decision_log()
    assert entries[-1]["outcome"] == "suggestion"
    # Approval applies the scene through the existing flow.
    await ds.approve_suggestion(suggestions[0]["id"], updated_by="op")
    assert ds.snapshot()["scene"] == "main"


async def test_manual_mode_never_writes(rig: Rig):
    ds = rig.director_service
    await ds.set_mode("manual")
    await rig.fight("fight-b", ["team-b", "team-c"], 95.0)
    snap = ds.snapshot()
    assert snap["active_camera"] == 1
    assert snap["preview"] is None  # manual: not even the preview moves
    assert ds.list_suggestions() == []
    entries = rig.live.decision_log()
    assert entries[-1]["outcome"] == "hold"
    assert entries[-1]["rule"] == "manual_mode_no_write"


async def test_unknown_team_dropped_and_counted(rig: Rig):
    await rig.fight("fight-x", ["team-x", "team-y"], 95.0)
    assert rig.live.status()["stats"]["dropped_unknown_team"] == 2
    assert rig.live.decision_log() == []
    assert rig.director_service.snapshot()["active_camera"] == 1


async def test_intel_alert_prepositions_without_blind_cut(rig: Rig):
    # Phase 24 intel_alert, first consumer: third-party anticipation
    # updates PVW + candidate scores, but intel alone cannot cut past
    # the on-air incumbent.
    await rig.publish(
        "intel_alert",
        {
            "kind": "third_party",
            "teams": ["team-b", "team-c", "team-d"],
            "killer_teams": ["team-b", "team-c", "team-d"],
            "timestamp": rig.clock(),
        },
    )
    snap = rig.director_service.snapshot()
    assert snap["active_camera"] == 1
    preview = snap["preview"]
    assert preview is not None
    assert preview["breakdown"]["precombat"] == 20.0
    entries = rig.live.decision_log()
    assert entries[-1]["outcome"] == "rejected_challenger"
    by_team = {c["team_id"]: c for c in entries[-1]["candidates"]}
    assert by_team["team-b"]["breakdown"]["precombat"] == 20.0


async def test_stale_incumbent_uses_margin_10(rig: Rig):
    # A signalless incumbent goes stale after 30 s (non-fight): the
    # stale margin (10) then governs. Intensity 62 - base decayed
    # (50 - (40-30)*0.5 = 45) = 17 >= 10 -> switch.
    rig.clock.advance(40)
    await rig.fight("fight-b", ["team-b", "team-c"], 62.0)
    assert rig.director_service.snapshot()["active_camera"] == 2
    switches = [e for e in rig.live.decision_log() if e["outcome"] == "switch"]
    assert switches[-1]["rule"] == "margin_stale_10"


async def test_decision_log_api_surface(rig: Rig):
    from app.api.v1.director.router import get_director_decision_log

    await rig.fight("fight-b", ["team-b", "team-c"], 85.0)
    set_live_director(rig.live)
    try:
        payload = await get_director_decision_log(_user=object(), limit=10)
    finally:
        set_live_director(None)
    assert payload["running"] is True
    assert payload["decisions"]
    assert payload["decisions"][-1]["outcome"] == "switch"
    assert payload["status"]["stats"]["switches"] == 1

    payload = await get_director_decision_log(_user=object(), limit=10)
    assert payload["running"] is False
    assert payload["decisions"] == []
