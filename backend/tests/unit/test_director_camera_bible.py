# backend/tests/unit/test_director_camera_bible.py
"""Unit tests for Visual Broadcast Bible camera corrections (Phase 18 W1).

Covers docs/VISUAL_BROADCAST_BIBLE.md sections 3 (camera switching
patterns) and 4 (fight coverage rules) as applied to the observer
director:

  * §3.1 — camera stays on ONE mid-game fight for 45-90s; faster
    switching only in early game / final circles
  * §3.2 — every camera change is a hard cut (1 frame)
  * §3.4 — switch only when: fight ends, a strictly more important fight
    starts elsewhere, or the zone forces repositioning
  * §4   — NO PiP for simultaneous fights; NO split-screen — pick one
    fight and commit, the other is missed live

No network, no DB, no real credentials. Async tests run under
``asyncio_mode = auto`` (see backend/tests/pytest.ini).
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.agents.base_agent import AgentConfig
from app.agents.observer_agents.director_ai import (
    FIGHT_SWITCH_SCORE_GAP,
    BroadcastScene,
    BroadcastState,
    DirectorDecision,
    ObserverDirector,
)
from app.agents.observer_agents.pov_selector import (
    POVCandidate,
    FeedType,
)
from app.agents.observer_agents.cinematic_controller import TransitionType
from app.services.pip import SOURCES, PiPService, PiPStateError


# --------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------- #


@pytest.fixture()
def mock_event_bus():
    """Mock event bus for agent construction."""
    bus = AsyncMock()
    bus.publish = AsyncMock(return_value="test-event-id")
    bus.subscribe = Mock(return_value="subscriber-id")
    bus.unsubscribe = Mock(return_value=True)
    return bus


@pytest.fixture()
def director(mock_event_bus):
    """Observer director with a match context."""
    director = ObserverDirector(
        config=AgentConfig(
            agent_id="observer_director", agent_type="observer_director"
        ),
        event_bus=mock_event_bus,
    )
    director.director_state.match_id = "test_match_bible"
    director.director_state.broadcast_state = BroadcastState.MID_GAME
    director.director_state.teams_alive = 8
    director.director_state.zone_phase = 3
    return director


def _active_fight(fight_id: str, intensity: int = 80) -> dict:
    return {
        "teams": ["team_001", "team_002"],
        "intensity": "high",
        "intensity_score": intensity,
        "is_clutch": False,
        "entertainment_score": intensity,
        "detected_at": time.time(),
    }


def _fight_scene(fight_id: str, duration: float) -> BroadcastScene:
    scene = BroadcastScene(
        feed_type=FeedType.FIGHT,
        fight_id=fight_id,
        team_id="team_001",
        started_at=time.time() - duration,
    )
    scene.duration = duration
    return scene


def _patch_decision_to(
    director: ObserverDirector, candidate: POVCandidate
):
    """Force the POV selector to surface exactly one candidate."""
    return (
        patch.object(
            director.pov_selector, "get_candidates", return_value=[candidate]
        ),
        patch.object(
            director.pov_selector,
            "select_best_candidate",
            return_value=candidate,
        ),
    )


# --------------------------------------------------------------------- #
# §3.1 / §4 — fight hold: stay on ONE fight, suppress second-fight cuts
# --------------------------------------------------------------------- #


@pytest.mark.unit
@pytest.mark.agents
class TestFightHoldWindow:
    """Bible §3.1: 45-90s hold on a mid-game fight; §4: never cut to a
    second simultaneous fight (it is missed live)."""

    def test_hold_window_true_on_active_midgame_fight(self, director):
        director.director_state.current_scene = _fight_scene("fight_001", 20)
        director.director_state.active_fights = {
            "fight_001": _active_fight("fight_001")
        }
        assert director._in_fight_hold_window() is True

    def test_hold_window_false_when_fight_ended(self, director):
        """Bible §3.4 — fight over, free to leave."""
        director.director_state.current_scene = _fight_scene("fight_001", 20)
        director.director_state.active_fights = {}  # fight_001 resolved
        assert director._in_fight_hold_window() is False

    def test_hold_window_false_in_final_circle(self, director):
        """Bible §3.1 — faster switching allowed in final circles."""
        director.director_state.current_scene = _fight_scene("fight_001", 20)
        director.director_state.active_fights = {
            "fight_001": _active_fight("fight_001")
        }
        director.director_state.broadcast_state = BroadcastState.FINAL_CIRCLE
        assert director._in_fight_hold_window() is False

    def test_hold_window_false_in_early_game(self, director):
        """Bible §3.1 — faster switching allowed in the early game."""
        director.director_state.current_scene = _fight_scene("fight_001", 20)
        director.director_state.active_fights = {
            "fight_001": _active_fight("fight_001")
        }
        director.director_state.broadcast_state = BroadcastState.EARLY_GAME
        assert director._in_fight_hold_window() is False

    def test_hold_window_false_when_not_on_fight(self, director):
        director.director_state.current_scene = BroadcastScene(
            feed_type=FeedType.MAP_OVERVIEW,
            started_at=time.time() - 10,
        )
        director.director_state.current_scene.duration = 10
        director.director_state.active_fights = {
            "fight_001": _active_fight("fight_001")
        }
        assert director._in_fight_hold_window() is False

    async def test_fight_switch_suppressed_within_hold(self, director):
        """A second fight 20s into the current fight must NOT trigger a
        switch — minor importance gap, mid-game."""
        director.director_state.current_scene = _fight_scene("fight_001", 20)
        director.director_state.active_fights = {
            "fight_001": _active_fight("fight_001", intensity=80),
            "fight_002": _active_fight("fight_002", intensity=90),
        }
        candidate = POVCandidate(
            feed_type=FeedType.FIGHT,
            fight_id="fight_002",
            team_id="team_003",
            priority_score=90,  # only 10 above the current fight's 80
            reason="Second fight",
            is_fight=True,
        )
        get_patch, select_patch = _patch_decision_to(director, candidate)
        with get_patch, select_patch:
            decision = await director._make_director_decision()
        assert decision is None  # hold wins over a 10-point gap

    async def test_fight_switch_allowed_for_much_more_important_fight(
        self, director
    ):
        """Bible §3.4 — a *strictly* more important fight breaks the hold."""
        director.director_state.current_scene = _fight_scene("fight_001", 20)
        director.director_state.active_fights = {
            "fight_001": _active_fight("fight_001", intensity=50),
            "fight_002": _active_fight("fight_002", intensity=95),
        }
        candidate = POVCandidate(
            feed_type=FeedType.FIGHT,
            fight_id="fight_002",
            team_id="team_003",
            priority_score=50 + FIGHT_SWITCH_SCORE_GAP,  # >= the gap
            reason="Critical second fight",
            is_fight=True,
        )
        get_patch, select_patch = _patch_decision_to(director, candidate)
        with get_patch, select_patch:
            decision = await director._make_director_decision()
        assert decision is not None
        assert decision["type"] == DirectorDecision.SWITCH_FEED

    async def test_clutch_candidate_breaks_hold(self, director):
        """Clutch is the highest priority — it always cuts, mid-fight or
        not (Bible §3.4)."""
        director.director_state.current_scene = _fight_scene("fight_001", 20)
        director.director_state.active_fights = {
            "fight_001": _active_fight("fight_001", intensity=80),
        }
        candidate = POVCandidate(
            feed_type=FeedType.PLAYER_POV,
            player_id="clutch_player",
            priority_score=100,
            reason="Clutch",
            is_clutch=True,
        )
        get_patch, select_patch = _patch_decision_to(director, candidate)
        with get_patch, select_patch:
            decision = await director._make_director_decision()
        assert decision is not None
        assert decision["type"] == DirectorDecision.SWITCH_FEED

    async def test_fight_switch_allowed_after_fight_ended(self, director):
        """Bible §3.4 — once the fight resolves, the director may leave."""
        director.director_state.current_scene = _fight_scene("fight_001", 60)
        director.director_state.active_fights = {
            "fight_002": _active_fight("fight_002", intensity=85),
        }
        candidate = POVCandidate(
            feed_type=FeedType.FIGHT,
            fight_id="fight_002",
            team_id="team_003",
            priority_score=85,
            reason="Next fight",
            is_fight=True,
        )
        get_patch, select_patch = _patch_decision_to(director, candidate)
        with get_patch, select_patch:
            decision = await director._make_director_decision()
        assert decision is not None

    async def test_fight_switch_allowed_in_final_circle(self, director):
        """Bible §3.1 — final circles cut faster; the normal (lower) bar
        applies instead of the fight-hold gap."""
        director.director_state.broadcast_state = BroadcastState.FINAL_CIRCLE
        director.director_state.current_scene = _fight_scene("fight_001", 25)
        director.director_state.active_fights = {
            "fight_001": _active_fight("fight_001", intensity=80),
            "fight_002": _active_fight("fight_002", intensity=90),
        }
        candidate = POVCandidate(
            feed_type=FeedType.FIGHT,
            fight_id="fight_002",
            team_id="team_003",
            priority_score=90,
            reason="Final-circle fight",
            is_fight=True,
        )
        get_patch, select_patch = _patch_decision_to(director, candidate)
        with get_patch, select_patch:
            decision = await director._make_director_decision()
        assert decision is not None


# --------------------------------------------------------------------- #
# §4 — forced fight switches must not yank the camera mid-fight
# --------------------------------------------------------------------- #


@pytest.mark.unit
@pytest.mark.agents
class TestForceSwitchGating:
    """_on_fight_detected used to force-switch on any 85+ intensity fight
    — even mid-fight. Per Bible §4 the new fight is missed live."""

    async def test_no_force_switch_mid_fight(self, director):
        director.director_state.current_scene = _fight_scene("fight_001", 20)
        director.director_state.active_fights = {
            "fight_001": _active_fight("fight_001", intensity=70)
        }
        with patch.object(
            director, "_switch_to_fight", new=AsyncMock()
        ) as switch_spy:
            await director._on_fight_detected(
                {
                    "fight_id": "fight_002",
                    "teams": ["team_003", "team_004"],
                    "intensity": "high",
                    "intensity_score": 90,
                    "is_clutch": False,
                    "entertainment_score": 90,
                }
            )
        # No instant yank — but the fight is tracked for the decision loop.
        switch_spy.assert_not_called()
        assert "fight_002" in director.director_state.active_fights

    async def test_force_switch_when_not_on_fight(self, director):
        """Bible §3: camera should pick up a new fight within 2-3s when it
        is not already holding one."""
        director.director_state.current_scene = BroadcastScene(
            feed_type=FeedType.MAP_OVERVIEW,
            started_at=time.time() - 12,
        )
        director.director_state.current_scene.duration = 12
        with patch.object(
            director, "_switch_to_fight", new=AsyncMock()
        ) as switch_spy:
            await director._on_fight_detected(
                {
                    "fight_id": "fight_002",
                    "teams": ["team_003", "team_004"],
                    "intensity": "high",
                    "intensity_score": 90,
                    "is_clutch": False,
                    "entertainment_score": 90,
                }
            )
        switch_spy.assert_awaited_once()

    async def test_clutch_force_switches_mid_fight(self, director):
        """A clutch always cuts immediately (Bible §3.4)."""
        director.director_state.current_scene = _fight_scene("fight_001", 20)
        director.director_state.active_fights = {
            "fight_001": _active_fight("fight_001", intensity=70)
        }
        with patch.object(
            director, "_switch_to_fight", new=AsyncMock()
        ) as switch_spy:
            await director._on_fight_detected(
                {
                    "fight_id": "fight_002",
                    "teams": ["team_003", "team_004"],
                    "intensity": "critical",
                    "intensity_score": 95,
                    "is_clutch": True,
                    "entertainment_score": 95,
                }
            )
        switch_spy.assert_awaited_once()


# --------------------------------------------------------------------- #
# §3.1 — staleness discount only after the hold window for fights
# --------------------------------------------------------------------- #


@pytest.mark.unit
@pytest.mark.agents
class TestSceneStaleness:
    def test_fight_scene_not_discounted_before_90s(self, director):
        """The old code started discounting fight scenes after 30s —
        Bible §3.1 says hold 45-90s."""
        director.director_state.current_scene = _fight_scene("fight_001", 40)
        director.director_state.active_fights = {
            "fight_001": _active_fight("fight_001", intensity=80)
        }
        score = director._calculate_current_scene_score()
        assert score == 80  # no staleness penalty at 40s

    def test_fight_scene_discounted_after_90s(self, director):
        director.director_state.current_scene = _fight_scene("fight_001", 100)
        director.director_state.active_fights = {
            "fight_001": _active_fight("fight_001", intensity=80)
        }
        score = director._calculate_current_scene_score()
        assert score == 80 - (100 - 90) * 0.5

    def test_nonfight_scene_discounted_after_30s(self, director):
        """Map cam and other non-fight scenes keep the old 30s stale line
        (Bible §2: map cam runs 15-30s)."""
        scene = BroadcastScene(
            feed_type=FeedType.MAP_OVERVIEW,
            started_at=time.time() - 40,
        )
        scene.duration = 40
        director.director_state.current_scene = scene
        score = director._calculate_current_scene_score()
        assert score == 50 - (40 - 30) * 0.5

    async def test_kill_follow_preserves_fight_hold(self, director):
        """Kill-follow POV changes stay inside the same fight (Bible §3.5:
        facecam syncs with the observed player) — the fight hold must not
        leak through the follow."""
        director.director_state.current_scene = _fight_scene("fight_001", 20)
        director.director_state.active_fights = {
            "fight_001": _active_fight("fight_001")
        }
        await director._on_kill({"fight_id": "fight_001", "killer_id": "p_9"})
        scene = director.director_state.current_scene
        assert scene.player_id == "p_9"
        assert scene.fight_id == "fight_001"
        assert director._in_fight_hold_window() is True


# --------------------------------------------------------------------- #
# §3.2 — hard cuts only
# --------------------------------------------------------------------- #


@pytest.mark.unit
@pytest.mark.agents
class TestHardCuts:
    def test_get_transition_always_cut(self, director):
        """Every camera change is a hard cut — clutch, fight change, final
        circle, same team. (Bible §3.2)."""
        clutch_candidate = POVCandidate(
            feed_type=FeedType.PLAYER_POV, is_clutch=True
        )
        fight_candidate = POVCandidate(
            feed_type=FeedType.FIGHT,
            fight_id="fight_002",
            is_fight=True,
        )
        final_candidate = POVCandidate(
            feed_type=FeedType.MAP_OVERVIEW, is_final_circle=True
        )
        current = _fight_scene("fight_001", 20)

        for candidate in (clutch_candidate, fight_candidate, final_candidate):
            assert (
                director.cinematic.get_transition(current, candidate)
                is TransitionType.CUT
            )

    async def test_switch_feed_sends_cut_to_obs(self, director):
        """The OBS broadcast command must carry transition='cut'."""
        candidate = POVCandidate(
            feed_type=FeedType.FIGHT,
            fight_id="fight_001",
            team_id="team_001",
            priority_score=85,
            reason="Fight",
        )
        await director._switch_feed(candidate, "Test cut")
        director.event_bus.publish.assert_awaited()
        broadcast_calls = [
            call
            for call in director.event_bus.publish.await_args_list
            if call.kwargs.get("event_type") == "broadcast_command"
        ]
        assert broadcast_calls, "expected an OBS broadcast_command"
        command = broadcast_calls[-1].kwargs["data"]
        assert command["transition"] == "cut"
        assert director.director_state.current_scene.transition is (
            TransitionType.CUT
        )


# --------------------------------------------------------------------- #
# §4 — PiP is map+gameplay only; split-screen never
# --------------------------------------------------------------------- #


@pytest.mark.unit
class TestNoFightPiP:
    def test_pip_source_keyspace_has_no_fight_feed(self):
        """By construction the PiP API cannot address a fight feed, so
        fight+fight PiP is unreachable (Bible §4)."""
        assert SOURCES == (
            "output",
            "map-cam",
            "standings",
            "killfeed",
            "commentary",
        )
        for key in SOURCES:
            assert "fight" not in key and "split" not in key

    async def test_pip_rejects_unknown_fight_source(self):
        """An operator cannot smuggle a fight feed into a PiP slot."""
        pip = PiPService()
        with pytest.raises(PiPStateError):
            await pip.set(inset_source="fight-cam", updated_by="op1")
        with pytest.raises(PiPStateError):
            await pip.set(main_source="fight_001", updated_by="op1")

    async def test_director_refuses_split_screen(self, director):
        """Bible §4: no split-screen, ever — enforced at the single funnel
        every camera change goes through."""
        candidate = POVCandidate(
            feed_type=FeedType.SPLIT_SCREEN,
            fight_id="fight_001",
            priority_score=99,
            reason="Two fights at once",
        )
        await director._switch_feed(candidate, "Two fights")
        assert director.director_state.current_scene is None
        assert director.director_state.total_scene_changes == 0
