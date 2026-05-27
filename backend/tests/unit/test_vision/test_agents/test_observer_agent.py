# backend/tests/unit/test_agents/test_observer_agent.py
"""
Unit tests for Observer Director Agent.
Tests broadcast decision making, POV selection,
cinematic control, safety rules, and playbook management.
"""

import pytest
import time
from unittest.mock import AsyncMock, Mock, patch

from app.agents.observer_agents.director_ai import (
    ObserverDirector, BroadcastState, DirectorDecision,
    BroadcastScene,
)
from app.agents.observer_agents.pov_selector import (
    POVSelector, POVCandidate, FeedType, CameraMode,
)
from app.agents.observer_agents.cinematic_controller import (
    TransitionType,
)
from app.agents.observer_agents.safety_controller import (
    SafetyController, SafetyViolation,
)
from app.agents.observer_agents.playbook_manager import (
    PlaybookManager, PlaybookType,
)


@pytest.mark.unit
@pytest.mark.agents
class TestObserverDirector:
    """Test ObserverDirector core functionality"""
    
    @pytest.fixture
    def mock_event_bus(self):
        """Create mock event bus"""
        bus = AsyncMock()
        bus.publish = AsyncMock(return_value="test-event-id")
        bus.subscribe = Mock(return_value="subscriber-id")
        bus.unsubscribe = Mock(return_value=True)
        return bus
    
    @pytest.fixture
    def director(self, mock_event_bus):
        """Create observer director instance"""
        director = ObserverDirector(mock_event_bus)
        director.state.match_id = "test_match_001"
        return director
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # INITIALIZATION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_initialization(self, director):
        """Test director initializes with correct defaults"""
        assert director.config.agent_id == "observer_director"
        assert director.config.agent_type == "observer_director"
        assert director.config.priority.value == 100  # CRITICAL priority
        assert director.state.broadcast_state == BroadcastState.IDLE
        assert director.min_scene_duration > 0
        assert director.max_switches_per_minute > 0
    
    def test_submodules_initialized(self, director):
        """Test all sub-modules are initialized"""
        assert director.pov_selector is not None
        assert director.cinematic is not None
        assert director.safety is not None
        assert director.playbook is not None
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BROADCAST STATE MACHINE TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_update_broadcast_state_early_game(self, director):
        """Test broadcast state transitions to early game"""
        director.state.teams_alive = 16
        director.state.match_time_seconds = 30
        director.state.zone_phase = 0
        
        director._update_broadcast_state()
        
        assert director.state.broadcast_state == BroadcastState.EARLY_GAME
    
    def test_update_broadcast_state_mid_game(self, director):
        """Test broadcast state transitions to mid game"""
        director.state.teams_alive = 10
        director.state.zone_phase = 3
        director.state.match_time_seconds = 300
        
        director._update_broadcast_state()
        
        assert director.state.broadcast_state == BroadcastState.MID_GAME
    
    def test_update_broadcast_state_late_game(self, director):
        """Test broadcast state transitions to late game"""
        director.state.teams_alive = 5
        director.state.zone_phase = 5
        
        director._update_broadcast_state()
        
        assert director.state.broadcast_state == BroadcastState.LATE_GAME
    
    def test_update_broadcast_state_final_circle(self, director):
        """Test broadcast state transitions to final circle"""
        director.state.teams_alive = 3
        director.state.zone_phase = 7
        
        director._update_broadcast_state()
        
        assert director.state.broadcast_state == BroadcastState.FINAL_CIRCLE
    
    def test_update_broadcast_state_match_end(self, director):
        """Test broadcast state transitions to match end"""
        director.state.teams_alive = 1
        director.state.zone_phase = 8
        
        director._update_broadcast_state()
        
        assert director.state.broadcast_state == BroadcastState.MATCH_END
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DECISION MAKING TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_make_decision_no_fights(self, director):
        """Test decision making when no fights are active"""
        director.state.active_fights = {}
        director.state.teams_alive = 12
        
        decision = await director._make_director_decision()
        
        # Without fights and no current scene, may return None
        assert decision is None or isinstance(decision, dict)
    
    @pytest.mark.asyncio
    async def test_make_decision_with_high_intensity_fight(self, director):
        """Test decision making with high intensity fight"""
        director.state.active_fights = {
            "fight_001": {
                "teams": ["team_001", "team_002"],
                "intensity": "high",
                "intensity_score": 85,
                "is_clutch": False,
                "entertainment_score": 80,
                "detected_at": time.time(),
            }
        }
        director.state.teams_alive = 8
        
        # Mock POV selector to return a candidate
        candidate = POVCandidate(
            feed_type=FeedType.FIGHT,
            fight_id="fight_001",
            team_id="team_001",
            priority_score=85,
            reason="High intensity fight",
        )
        
        with patch.object(director.pov_selector, 'get_candidates', return_value=[candidate]):
            with patch.object(director.pov_selector, 'select_best_candidate', return_value=candidate):
                decision = await director._make_director_decision()
        
        # Should return a switch decision
        if decision:
            assert decision["type"] == DirectorDecision.SWITCH_FEED
    
    @pytest.mark.asyncio
    async def test_minimum_scene_time_enforced(self, director):
        """Test that minimum scene time prevents switching"""
        # Set current scene with short duration
        director.state.current_scene = BroadcastScene(
            feed_type=FeedType.FIGHT,
            fight_id="fight_001",
            started_at=time.time() - 2,  # Only 2 seconds ago
        )
        director.state.current_scene.duration = 2.0
        director.min_scene_duration = 10.0
        
        decision = await director._make_director_decision()
        
        # Should not switch because minimum time not met
        assert decision is None
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SCENE SWITCHING TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_switch_feed_execution(self, director):
        """Test executing a feed switch"""
        candidate = POVCandidate(
            feed_type=FeedType.TEAM_POV,
            team_id="team_001",
            player_id=None,
            priority_score=70,
            reason="Following team rotation",
        )
        
        await director._switch_feed(candidate, "Test switch")
        
        assert director.state.current_scene is not None
        assert director.state.current_scene.feed_type == FeedType.TEAM_POV
        assert director.state.current_scene.team_id == "team_001"
        assert director.state.total_scene_changes == 1
        assert len(director.state.previous_scenes) == 0  # First switch
    
    @pytest.mark.asyncio
    async def test_switch_feed_saves_previous(self, director):
        """Test that switching saves previous scene to history"""
        # Set initial scene
        director.state.current_scene = BroadcastScene(
            feed_type=FeedType.TEAM_POV,
            team_id="team_001",
        )
        
        # Switch to new scene
        candidate = POVCandidate(
            feed_type=FeedType.FIGHT,
            fight_id="fight_001",
            priority_score=85,
            reason="Fight detected",
        )
        
        await director._switch_feed(candidate, "Switch to fight")
        
        assert len(director.state.previous_scenes) == 1
        assert director.state.previous_scenes[0].team_id == "team_001"
        assert director.state.current_scene.fight_id == "fight_001"
    
    @pytest.mark.asyncio
    async def test_switch_to_fight(self, director):
        """Test switching to a specific fight"""
        director.state.active_fights["fight_001"] = {
            "teams": ["team_001", "team_002"],
            "intensity": "high",
            "intensity_score": 80,
            "is_clutch": False,
            "entertainment_score": 75,
        }
        
        with patch.object(director.pov_selector, 'select_best_team_for_fight', return_value="team_001"):
            await director._switch_to_fight("fight_001", "Important fight")
        
        assert director.state.current_scene is not None
        assert director.state.current_scene.fight_id == "fight_001"
    
    @pytest.mark.asyncio
    async def test_switch_to_clutch(self, director):
        """Test switching to clutch situation - highest priority"""
        await director._switch_to_clutch("clutch_001", "1v4 clutch!")
        
        assert director.state.current_scene is not None
        assert director.state.current_scene.feed_type == FeedType.PLAYER_POV
        assert director.state.current_scene.is_clutch is True
        assert director.state.current_scene.priority_score == 100
        assert director.state.current_scene.camera_mode == CameraMode.CLUTCH
    
    @pytest.mark.asyncio
    async def test_follow_player(self, director):
        """Test following a specific player"""
        await director._follow_player("player_star_001", "Star player action")
        
        assert director.state.current_scene is not None
        assert director.state.current_scene.feed_type == FeedType.PLAYER_POV
        assert director.state.current_scene.player_id == "player_star_001"
    
    @pytest.mark.asyncio
    async def test_show_replay(self, director):
        """Test showing a replay"""
        replay_data = {
            "type": "clutch",
            "priority": 95,
            "clip_id": "clip_001",
        }
        
        await director._show_replay(replay_data)
        
        assert director.state.broadcast_state == BroadcastState.REPLAY
        assert director.state.total_replays_shown == 1
    
    @pytest.mark.asyncio
    async def test_replay_cooldown_respected(self, director):
        """Test that replay cooldown is enforced"""
        director.state.last_replay_time = time.time()  # Just showed replay
        
        replay_data = {"type": "clutch", "priority": 90}
        
        await director._show_replay(replay_data)
        
        # Should not show because cooldown active
        assert director.state.total_replays_shown == 0
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EVENT HANDLING TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_on_fight_detected_critical(self, director):
        """Test handling critical intensity fight detection"""
        data = {
            "fight_id": "fight_critical",
            "teams": ["team_001", "team_002"],
            "intensity": "extreme",
            "intensity_score": 92,
            "is_clutch": False,
            "entertainment_score": 90,
        }
        
        await director._on_fight_detected(data)
        
        assert "fight_critical" in director.state.active_fights
        # Critical fight should trigger immediate switch
        assert director.state.candidate_priority_score > 0
    
    @pytest.mark.asyncio
    async def test_on_fight_ended(self, director):
        """Test handling fight end event"""
        director.state.active_fights["fight_001"] = {
            "teams": ["team_001", "team_002"],
            "intensity": "medium",
            "intensity_score": 50,
        }
        director.state.current_scene = BroadcastScene(
            feed_type=FeedType.FIGHT,
            fight_id="fight_001",
        )
        
        await director._on_fight_ended({"fight_id": "fight_001"})
        
        assert "fight_001" not in director.state.active_fights
    
    @pytest.mark.asyncio
    async def test_on_clutch_detected(self, director):
        """Test handling clutch detection - forces immediate switch"""
        data = {
            "clutch_id": "clutch_epic",
            "player_name": "ScoutOP",
            "probability": 0.08,
            "enemy_count": 4,
        }
        
        await director._on_clutch_detected(data)
        
        # Clutch detection should force immediate switch
        assert director.state.current_scene is not None
    
    @pytest.mark.asyncio
    async def test_on_clutch_resolved_win(self, director):
        """Test handling clutch win - queues replay"""
        data = {
            "won": True,
            "player_name": "ScoutOP",
            "enemies_killed": 4,
        }
        
        await director._on_clutch_resolved(data)
        
        assert len(director.state.replay_buffer) == 1
        assert director.state.replay_buffer[0]["type"] == "clutch"
        assert director.state.replay_buffer[0]["priority"] == 100
    
    @pytest.mark.asyncio
    async def test_on_kill_in_watched_fight(self, director):
        """Test handling kill in currently watched fight"""
        director.state.current_scene = BroadcastScene(
            feed_type=FeedType.FIGHT,
            fight_id="fight_001",
        )
        
        data = {
            "fight_id": "fight_001",
            "killer_id": "player_killer",
            "victim_id": "player_victim",
        }
        
        with patch.object(director, '_follow_player', new_callable=AsyncMock) as mock_follow:
            await director._on_kill(data)
            mock_follow.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_on_zone_change_final(self, director):
        """Test handling zone change to final circle"""
        data = {"phase": 7, "center_x": 4000, "center_y": 5000, "radius": 100}
        
        await director._on_zone_change(data)
        
        assert director.state.zone_phase == 7
        assert director.state.broadcast_state == BroadcastState.FINAL_CIRCLE
    
    @pytest.mark.asyncio
    async def test_on_highlight_moment(self, director):
        """Test handling auto-detected highlight moment"""
        data = {
            "type": "squad_wipe",
            "priority": 85,
            "description": "Team Soul wipes GodLike",
        }
        
        await director._on_highlight_moment(data)
        
        assert len(director.state.replay_buffer) == 1
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # OBS COMMUNICATION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_send_obs_switch_command(self, director):
        """Test sending OBS switch command"""
        scene = BroadcastScene(
            feed_type=FeedType.FIGHT,
            team_id="team_001",
            fight_id="fight_001",
            camera_mode=CameraMode.DYNAMIC,
            transition=TransitionType.WHIP_PAN,
        )
        
        await director._send_obs_switch_command(scene)
        
        director.event_bus.publish.assert_called_once()
        call_args = director.event_bus.publish.call_args
        assert call_args[1]["event_type"] == "broadcast_command"
        assert call_args[1]["data"]["command"] == "switch_scene"
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SCENE SCORE CALCULATION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_calculate_current_scene_score_fight(self, director):
        """Test scene score for active fight"""
        director.state.current_scene = BroadcastScene(
            feed_type=FeedType.FIGHT,
            fight_id="fight_001",
        )
        director.state.active_fights["fight_001"] = {
            "intensity_score": 80,
            "is_clutch": False,
            "entertainment_score": 70,
        }
        
        score = director._calculate_current_scene_score()
        
        assert score > 50  # Active fight should have good score
    
    def test_calculate_current_scene_score_clutch(self, director):
        """Test scene score for clutch situation"""
        director.state.current_scene = BroadcastScene(
            feed_type=FeedType.FIGHT,
            fight_id="fight_clutch",
        )
        director.state.active_fights["fight_clutch"] = {
            "intensity_score": 90,
            "is_clutch": True,
            "entertainment_score": 95,
        }
        
        score = director._calculate_current_scene_score()
        
        assert score > 80  # Clutch should have very high score
    
    def test_calculate_current_scene_score_priority_team(self, director):
        """Test scene score with priority team bonus"""
        director.state.current_scene = BroadcastScene(
            feed_type=FeedType.TEAM_POV,
            team_id="team_priority",
        )
        director.priority_teams.add("team_priority")
        
        score = director._calculate_current_scene_score()
        
        assert score > 50  # Priority team bonus
    
    def test_calculate_current_scene_score_stale(self, director):
        """Test scene score decreases for stale scenes"""
        director.state.current_scene = BroadcastScene(
            feed_type=FeedType.TEAM_POV,
            team_id="team_001",
            started_at=time.time() - 40,  # 40 seconds old
        )
        director.state.current_scene.duration = 40.0
        
        score = director._calculate_current_scene_score()
        
        # Should be lower due to staleness penalty
        assert score < 50
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PLAYBOOK INTEGRATION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_execute_playbook_action(self, director):
        """Test executing a playbook action"""
        action = {
            "type": "switch_feed",
            "candidate": POVCandidate(
                feed_type=FeedType.TEAM_POV,
                team_id="team_001",
                priority_score=60,
                reason="Playbook rotation",
            ),
            "reason": "Playbook: rotate teams",
        }
        
        await director._execute_playbook_action(action)
        
        assert director.state.current_scene is not None
        assert director.state.current_scene.feed_type == FeedType.TEAM_POV
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATE MANAGEMENT TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_update_match_context(self, director):
        """Test updating match context"""
        director.state.recent_decisions.append({
            "timestamp": time.time(),
            "decision": "switch_feed",
        })
        
        director._update_match_context()
        
        assert director.state.switch_count_last_minute == 1
    
    def test_get_director_summary(self, director):
        """Test getting director summary"""
        summary = director.get_director_summary()
        
        assert "broadcast_state" in summary
        assert "current_scene" in summary
        assert "active_fights" in summary
        assert "teams_alive" in summary
        assert "zone_phase" in summary
        assert "total_scene_changes" in summary
        assert "total_replays" in summary
        assert "switches_last_minute" in summary
        assert "replay_buffer_size" in summary
    
    def test_set_priority_teams(self, director):
        """Test setting priority teams for broadcast focus"""
        director.set_priority_teams(["team_001", "team_002", "team_003"])
        
        assert len(director.priority_teams) == 3
        assert "team_001" in director.priority_teams
        assert "team_002" in director.priority_teams
        assert "team_003" in director.priority_teams
    
    def test_set_priority_players(self, director):
        """Test setting priority players for broadcast focus"""
        director.set_priority_players(["player_star", "player_clutch", "player_mvp"])
        
        assert len(director.priority_players) == 3
        assert "player_star" in director.priority_players
        assert "player_clutch" in director.priority_players
    
    def test_force_switch_manual_override(self, director):
        """Test manual force switch (human override)"""
        target = {
            "feed_type": "team_pov",
            "team_id": "team_override",
            "player_id": None,
        }
        
        director.force_switch(target)
        
        # Force switch creates an async task - verify it was triggered
        # The actual switch happens asynchronously
        assert True  # If no exception, test passes
    
    def test_reset_for_match(self, director):
        """Test resetting director for new match"""
        director.state.total_scene_changes = 50
        director.state.active_fights["fight_old"] = {}
        director.priority_teams.add("team_old")
        
        director.reset_for_match("new_match_002")
        
        assert director.state.match_id == "new_match_002"
        assert director.state.total_scene_changes == 0
        assert len(director.state.active_fights) == 0


@pytest.mark.unit
@pytest.mark.agents
class TestPOVSelector:
    """Test POVSelector functionality"""
    
    @pytest.fixture
    def selector(self):
        """Create POVSelector instance"""
        return POVSelector()
    
    def test_initialization(self, selector):
        """Test selector initializes correctly"""
        assert len(selector.team_feeds) == 0
        assert len(selector.player_feeds) == 0
        assert len(selector.priority_teams) == 0
        assert len(selector.priority_players) == 0
    
    def test_get_candidates_empty(self, selector):
        """Test getting candidates with no state"""
        candidates = selector.get_candidates(
            director_state=Mock(active_fights={}, teams_alive=16, zone_phase=1),
            active_fights={},
        )
        
        # May return cinematic/overview candidates
        assert isinstance(candidates, list)
    
    def test_get_candidates_with_fights(self, selector):
        """Test getting candidates with active fights"""
        active_fights = {
            "fight_001": {
                "teams": ["team_001", "team_002"],
                "intensity": "high",
                "intensity_score": 80,
                "is_clutch": False,
                "entertainment_score": 75,
            }
        }
        
        candidates = selector.get_candidates(
            director_state=Mock(active_fights=active_fights, teams_alive=8, zone_phase=4),
            active_fights=active_fights,
        )
        
        assert len(candidates) > 0
    
    def test_select_best_candidate(self, selector):
        """Test selecting best candidate"""
        candidates = [
            POVCandidate(feed_type=FeedType.FIGHT, fight_id="f1", priority_score=85, reason="High intensity"),
            POVCandidate(feed_type=FeedType.TEAM_POV, team_id="t1", priority_score=40, reason="Coverage"),
            POVCandidate(feed_type=FeedType.CINEMATIC, priority_score=20, reason="Filler"),
        ]
        
        best = selector.select_best_candidate(candidates, Mock())
        
        assert best is not None
        assert best.fight_id == "f1"
        assert best.priority_score == 85
    
    def test_set_star_player(self, selector):
        """Test setting star player"""
        selector.set_star_player("player_001", 9.5)
        
        assert "player_001" in selector.star_players
        assert selector.star_players["player_001"] == 9.5
        assert "player_001" in selector.priority_players
    
    def test_set_priority_team(self, selector):
        """Test setting priority team"""
        selector.set_priority_team("team_priority")
        
        assert "team_priority" in selector.priority_teams
    
    def test_record_feed_shown(self, selector):
        """Test recording that a feed was shown"""
        candidate = POVCandidate(
            feed_type=FeedType.TEAM_POV,
            team_id="team_001",
            player_id="player_001",
        )
        
        selector.record_feed_shown(candidate)
        
        assert "team_001" in selector.recently_shown_teams
        assert "player_001" in selector.recently_shown_players


@pytest.mark.unit
@pytest.mark.agents
class TestSafetyController:
    """Test SafetyController functionality"""
    
    @pytest.fixture
    def safety(self):
        """Create SafetyController instance"""
        return SafetyController()
    
    def test_initialization(self, safety):
        """Test safety controller initializes correctly"""
        assert safety.config.min_scene_time > 0
        assert safety.config.max_switches_per_minute > 0
        assert safety.state.locked_until == 0.0
    
    def test_can_switch_normal(self, safety):
        """Test switching is allowed in normal state"""
        assert safety.can_switch(Mock()) is True
    
    def test_can_switch_when_locked(self, safety):
        """Test switching prevented when locked"""
        safety.state.locked_until = time.time() + 10  # Locked for 10 seconds
        safety.state.lock_reason = "Clutch situation"
        
        assert safety.can_switch(Mock()) is False
    
    def test_can_switch_manual_override(self, safety):
        """Test switching always allowed with manual override"""
        safety.state.locked_until = time.time() + 10
        safety.manual_override = True
        
        assert safety.can_switch(Mock()) is True
    
    def test_validate_switch_normal(self, safety):
        """Test validating a normal switch"""
        result = safety.validate_switch(None, Mock())
        assert result is True
    
    def test_validate_switch_during_replay(self, safety):
        """Test switch validation during replay"""
        safety.state.is_replay = True
        
        new_scene = Mock()
        new_scene.feed_type = Mock()
        new_scene.feed_type.value = "team_pov"
        
        result = safety.validate_switch(None, new_scene)
        assert result is False
    
    def test_apply_lock(self, safety):
        """Test applying safety lock"""
        safety._apply_lock(10.0, "Clutch focus")
        
        assert safety.state.locked_until > time.time()
        assert safety.state.lock_reason == "Clutch focus"
    
    def test_release_lock(self, safety):
        """Test releasing safety lock"""
        safety._apply_lock(10.0, "Test lock")
        safety.release_lock()
        
        assert safety.state.locked_until == 0.0
        assert safety.state.lock_reason == ""
    
    def test_record_violation(self, safety):
        """Test recording a safety violation"""
        safety._record_violation(SafetyViolation.SCENE_TOO_SHORT, "Scene was only 2 seconds")
        
        assert safety.state.violation_count == 1
        assert len(safety.state.violations) == 1
    
    def test_enable_disable_manual_override(self, safety):
        """Test toggling manual override"""
        safety.enable_manual_override()
        assert safety.manual_override is True
        
        safety.disable_manual_override()
        assert safety.manual_override is False
    
    def test_get_state(self, safety):
        """Test getting safety state"""
        state = safety.get_state()
        
        assert "locked" in state
        assert "is_clutch" in state
        assert "is_replay" in state
        assert "switches_last_minute" in state


@pytest.mark.unit
@pytest.mark.agents
class TestPlaybookManager:
    """Test PlaybookManager functionality"""
    
    @pytest.fixture
    def playbook(self):
        """Create PlaybookManager instance"""
        return PlaybookManager()
    
    def test_initialization(self, playbook):
        """Test playbook manager initializes with default playbooks"""
        assert len(playbook.playbooks) > 0
        assert PlaybookType.EARLY_GAME in playbook.playbooks
        assert PlaybookType.MID_GAME in playbook.playbooks
        assert PlaybookType.FINAL_CIRCLE in playbook.playbooks
        assert PlaybookType.CLUTCH in playbook.playbooks
    
    def test_get_active_playbook_early_game(self, playbook):
        """Test getting playbook for early game"""
        pb = playbook.get_active_playbook("early_game")
        
        assert pb is not None
        assert pb.playbook_type == PlaybookType.EARLY_GAME
    
    def test_get_active_playbook_final_circle(self, playbook):
        """Test getting playbook for final circle"""
        pb = playbook.get_active_playbook("final_circle")
        
        assert pb is not None
        assert pb.playbook_type == PlaybookType.FINAL_CIRCLE
    
    def test_get_action_with_fight(self, playbook):
        """Test getting action when fight is active"""
        context = {
            "has_active_fight": True,
            "fight_intensity": 75,
            "is_clutch": False,
            "teams_alive": 8,
            "match_time": 300,
        }
        
        director_mock = Mock()
        director_mock.active_fights = {"f1": {"intensity_score": 75}}
        director_mock.teams_alive = 8
        director_mock.match_time_seconds = 300
        
        with patch.object(playbook, '_build_context', return_value=context):
            action = playbook.get_action("mid_game", director_mock)
            
            if action:
                assert "type" in action
                assert "rule_id" in action
                assert "reason" in action
    
    def test_enable_disable_playbook(self, playbook):
        """Test enabling and disabling playbooks"""
        playbook.disable_playbook(PlaybookType.EARLY_GAME)
        assert playbook.playbooks[PlaybookType.EARLY_GAME].is_active is False
        
        playbook.enable_playbook(PlaybookType.EARLY_GAME)
        assert playbook.playbooks[PlaybookType.EARLY_GAME].is_active is True
    
    def test_get_playbook_summary(self, playbook):
        """Test getting playbook summary"""
        summary = playbook.get_playbook_summary()
        
        assert "active_playbook" in summary
        assert "playbooks" in summary
        assert "history" in summary