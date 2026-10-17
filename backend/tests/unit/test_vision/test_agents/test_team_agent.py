# backend/tests/unit/test_agents/test_team_agent.py
"""
Unit tests for Team Agent.
Tests team state tracking, player monitoring, fight detection,
and inter-agent communication.
"""

import pytest
import time
from unittest.mock import AsyncMock, Mock

from app.agents.base_agent import AgentConfig

from app.agents.team_agents.team_tracker import (
    TeamTracker, PlayerState, TeamActivity, ThreatLevel,
)


@pytest.mark.unit
@pytest.mark.agents
class TestTeamTracker:
    """Test TeamTracker functionality"""
    
    @pytest.fixture
    def mock_event_bus(self):
        """Create mock event bus"""
        bus = AsyncMock()
        bus.publish = AsyncMock(return_value="test-event-id")
        bus.subscribe = Mock(return_value="subscriber-id")
        bus.unsubscribe = Mock(return_value=True)
        bus.send_message = AsyncMock()
        bus.broadcast_message = AsyncMock()
        return bus
    
    @pytest.fixture
    def team_agent(self, mock_event_bus):
        """Create a test team agent"""
        return TeamTracker(
            config=AgentConfig(agent_id="test_team_001", agent_type="team_agent"),
            event_bus=mock_event_bus,
            team_id="test_team_001",
            team_name="Test Team",
            short_name="TEST",
            player_names=["Player1", "Player2", "Player3", "Player4"],
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # INITIALIZATION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_initialization(self, team_agent):
        """Test agent initializes with correct state"""
        assert team_agent.team.team_id == "test_team_001"
        assert team_agent.team.team_name == "Test Team"
        assert team_agent.team.short_name == "TEST"
        assert len(team_agent.team.players) == 4
        assert team_agent.team.alive_count == 4
        assert team_agent.team.activity == TeamActivity.LOOTING
    
    def test_players_initialized(self, team_agent):
        """Test players are initialized correctly"""
        players = team_agent.team.players
        assert len(players) == 4
        assert players[0].player_name == "Player1"
        assert players[1].player_name == "Player2"
        assert players[2].player_name == "Player3"
        assert players[3].player_name == "Player4"
        
        for player in players:
            assert player.state == PlayerState.ALIVE
            assert player.hp == 100
            assert player.armor == 100
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PLAYER MANAGEMENT TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_get_player(self, team_agent):
        """Test getting player by ID"""
        player = team_agent.get_player("test_team_001_player_1")
        assert player is not None
        assert player.player_name == "Player1"
    
    def test_get_player_not_found(self, team_agent):
        """Test getting non-existent player"""
        player = team_agent.get_player("non_existent")
        assert player is None
    
    def test_get_player_by_name(self, team_agent):
        """Test getting player by name"""
        player = team_agent.get_player_by_name("Player2")
        assert player is not None
        assert player.player_id == "test_team_001_player_2"
    
    def test_update_player_hp(self, team_agent):
        """Test updating player HP"""
        player = team_agent.get_player("test_team_001_player_1")
        
        team_agent.update_player_hp(player.player_id, 75, 80)
        
        assert player.hp == 75
        assert player.armor == 80
        assert player.damage_taken == 25
    
    def test_update_player_hp_damage_tracking(self, team_agent):
        """Test damage tracking on HP change"""
        player = team_agent.get_player("test_team_001_player_1")
        
        # Take damage
        team_agent.update_player_hp(player.player_id, 60)
        assert player.damage_taken == 40
        assert player.recent_damage_taken == 40
        
        # Take more damage
        team_agent.update_player_hp(player.player_id, 30)
        assert player.damage_taken == 70
        assert player.under_attack is True
    
    def test_update_player_position(self, team_agent):
        """Test updating player position"""
        player = team_agent.get_player("test_team_001_player_1")
        
        team_agent.update_player_position(player.player_id, 1000.0, 2000.0, 50.0)
        
        assert player.position_x == 1000.0
        assert player.position_y == 2000.0
        assert player.position_z == 50.0
    
    def test_update_player_position_movement(self, team_agent):
        """Test movement speed calculation"""
        player = team_agent.get_player("test_team_001_player_1")
        
        # Set initial position
        team_agent.update_player_position(player.player_id, 1000.0, 2000.0)
        time.sleep(0.1)
        
        # Move to new position
        team_agent.update_player_position(player.player_id, 1100.0, 2000.0)
        
        assert player.movement_speed > 0
        assert team_agent.team.distance_traveled > 0
    
    def test_update_player_state(self, team_agent):
        """Test updating player state"""
        player = team_agent.get_player("test_team_001_player_1")
        
        team_agent.update_player_state(player.player_id, PlayerState.KNOCKED)
        
        assert player.state == PlayerState.KNOCKED
        assert player.times_knocked == 1
        assert team_agent.team.knocked_count == 1
        assert team_agent.team.alive_count == 3
    
    def test_update_player_weapon(self, team_agent):
        """Test updating player weapon"""
        player = team_agent.get_player("test_team_001_player_1")
        
        team_agent.update_player_weapon(player.player_id, "M416", 0)
        team_agent.update_player_weapon(player.player_id, "AKM", 1)
        
        assert player.current_weapon == "M416"
        assert player.secondary_weapon == "AKM"
    
    def test_update_player_equipment(self, team_agent):
        """Test updating player equipment"""
        player = team_agent.get_player("test_team_001_player_1")
        
        team_agent.update_player_equipment(player.player_id, helmet_level=3, vest_level=2)
        
        assert player.helmet_level == 3
        assert player.has_helmet is True
        assert player.vest_level == 2
        assert player.has_vest is True
    
    def test_update_player_utility(self, team_agent):
        """Test updating player utility inventory"""
        player = team_agent.get_player("test_team_001_player_1")
        
        team_agent.update_player_utility(player.player_id, "smoke", 3)
        team_agent.update_player_utility(player.player_id, "grenade", 2)
        
        assert player.smokes == 3
        assert player.grenades == 2
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # COMBAT STATS TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_record_kill(self, team_agent):
        """Test recording a kill"""
        player = team_agent.get_player("test_team_001_player_1")
        
        team_agent.record_kill(
            killer_id=player.player_id,
            victim_id="enemy_001",
            weapon="M416",
            damage=100,
            distance=50.0,
            headshot=True,
        )
        
        assert player.kills == 1
        assert player.damage_dealt == 100
        assert player.headshots == 1
        assert player.longest_kill == 50.0
        assert team_agent.team.total_kills == 1
    
    def test_record_knock(self, team_agent):
        """Test recording a knock"""
        player = team_agent.get_player("test_team_001_player_1")
        
        team_agent.record_knock(player.player_id)
        
        assert player.knocks == 1
        assert team_agent.team.total_knocks == 1
    
    def test_record_revive(self, team_agent):
        """Test recording a revive"""
        reviver = team_agent.get_player("test_team_001_player_1")
        revived = team_agent.get_player("test_team_001_player_2")
        
        # Set revived as knocked first
        revived.state = PlayerState.KNOCKED
        team_agent.team.knocked_count = 1
        team_agent.team.alive_count = 3
        
        team_agent.record_revive(reviver.player_id, revived.player_id)
        
        assert reviver.revives_performed == 1
        assert revived.times_revived == 1
        assert revived.state == PlayerState.ALIVE
        assert team_agent.team.knocked_count == 0
        assert team_agent.team.alive_count == 4
    
    def test_record_healing(self, team_agent):
        """Test recording healing"""
        player = team_agent.get_player("test_team_001_player_1")
        player.hp = 50
        
        team_agent.record_healing(player.player_id, 75, "first_aid")
        
        assert player.healing_done == 75
        assert player.hp == 100  # Capped at max
        assert team_agent.team.total_healing == 75
    
    def test_record_utility_used(self, team_agent):
        """Test recording utility usage"""
        player = team_agent.get_player("test_team_001_player_1")
        player.smokes = 3
        
        team_agent.record_utility_used(player.player_id, "smoke")
        
        assert player.smokes == 2  # Decremented
        assert team_agent.team.total_smokes_used == 1
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TEAM STATE TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_update_team_center(self, team_agent):
        """Test team center calculation"""
        # Set positions
        team_agent.update_player_position("test_team_001_player_1", 1000, 1000)
        team_agent.update_player_position("test_team_001_player_2", 1100, 1000)
        team_agent.update_player_position("test_team_001_player_3", 1000, 1100)
        team_agent.update_player_position("test_team_001_player_4", 1100, 1100)
        
        team_agent._update_team_center()
        
        assert abs(team_agent.team.center_x - 1050) < 10
        assert abs(team_agent.team.center_y - 1050) < 10
    
    def test_formation_detection(self, team_agent):
        """Test formation type detection"""
        # Set positions in a diamond formation
        team_agent.update_player_position("test_team_001_player_1", 1000, 1000)  # Top
        team_agent.update_player_position("test_team_001_player_2", 1100, 1050)  # Right
        team_agent.update_player_position("test_team_001_player_3", 1000, 1100)  # Bottom
        team_agent.update_player_position("test_team_001_player_4", 900, 1050)   # Left
        
        team_agent._update_team_center()
        
        assert team_agent.team.formation_type in ["diamond", "scattered", "circle"]
    
    def test_team_survival_counts(self, team_agent):
        """Test team survival counting"""
        assert team_agent.team.alive_count == 4
        assert team_agent.team.knocked_count == 0
        assert team_agent.team.dead_count == 0
        
        # Knock two players
        team_agent.update_player_state("test_team_001_player_1", PlayerState.KNOCKED)
        team_agent.update_player_state("test_team_001_player_2", PlayerState.KNOCKED)
        
        assert team_agent.team.alive_count == 2
        assert team_agent.team.knocked_count == 2
        assert team_agent.team.dead_count == 0
    
    def test_team_eliminated(self, team_agent):
        """Test team elimination detection"""
        # Kill all players
        for i in range(1, 5):
            team_agent.update_player_state(f"test_team_001_player_{i}", PlayerState.DEAD)
        
        assert team_agent.team.is_eliminated() is True
        assert team_agent.team.activity == TeamActivity.ELIMINATED
    
    def test_team_wipable(self, team_agent):
        """Test team wipe detection (all alive are knocked)"""
        # Knock all players
        for i in range(1, 5):
            team_agent.update_player_state(f"test_team_001_player_{i}", PlayerState.KNOCKED)
        
        assert team_agent.team.is_wipable() is True
        assert team_agent.team.alive_count == 0
        assert team_agent.team.knocked_count == 4
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # THREAT ASSESSMENT TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_assess_threat_safe(self, team_agent):
        """Test threat assessment - safe situation"""
        threat = team_agent.assess_threat(
            nearby_enemy_count=0,
            nearest_enemy_distance=9999,
            under_attack=False,
        )
        
        assert threat == ThreatLevel.NONE
    
    def test_assess_threat_critical(self, team_agent):
        """Test threat assessment - critical situation"""
        # Set low HP
        team_agent.update_player_hp("test_team_001_player_1", 20)
        team_agent.update_player_hp("test_team_001_player_2", 15)
        
        threat = team_agent.assess_threat(
            nearby_enemy_count=4,
            nearest_enemy_distance=10,
            under_attack=True,
        )
        
        assert threat in [ThreatLevel.CRITICAL, ThreatLevel.HIGH]
    
    def test_assess_threat_with_zone_damage(self, team_agent):
        """Test threat assessment with zone damage"""
        team_agent.team.in_zone = False
        team_agent.team.zone_phase = 5
        
        threat = team_agent.assess_threat(
            zone_damage=True,
        )
        
        assert threat.value in ["medium", "high", "critical"]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ACTIVITY DETECTION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_detect_activity_fighting(self, team_agent):
        """Test activity detection - fighting"""
        team_agent.team.is_fighting = True
        
        team_agent._detect_team_activity()
        
        assert team_agent.team.activity == TeamActivity.FIGHTING
    
    def test_detect_activity_healing(self, team_agent):
        """Test activity detection - healing after fight"""
        team_agent.team.knocked_count = 1
        team_agent.team.alive_count = 3
        
        team_agent._detect_team_activity()
        
        # Team with knocked players should be in revive/healing state
        assert team_agent.team.activity in [TeamActivity.HEALING, TeamActivity.REVIVING]
    
    def test_detect_activity_rotating(self, team_agent):
        """Test activity detection - rotating"""
        team_agent.team.is_moving = True
        team_agent.team.movement_speed = 10.0
        
        team_agent._detect_team_activity()
        
        assert team_agent.team.activity == TeamActivity.ROTATING
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DATA EXPORT TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_get_team_summary(self, team_agent):
        """Test getting team summary"""
        summary = team_agent.get_team_summary()
        
        assert summary["team_id"] == "test_team_001"
        assert summary["team_name"] == "Test Team"
        assert summary["alive_count"] == 4
        assert summary["activity"] == "looting"
        assert len(summary["players"]) == 4
    
    def test_get_player_detail(self, team_agent):
        """Test getting player detail"""
        detail = team_agent.get_player_detail("test_team_001_player_1")
        
        assert detail is not None
        assert detail["name"] == "Player1"
        assert detail["state"] == "alive"
        assert detail["hp"] == 100
        assert "position" in detail
        assert "weapons" in detail
        assert "inventory" in detail
    
    def test_reset_for_new_match(self, team_agent):
        """Test resetting for new match"""
        # Change some state
        team_agent.team.total_kills = 10
        team_agent.team.alive_count = 2
        
        team_agent.reset_for_new_match()
        
        assert team_agent.team.total_kills == 0
        assert team_agent.team.alive_count == 4
        assert len(team_agent.team.players) == 4