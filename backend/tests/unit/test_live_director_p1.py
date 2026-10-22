# backend/tests/unit/test_live_director_p1.py
"""Phase 25 (W5, P1) — Spec Gold, spectator version.

Covers the four P1 builds on top of W1's live director:

  * Build 1 — storyline multiplier W: leader with equal fight signal
    beats non-leader; storyline alone (base 0) never switches; missing
    standings -> multiplier exactly 1.0 with empty factors in the log.
  * Build 2 — PRE_COMBAT engagement state: convergence -> PRE_COMBAT
    + PVW pre-positioned + NO PGM cut; promotion -> ACTIVE_COMBAT
    (clutch -> CLUTCH); signal silence -> honest decay to baseline.
  * Build 3 — drop clash: two teams same POI -> intel_alert emitted +
    director watch raised; single-team POI -> none; no flight path ->
    none.
  * Build 4 — context-first beat: anticipation cut takes the beat,
    hot cut skips it, final circle skips it, missing establishing
    view skips it.

Same rig pattern as tests/unit/test_live_director.py (W1): real
EventBus (Redis transport mocked), real DirectorService, fake clock.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from app.core.event_bus import EventBus, EventPriority
from app.services.director_service import DirectorService
from app.services.live_director import LiveDirector
from app.services.live_director.config import LiveDirectorConfig
from app.services.live_director.engagement import (
    EngagementState,
    EngagementTracker,
)
from app.services.live_director.storyline import compute_storyline
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
    """LiveDirector + DirectorService + real bus, team A on PGM."""

    def __init__(self, phase: str = "mid_game", **live_kwargs):
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

    async def kill(self, killer_team: str = "Team B",
                   victim_team: str = "Team C") -> None:
        await self.publish(
            "kill_event",
            {
                "killer": "Shooter",
                "killerTeam": killer_team,
                "victim": "Target",
                "victimTeam": victim_team,
                "weapon": "M416",
                "match_id": "m-1",
            },
        )


def _candidates(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {c["team_id"]: c for c in entry["candidates"]}


# ── Build 1: storyline multiplier W ──────────────────────────────


async def test_storyline_leader_beats_nonleader_at_equal_signal():
    # Non-leader (rank 6 -> W 1.0) at intensity 62: margin 12 < 15.
    rig_c = Rig(standings_provider=lambda: list(STANDINGS))
    await rig_c.fight("fight-c", ["team-c", "team-d"], 62.0)
    assert rig_c.director_service.snapshot()["active_camera"] == 1
    entry_c = rig_c.live.decision_log()[-1]
    assert entry_c["outcome"] == "rejected_challenger"
    assert _candidates(entry_c)["team-c"]["storyline"]["multiplier"] == 1.0

    # Points leader (rank 1 -> W 1.10) at the SAME intensity 62:
    # 62 * 1.10 = 68.2, margin 18.2 >= 15 -> the storyline tips it.
    rig_b = Rig(standings_provider=lambda: list(STANDINGS))
    await rig_b.fight("fight-b", ["team-b", "team-c"], 62.0)
    assert rig_b.director_service.snapshot()["active_camera"] == 2
    entry_b = [e for e in rig_b.live.decision_log()
               if e["outcome"] == "switch"][-1]
    cand_b = _candidates(entry_b)["team-b"]
    assert cand_b["storyline"]["multiplier"] == 1.1
    factors = [f["factor"] for f in cand_b["storyline"]["factors"]]
    assert "standings_rank_1" in factors
    assert cand_b["breakdown"]["storyline_bonus"] == 6.2
    assert cand_b["score"] == 68.2


async def test_storyline_equal_signal_scores_differ_in_one_log():
    # MANUAL mode logs every evaluation without writing: both
    # challengers appear in one candidate list at equal intensity.
    rig = Rig(standings_provider=lambda: list(STANDINGS))
    await rig.director_service.set_mode("manual")
    await rig.fight("fight-b", ["team-b", "team-d"], 70.0)
    await rig.fight("fight-c", ["team-c", "team-d"], 70.0)
    entry = rig.live.decision_log()[-1]
    cands = _candidates(entry)
    assert cands["team-b"]["score"] == 77.0  # 70 * 1.10
    assert cands["team-c"]["score"] == 70.0  # 70 * 1.00
    assert entry["challenger"]["team_id"] == "team-b"


async def test_storyline_alone_never_switches():
    rig = Rig(standings_provider=lambda: list(STANDINGS))
    rig.live.note_narrative_flag("team-b", "rivalry")
    # No fight signal at all: no candidate, no decision, no switch.
    assert await rig.live.evaluate_now() is None
    assert rig.live.decision_log() == []
    assert rig.director_service.snapshot()["active_camera"] == 1
    # The multiplier itself is real (>1) — but it multiplies a base
    # of 0, so the team's score is exactly 0.
    scored = rig.live._score_team("team-b", rig.clock())
    assert scored["storyline"]["multiplier"] == 1.15  # rank1 .10 + rivalry .05
    assert scored["base_score"] == 0.0
    assert scored["score"] == 0.0


async def test_missing_standings_multiplier_exactly_one_in_log():
    rig = Rig()  # default providers: no live-stats singleton in tests
    await rig.fight("fight-b", ["team-b", "team-c"], 85.0)
    assert rig.director_service.snapshot()["active_camera"] == 2
    entry = [e for e in rig.live.decision_log()
             if e["outcome"] == "switch"][-1]
    for cand in entry["candidates"]:
        assert cand["storyline"] == {"multiplier": 1.0, "factors": []}
        assert cand["breakdown"]["storyline"] == {
            "multiplier": 1.0, "factors": [],
        }
        assert cand["breakdown"]["storyline_bonus"] == 0.0


def test_storyline_pure_factors_and_cap():
    cfg = LiveDirectorConfig()
    # Nothing exists -> exactly neutral.
    assert compute_storyline("team-x", config=cfg) == {
        "multiplier": 1.0, "uplift": 0.0, "factors": [],
    }
    # Match point + hot streak + narrative stack, hard-capped.
    story = compute_storyline(
        "team-x",
        standings_row={"rank": 1, "total_points": 99},
        on_match_point=True,
        recent_finishes=5,
        narrative_flags=["rivalry", "comeback", "milestone"],
        win_probability=55.0,
        win_prob_is_leader=True,
        config=cfg,
    )
    assert story["multiplier"] == cfg.storyline_max_multiplier == 1.35
    kinds = {f["factor"] for f in story["factors"]}
    assert kinds == {
        "standings_rank_1", "match_point", "hot_streak",
        "narrative_rivalry", "narrative_comeback", "narrative_milestone",
        "win_probability_leader",
    }
    # Win-probability only counts for the leader above the floor.
    low = compute_storyline(
        "team-x", win_probability=20.0, win_prob_is_leader=True, config=cfg
    )
    assert low["multiplier"] == 1.0
    not_leader = compute_storyline(
        "team-x", win_probability=80.0, win_prob_is_leader=False, config=cfg
    )
    assert not_leader["multiplier"] == 1.0


async def test_hot_streak_from_live_kill_stream_enters_log():
    rig = Rig()
    for _ in range(3):
        await rig.kill("Team B", "Team C")
    scored = rig.live._score_team("team-b", rig.clock())
    kinds = {f["factor"] for f in scored["storyline"]["factors"]}
    assert "hot_streak" in kinds
    assert scored["storyline"]["multiplier"] == 1.08


# ── Build 2: PRE_COMBAT engagement state ─────────────────────────


async def test_precombat_convergence_prepositions_preview_no_cut():
    rig = Rig()
    entry = await rig.live.note_potential_fight(["team-b"], 0.6)
    # State is PRE_COMBAT...
    assert rig.live.engagement.state_of("team-b") == EngagementState.PRE_COMBAT
    assert rig.live.status()["engagement"]["teams"]["team-b"]["state"] == (
        "PRE_COMBAT"
    )
    # ...PVW pre-positions on team B's slot...
    preview = rig.director_service.snapshot()["preview"]
    assert preview is not None and preview["slot"] == 2
    assert preview["breakdown"]["precombat"] == 15.0
    # ...but PGM never cuts on PRE_COMBAT alone.
    assert rig.director_service.snapshot()["active_camera"] == 1
    assert entry is not None and entry["outcome"] == "rejected_challenger"
    # The transition is logged with its triggering signal.
    transitions = rig.live.engagement.transitions()
    assert transitions[-1]["to"] == "PRE_COMBAT"
    assert any(
        "potential_fight_confidence" in s
        for s in transitions[-1]["signals"]
    )


async def test_promotion_moves_precombat_to_active_combat_and_clutch():
    rig = Rig()
    await rig.live.note_potential_fight(["team-b"], 0.65)
    assert rig.live.engagement.state_of("team-b") == EngagementState.PRE_COMBAT
    await rig.fight("fight-b", ["team-b", "team-c"], 85.0)
    assert rig.live.engagement.state_of("team-b") == (
        EngagementState.ACTIVE_COMBAT
    )

    rig2 = Rig()
    await rig2.fight("fight-c", ["team-c", "team-d"], 90.0, is_clutch=True)
    assert rig2.live.engagement.state_of("team-c") == EngagementState.CLUTCH


async def test_engagement_silence_decays_to_phase_baseline():
    rig = Rig(phase="loot")
    await rig.live.note_potential_fight(["team-b"], 0.6)
    assert rig.live.engagement.state_of("team-b") == EngagementState.PRE_COMBAT
    rig.clock.advance(25)  # > precombat_ttl_s (20)
    await rig.live.evaluate_now()
    assert rig.live.engagement.state_of("team-b") == EngagementState.LOOTING
    transitions = rig.live.engagement.transitions()
    assert any(
        t["from"] == "PRE_COMBAT" and t["to"] == "LOOTING"
        for t in transitions
    )


def test_engagement_tracker_standalone_fusion_and_decay():
    clock = FakeClock()
    tracker = EngagementTracker(clock=clock)
    tracker.observe_phase("mid_game")
    # Rotation alone -> ROTATING.
    tracker.note_rotation("team-b", active=True)
    assert tracker.state_of("team-b") == EngagementState.ROTATING
    # Rotation into an occupied POI -> PRE_COMBAT.
    tracker.note_rotation("team-c", active=True, occupied_by="team-b",
                          poi="Mid")
    assert tracker.state_of("team-c") == EngagementState.PRE_COMBAT
    # Proximity inside the pixel threshold -> PRE_COMBAT.
    tracker.note_proximity("team-d", distance_px=40.0)
    assert tracker.state_of("team-d") == EngagementState.PRE_COMBAT
    # Metres without a calibrated scale are ignored, never guessed.
    tracker.note_proximity("team-a", distance_m=30.0)
    assert tracker.state_of("team-a") == EngagementState.IDLE
    # Weak potential-fight confidence does not enter PRE_COMBAT.
    tracker.note_potential_fight(["team-a"], 0.3)
    assert tracker.state_of("team-a") == EngagementState.IDLE
    # Silence decays everything back to the phase baseline.
    clock.advance(30)
    tracker.tick()
    assert tracker.state_of("team-c") == EngagementState.IDLE
    assert tracker.state_of("team-b") == EngagementState.IDLE


# ── Build 3: drop clash ──────────────────────────────────────────


class FakePoiDb:
    POIS = {
        ("erangel", "Alpha"): {"name": "Alpha", "map": "erangel",
                                "x": 0.1, "y": 0.1, "type": "city", "tier": 1},
        ("erangel", "Beta"): {"name": "Beta", "map": "erangel",
                               "x": 0.9, "y": 0.9, "type": "city", "tier": 1},
        ("erangel", "Mid"): {"name": "Mid", "map": "erangel",
                              "x": 0.5, "y": 0.52, "type": "town", "tier": 2},
        ("erangel", "Far"): {"name": "Far", "map": "erangel",
                              "x": 0.1, "y": 0.9, "type": "town", "tier": 2},
    }

    def by_name(self, map_id, name):
        return self.POIS.get((map_id, name))

    def all_pois(self, map_id):
        return [p for (m, _), p in self.POIS.items() if m == map_id]

    def nearest(self, map_id, x, y, max_radius=0.05):
        return None

    def maps(self):
        return ["erangel"]


def _drop_rows() -> List[Dict[str, Any]]:
    return [
        {"team_id": "t1", "team_name": "One", "named_location": "Mid",
         "contested_by": []},
        {"team_id": "t1", "team_name": "One", "named_location": "Mid",
         "contested_by": []},
        {"team_id": "t2", "team_name": "Two", "named_location": "Mid",
         "contested_by": []},
        {"team_id": "t3", "team_name": "Three", "named_location": "Far",
         "contested_by": []},
    ]


async def test_drop_clash_two_teams_same_poi_emits_intel_alert():
    from app.services.flight_path import FlightPathService

    emitted: List[Any] = []

    async def _publish(event_type, data):
        emitted.append((event_type, data))

    svc = FlightPathService(poi_db=FakePoiDb(), publish=_publish)
    await svc.set_path(map_id="erangel", start_poi="Alpha", end_poi="Beta")
    fresh = await svc.check_drop_clashes(_drop_rows())

    by_basis = {a["basis"]: a for a in fresh}
    assert set(by_basis) == {"predicted", "observed"}
    for alert in fresh:
        assert alert["kind"] == "drop_clash"
        assert alert["poi"] == "Mid"
        assert alert["teams"] == ["t1", "t2"]
        # Corridor/normalized units only — no metre claims anywhere.
        assert alert["units"] == "normalized_map_coordinates"
        assert not any("meter" in str(k) or "_m" == str(k)[-2:]
                       for k in alert)
    # Both alerts went out on the bus as intel_alert (Phase 24 shape);
    # the publisher also carries set_path's own flight_path_set event.
    intel = [e for e in emitted if e[0] == "intel_alert"]
    assert len(intel) == 2
    assert all(e[1]["kind"] == "drop_clash" for e in intel)
    # Repeat check: deduped, no bus spam.
    assert await svc.check_drop_clashes(_drop_rows()) == []
    assert len([e for e in emitted if e[0] == "intel_alert"]) == 2


async def test_drop_clash_single_team_and_no_path_no_alert():
    from app.services.flight_path import FlightPathService

    emitted: List[Any] = []

    async def _publish(event_type, data):
        emitted.append((event_type, data))

    svc = FlightPathService(poi_db=FakePoiDb(), publish=_publish)
    await svc.set_path(map_id="erangel", start_poi="Alpha", end_poi="Beta")
    single = [r for r in _drop_rows() if r["team_id"] == "t1"]
    assert await svc.check_drop_clashes(single) == []
    assert [e for e in emitted if e[0] == "intel_alert"] == []

    # No flight path set -> predicted data absent -> never fires,
    # even with two teams recorded at the same POI.
    svc2 = FlightPathService(poi_db=FakePoiDb(), publish=_publish)
    assert await svc2.check_drop_clashes(_drop_rows()) == []
    assert svc2.detect_drop_clashes(_drop_rows()) == []
    assert [e for e in emitted if e[0] == "intel_alert"] == []


async def test_drop_clash_reaches_director_watch_and_preview():
    rig = Rig(phase="plane")
    await rig.publish(
        "intel_alert",
        {
            "kind": "drop_clash",
            "poi": "Mid",
            "teams": ["team-b", "team-c"],
            "team_names": ["Team B", "Team C"],
            "basis": "predicted",
            "source": "predicted_drops_corridor",
            "units": "normalized_map_coordinates",
        },
    )
    snap = rig.director_service.snapshot()
    # Watch raised: PVW pre-positions on a clash team's slot...
    assert snap["preview"] is not None
    assert snap["preview"]["slot"] == 2  # team-b wins the deterministic tie
    assert snap["preview"]["breakdown"]["precombat"] == 10.0
    # ...the director's watch surface names the clash...
    watch = rig.live.status()["drop_clash_watch"]
    assert {w["team_id"] for w in watch} == {"team-b", "team-c"}
    assert all(w["poi"] == "Mid" for w in watch)
    # ...and PGM does NOT cut on a predicted clash.
    assert snap["active_camera"] == 1


# ── Build 4: context-first beat ──────────────────────────────────


async def _anticipation_win(rig: Rig) -> None:
    """PRE_COMBAT for team B + enough kill heat to win the margin —
    without any promoted fight (the definition of an anticipation cut)."""
    await rig.live.note_potential_fight(["team-b"], 0.6)
    for _ in range(4):
        await rig.kill("Team B", "Team C")


async def test_context_first_beat_taken_for_anticipation_cut():
    rig = Rig()
    await _anticipation_win(rig)
    snap = rig.director_service.snapshot()
    # The beat is on air: establishing view, fight feed NOT yet cut.
    assert snap["scene"] == "map_cam"
    assert snap["active_camera"] == 1
    beats = [e for e in rig.live.decision_log()
             if e["outcome"] == "context_beat"]
    assert len(beats) == 1
    assert beats[0]["context_beat"]["taken"] is True
    assert beats[0]["context_beat"]["scene"] == "map_cam"
    assert rig.live.status()["pending_context_beat"] is not None

    # Beat elapses -> the cut completes to the fight feed.
    rig.clock.advance(3.0)
    await rig.live.evaluate_now()
    snap = rig.director_service.snapshot()
    assert snap["scene"] == "main"
    assert snap["active_camera"] == 2
    done = [e for e in rig.live.decision_log()
            if e.get("rule") == "context_beat_completed"]
    assert len(done) == 1
    assert done[0]["context_beat"]["completed"] is True
    assert rig.live.status()["pending_context_beat"] is None


async def test_context_first_beat_skipped_for_hot_cut():
    rig = Rig()
    await rig.fight("fight-b", ["team-b", "team-c"], 85.0)
    snap = rig.director_service.snapshot()
    assert snap["active_camera"] == 2  # cut immediately, no beat delay
    assert snap["scene"] == "main"
    entry = [e for e in rig.live.decision_log()
             if e["outcome"] == "switch"][-1]
    assert entry["context_beat"]["taken"] is False
    assert entry["context_beat"]["reason"] == "hot_fight_cut_never_delayed"


async def test_context_first_beat_skipped_in_final_circle():
    rig = Rig(phase="final_circle")
    # Anticipation signals (intel convergence) + kill heat carry the
    # challenger past the margin — in the final circle it must still
    # cut immediately, with no beat.
    await rig.publish(
        "intel_alert",
        {"kind": "third_party", "teams": ["team-b", "team-c", "team-d"]},
    )
    for _ in range(4):
        await rig.kill("Team B", "Team C")
    snap = rig.director_service.snapshot()
    assert snap["active_camera"] == 2  # immediate cut, no beat
    assert snap["scene"] == "main"
    entry = [e for e in rig.live.decision_log()
             if e["outcome"] == "switch"][-1]
    assert entry["context_beat"]["taken"] is False
    assert entry["context_beat"]["reason"] == "final_circle_no_beat"


async def test_context_first_beat_skipped_without_establishing_view():
    rig = Rig(establishing_view_provider=lambda: None)
    await _anticipation_win(rig)
    snap = rig.director_service.snapshot()
    # No establishing view in this rig: skip silently, cut directly.
    assert snap["active_camera"] == 2
    assert snap["scene"] == "main"
    entry = [e for e in rig.live.decision_log()
             if e["outcome"] == "switch"][-1]
    assert entry["context_beat"]["taken"] is False
    assert entry["context_beat"]["reason"] == (
        "establishing_view_unavailable"
    )
