# backend/tests/unit/test_match_state.py
"""
Unit tests for Match State management.
Tests centralized match state machine, state transitions,
and consistency validation.
"""

import pytest
import time

from app.core.match_state import MatchState, MatchStateMachine, MatchPhase


@pytest.mark.unit
class TestMatchState:
    """Test MatchState data structure"""
    
    def test_initial_state(self):
        """Test initial match state values"""
        state = MatchState(match_id="test_match_001")
        
        assert state.match_id == "test_match_001"
        assert state.phase == MatchPhase.NOT_STARTED
        assert state.teams_alive == 16
        assert state.players_alive == 64
        assert state.zone_phase == 0
        assert state.match_time == 0.0
    
    def test_update_teams_alive(self):
        """Test updating teams alive count"""
        state = MatchState(match_id="test_match_001")
        
        state.update_teams_alive(12)
        assert state.teams_alive == 12
        assert state.players_alive == 48  # 12 * 4
        
        state.update_teams_alive(5)
        assert state.teams_alive == 5
        assert state.players_alive == 20
    
    def test_update_zone(self):
        """Test updating zone information"""
        state = MatchState(match_id="test_match_001")
        
        state.update_zone(
            phase=3,
            center_x=4000.0,
            center_y=5000.0,
            radius=1000.0,
        )
        
        assert state.zone_phase == 3
        assert state.zone_center_x == 4000.0
        assert state.zone_center_y == 5000.0
        assert state.zone_radius == 1000.0
    
    def test_update_match_time(self):
        """Test updating match time"""
        state = MatchState(match_id="test_match_001")
        
        state.update_match_time(300.5)
        assert state.match_time == 300.5
        
        state.update_match_time(600.0)
        assert state.match_time == 600.0
    
    def test_add_kill_event(self):
        """Test adding kill events"""
        state = MatchState(match_id="test_match_001")
        
        state.add_kill_event(
            killer_id="player_1",
            victim_id="player_2",
            weapon="M416",
            headshot=True,
        )
        
        assert state.total_kills == 1
        assert len(state.recent_kills) == 1
        assert state.recent_kills[0]["killer_id"] == "player_1"
        assert state.recent_kills[0]["weapon"] == "M416"
    
    def test_recent_kills_limit(self):
        """Test recent kills buffer limit"""
        state = MatchState(match_id="test_match_001")
        
        # Add more than buffer size
        for i in range(60):
            state.add_kill_event(
                killer_id=f"player_{i}",
                victim_id=f"player_{i+100}",
                weapon="M416",
            )
        
        # Buffer should be limited to 50
        assert len(state.recent_kills) == 50
    
    def test_add_fight(self):
        """Test adding fights"""
        state = MatchState(match_id="test_match_001")
        
        fight_id = state.add_fight(
            teams_involved=["team_001", "team_002"],
            location=(4000.0, 5000.0),
            intensity=75.0,
        )
        
        assert fight_id is not None
        assert fight_id in state.active_fights
        assert state.active_fights[fight_id]["teams"] == ["team_001", "team_002"]
        assert state.active_fights[fight_id]["intensity"] == 75.0
        assert state.active_fights[fight_id]["is_active"] is True
    
    def test_resolve_fight(self):
        """Test resolving a fight"""
        state = MatchState(match_id="test_match_001")
        
        fight_id = state.add_fight(
            teams_involved=["team_001", "team_002"],
            location=(4000.0, 5000.0),
            intensity=75.0,
        )
        
        state.resolve_fight(fight_id, winner="team_001")
        
        assert fight_id not in state.active_fights
        assert len(state.fight_history) == 1
        assert state.fight_history[0]["winner"] == "team_001"
    
    def test_get_active_fights_count(self):
        """Test getting active fights count"""
        state = MatchState(match_id="test_match_001")
        
        state.add_fight(["team_001", "team_002"], (0, 0), 50)
        state.add_fight(["team_003", "team_004"], (0, 0), 80)
        
        assert state.get_active_fights_count() == 2
    
    def test_to_dict(self):
        """Test converting state to dictionary"""
        state = MatchState(match_id="test_match_001")
        state.update_zone(3, 4000, 5000, 1000)
        state.add_kill_event("p1", "p2", "M416", True)
        
        state_dict = state.to_dict()
        
        assert state_dict["match_id"] == "test_match_001"
        assert state_dict["zone_phase"] == 3
        assert state_dict["total_kills"] == 1
        assert state_dict["teams_alive"] == 16


@pytest.mark.unit
class TestMatchStateMachine:
    """Test MatchStateMachine transitions"""
    
    @pytest.fixture
    def state_machine(self, mock_event_bus):
        return MatchStateMachine(
            match_id="test_match_001",
            event_bus=mock_event_bus,
        )
    
    def test_initial_phase(self, state_machine):
        """Test initial phase"""
        assert state_machine.state.phase == MatchPhase.NOT_STARTED
    
    def test_transition_to_plane(self, state_machine):
        """Test transition to plane phase"""
        state_machine.transition_to(MatchPhase.PLANE)
        
        assert state_machine.state.phase == MatchPhase.PLANE
        assert len(state_machine.phase_history) == 1
    
    def test_transition_to_live(self, state_machine):
        """Test transition to live phase"""
        state_machine.transition_to(MatchPhase.PLANE)
        state_machine.transition_to(MatchPhase.LIVE)
        
        assert state_machine.state.phase == MatchPhase.LIVE
        assert len(state_machine.phase_history) == 2
    
    def test_invalid_transition(self, state_machine):
        """Test invalid phase transition"""
        state_machine.transition_to(MatchPhase.PLANE)
        
        # Cannot go back to NOT_STARTED from PLANE
        with pytest.raises(ValueError):
            state_machine.transition_to(MatchPhase.NOT_STARTED)
    
    def test_valid_transitions(self, state_machine):
        """Test valid transition chain"""
        valid_chain = [
            MatchPhase.LOBBY,
            MatchPhase.PLANE,
            MatchPhase.LIVE,
            MatchPhase.FINISHED,
        ]
        
        for phase in valid_chain:
            state_machine.transition_to(phase)
        
        assert state_machine.state.phase == MatchPhase.FINISHED
        assert len(state_machine.phase_history) == 4
    
    def test_final_circle_detection(self, state_machine):
        """Test final circle auto-detection"""
        state_machine.transition_to(MatchPhase.LIVE)
        state_machine.state.update_zone(7, 4000, 5000, 100)
        
        state_machine._check_final_circle()
        
        assert state_machine.state.phase == MatchPhase.FINAL_CIRCLE
    
    def test_match_end_detection(self, state_machine):
        """Test match end auto-detection"""
        state_machine.transition_to(MatchPhase.LIVE)
        state_machine.state.update_teams_alive(1)
        
        state_machine._check_match_end()
        
        assert state_machine.state.phase == MatchPhase.FINISHED
    
    def test_get_phase_duration(self, state_machine):
        """Test getting phase duration"""
        state_machine.transition_to(MatchPhase.PLANE)
        time.sleep(0.1)
        state_machine.transition_to(MatchPhase.LIVE)
        
        duration = state_machine.get_phase_duration(MatchPhase.PLANE)
        assert duration > 0
    
    def test_get_summary(self, state_machine):
        """Test getting state machine summary"""
        state_machine.transition_to(MatchPhase.LIVE)
        state_machine.state.add_kill_event("p1", "p2", "M416", False)
        
        summary = state_machine.get_summary()
        
        assert summary["phase"] == "live"
        assert summary["total_kills"] == 1
        assert summary["teams_alive"] == 16
        assert "phase_history" in summary