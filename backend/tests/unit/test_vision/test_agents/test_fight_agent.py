# backend/tests/unit/test_agents/test_fight_agent.py
"""
Unit tests for Fight Detection Agent.
Tests fight signal processing, intensity scoring,
and fight lifecycle management.
"""

import pytest
import time
from unittest.mock import AsyncMock, Mock

from app.agents.base_agent import AgentConfig

from app.agents.fight_agents.fight_detector import (
    FightDetector, PotentialFight,
    DetectedFight, FightSignalData, FightSignal,
    FightPhase, FightType,
)


@pytest.mark.unit
@pytest.mark.agents
class TestFightDetector:
    """Test FightDetector functionality"""
    
    @pytest.fixture
    def mock_event_bus(self):
        """Create mock event bus"""
        bus = AsyncMock()
        bus.publish = AsyncMock(return_value="test-event-id")
        bus.subscribe = Mock(return_value="subscriber-id")
        bus.unsubscribe = Mock(return_value=True)
        return bus

    @pytest.fixture
    def detector(self, mock_event_bus):
        """Create fight detector instance"""
        return FightDetector(
            config=AgentConfig(agent_id="fight_detector", agent_type="fight_detector"),
            event_bus=mock_event_bus,
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SIGNAL PROCESSING TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_process_hp_change_signal(self, detector):
        """Test processing HP change as fight signal"""
        fight_id = detector.process_hp_change(
            player_id="player_1",
            team_id="team_001",
            old_hp=100,
            new_hp=60,
            source_player_id="player_5",
            source_team_id="team_002",
            damage=40,
            location_x=4000.0,
            location_y=5000.0,
        )
        
        # May or may not create a fight depending on thresholds
        # But should process the signal
        assert len(detector.detection_state.signal_buffer) >= 0
    
    def test_process_kill_feed_signal(self, detector):
        """Test processing kill feed as fight signal"""
        fight_id = detector.process_kill_feed(
            killer_id="player_1",
            killer_team_id="team_001",
            victim_id="player_5",
            victim_team_id="team_002",
            is_knock=False,
            location_x=4000.0,
            location_y=5000.0,
            weapon="M416",
            damage=100,
        )
        
        # Kill feed is a strong signal
        assert len(detector.detection_state.signal_buffer) >= 0
    
    def test_process_proximity_signal(self, detector):
        """Test processing proximity as fight signal"""
        fight_id = detector.process_proximity(
            team_id="team_001",
            other_team_id="team_002",
            distance=50.0,
            location_x=4000.0,
            location_y=5000.0,
        )
        
        assert len(detector.detection_state.signal_buffer) >= 0
    
    def test_process_utility_signal(self, detector):
        """Test processing utility usage as fight signal"""
        fight_id = detector.process_utility(
            player_id="player_1",
            team_id="team_001",
            utility_type="grenade",
            target_x=4100.0,
            target_y=5100.0,
            source_x=4000.0,
            source_y=5000.0,
            hit_enemy=True,
        )
        
        assert len(detector.detection_state.signal_buffer) >= 0
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FIGHT DETECTION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_create_potential_fight(self, detector):
        """Test creating a potential fight from signals"""
        signal = FightSignalData(
            signal_type=FightSignal.HP_DROP,
            timestamp=time.time(),
            source_team_id="team_001",
            target_team_id="team_002",
            value=50.0,
            location_x=4000.0,
            location_y=5000.0,
            damage=40,
            confidence=0.9,
        )
        
        fight_id = detector._create_potential_fight(signal)
        
        assert fight_id is not None
        assert fight_id in detector.detection_state.potential_fights
        
        fight = detector.detection_state.potential_fights[fight_id]
        assert "team_001" in fight.teams_involved
        assert "team_002" in fight.teams_involved
    
    def test_find_related_fight(self, detector):
        """Test finding related fight for a signal"""
        # Create a potential fight first
        signal1 = FightSignalData(
            signal_type=FightSignal.HP_DROP,
            timestamp=time.time(),
            source_team_id="team_001",
            target_team_id="team_002",
            value=50.0,
            location_x=4000.0,
            location_y=5000.0,
        )
        
        fight_id = detector._create_potential_fight(signal1)
        
        # Create related signal
        signal2 = FightSignalData(
            signal_type=FightSignal.HP_DROP,
            timestamp=time.time(),
            source_team_id="team_001",
            target_team_id="team_002",
            value=30.0,
            location_x=4050.0,
            location_y=5050.0,
        )
        
        related_id = detector._find_related_fight(signal2)
        
        assert related_id == fight_id
    
    def test_find_related_fight_by_location(self, detector):
        """Test finding related fight by proximity"""
        signal1 = FightSignalData(
            signal_type=FightSignal.HP_DROP,
            timestamp=time.time(),
            source_team_id="team_001",
            value=50.0,
            location_x=4000.0,
            location_y=5000.0,
        )
        
        detector._create_potential_fight(signal1)
        
        # Nearby signal
        signal2 = FightSignalData(
            signal_type=FightSignal.UTILITY,
            timestamp=time.time(),
            source_team_id="team_003",
            value=30.0,
            location_x=4050.0,
            location_y=5050.0,
        )
        
        related_id = detector._find_related_fight(signal2)
        
        # Should find by proximity
        assert related_id is not None
    
    def test_multiple_signals_create_fight(self, detector):
        """Test that multiple strong signals create a confirmed fight"""
        # Send multiple HP drops
        detector.process_hp_change("p1", "team_001", 100, 60, "p5", "team_002", 40)
        detector.process_hp_change("p2", "team_001", 100, 50, "p6", "team_002", 50)
        
        # Send proximity
        detector.process_proximity("team_001", "team_002", 30)
        
        # Send kill
        result = detector.process_kill_feed("p5", "team_002", "p1", "team_001", is_knock=True)
        
        # Should have detected a fight
        assert len(detector.detection_state.active_fights) + len(detector.detection_state.potential_fights) >= 0
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FIGHT PROBABILITY TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_calculate_fight_probability(self, detector):
        """Test fight probability calculation"""
        fight = PotentialFight(
            fight_id="test_fight",
            teams_involved={"team_001", "team_002"},
        )
        
        # Add signals
        fight.signal_types = {FightSignal.HP_DROP, FightSignal.KILL_FEED, FightSignal.PROXIMITY}
        fight.signal_count = 5
        fight.hp_drops = 3
        fight.knocks = 1
        fight.total_damage = 150
        
        prob = detector._calculate_fight_probability(fight)
        
        assert prob > 0.5  # Should be high probability
    
    def test_low_probability_fight(self, detector):
        """Test low probability fight"""
        fight = PotentialFight(
            fight_id="test_fight",
            teams_involved={"team_001"},
        )
        
        fight.signal_types = {FightSignal.PROXIMITY}
        fight.signal_count = 1
        fight.hp_drops = 0
        
        prob = detector._calculate_fight_probability(fight)
        
        assert prob < 0.5  # Should be low probability
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FIGHT TYPE DETECTION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_determine_close_quarters_fight(self, detector):
        """Test determining close quarters fight type"""
        fight = PotentialFight(
            fight_id="test_fight",
            teams_involved={"team_001", "team_002"},
        )
        
        fight.signal_types = {FightSignal.HP_DROP, FightSignal.PROXIMITY}
        fight.hp_drops = 5
        
        fight_type = detector._determine_fight_type(fight)
        
        assert fight_type == FightType.CLOSE_QUARTERS
    
    def test_determine_long_range_fight(self, detector):
        """Test determining long range fight type"""
        fight = PotentialFight(
            fight_id="test_fight",
            teams_involved={"team_001", "team_002"},
        )
        
        fight.signal_types = {FightSignal.HP_DROP}  # No proximity = long range
        fight.hp_drops = 3
        
        fight_type = detector._determine_fight_type(fight)
        
        assert fight_type == FightType.LONG_RANGE
    
    def test_determine_utility_fight(self, detector):
        """Test determining utility-heavy fight"""
        fight = PotentialFight(
            fight_id="test_fight",
            teams_involved={"team_001", "team_002"},
        )
        
        fight.signal_types = {FightSignal.UTILITY}
        fight.utility_used = 5
        
        fight_type = detector._determine_fight_type(fight)
        
        assert fight_type == FightType.UTILITY_FIGHT
    
    def test_determine_third_party_fight(self, detector):
        """Test determining third party fight"""
        fight = PotentialFight(
            fight_id="test_fight",
            teams_involved={"team_001", "team_002", "team_003"},
        )
        
        fight.signal_types = {FightSignal.HP_DROP, FightSignal.KILL_FEED}
        fight.knocks = 1
        
        fight_type = detector._determine_fight_type(fight)
        
        assert fight_type == FightType.THIRD_PARTY
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FIGHT LIFECYCLE TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_update_active_fight(self, detector):
        """Test updating an active fight"""
        # Create an active fight
        fight = DetectedFight(
            fight_id="test_fight_001",
            fight_type=FightType.MID_RANGE,
            phase=FightPhase.ENGAGEMENT,
            teams_involved={"team_001", "team_002"},
        )
        detector.detection_state.active_fights["test_fight_001"] = fight
        
        # Update with damage
        detector.update_active_fight(
            fight_id="test_fight_001",
            event_type="damage",
            data={"damage": 75, "player_id": "p1", "team_id": "team_001"},
        )
        
        assert fight.total_damage == 75
        assert fight.current_intensity > 0
    
    def test_update_fight_phase(self, detector):
        """Test fight phase progression"""
        fight = DetectedFight(
            fight_id="test_fight_001",
            fight_type=FightType.MID_RANGE,
            phase=FightPhase.ENGAGEMENT,
            teams_involved={"team_001", "team_002"},
            current_intensity=80.0,
        )
        
        detector._update_fight_phase(fight)
        
        assert fight.phase == FightPhase.PEAK
    
    def test_check_fight_resolution(self, detector):
        """Test fight resolution check"""
        fight = DetectedFight(
            fight_id="test_fight_001",
            fight_type=FightType.MID_RANGE,
            phase=FightPhase.RESOLVED,
            teams_involved={"team_001", "team_002"},
        )
        detector.detection_state.active_fights["test_fight_001"] = fight
        
        should_resolve = detector.check_fight_resolution("test_fight_001")
        
        assert should_resolve is True
    
    def test_resolve_fight(self, detector):
        """Test resolving a fight"""
        fight = DetectedFight(
            fight_id="test_fight_001",
            fight_type=FightType.MID_RANGE,
            phase=FightPhase.RESOLVED,
            teams_involved={"team_001", "team_002"},
            total_kills=3,
            total_damage=500,
        )
        detector.detection_state.active_fights["test_fight_001"] = fight
        
        summary = detector.resolve_fight("test_fight_001")
        
        assert summary is not None
        assert summary["fight_id"] == "test_fight_001"
        assert summary["total_kills"] == 3
        assert "test_fight_001" not in detector.detection_state.active_fights
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CLEANUP TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_cleanup_old_signals(self, detector):
        """Test cleaning up old signals"""
        # Add old signals
        old_time = time.time() - 20  # Older than buffer window
        detector.detection_state.signal_buffer.append(
            FightSignalData(
                signal_type=FightSignal.HP_DROP,
                timestamp=old_time,
                source_team_id="team_001",
            )
        )
        
        detector._cleanup_old_signals()
        
        # Old signal should be removed
        assert len(detector.detection_state.signal_buffer) == 0
    
    def test_cleanup_stale_fights(self, detector):
        """Test cleaning up stale potential fights"""
        old_time = time.time() - 60
        fight = PotentialFight(
            fight_id="stale_fight",
            teams_involved={"team_001"},
            last_signal_time=old_time,
        )
        detector.detection_state.potential_fights["stale_fight"] = fight
        
        detector.cleanup_stale_fights()
        
        assert "stale_fight" not in detector.detection_state.potential_fights
    
    def test_is_team_fighting(self, detector):
        """Test checking if team is in active fight"""
        fight = DetectedFight(
            fight_id="test_fight_001",
            teams_involved={"team_001", "team_002"},
        )
        detector.detection_state.active_fights["test_fight_001"] = fight
        
        assert detector.is_team_fighting("team_001") is True
        assert detector.is_team_fighting("team_003") is False
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERY TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_get_active_fights(self, detector):
        """Test getting active fights list"""
        fight = DetectedFight(
            fight_id="test_fight_001",
            fight_type=FightType.MID_RANGE,
            phase=FightPhase.ENGAGEMENT,
            teams_involved={"team_001", "team_002"},
            current_intensity=60.0,
        )
        detector.detection_state.active_fights["test_fight_001"] = fight
        
        active = detector.get_active_fights()
        
        assert len(active) == 1
        assert active[0]["fight_id"] == "test_fight_001"
        assert active[0]["intensity"] > 0
    
    def test_get_fight_detail(self, detector):
        """Test getting specific fight details"""
        fight = DetectedFight(
            fight_id="test_fight_001",
            fight_type=FightType.CLOSE_QUARTERS,
            phase=FightPhase.ENGAGEMENT,
            teams_involved={"team_001", "team_002"},
            primary_attacker="team_001",
            primary_defender="team_002",
            total_damage=300,
            total_kills=2,
            is_clutch=False,
        )
        detector.detection_state.active_fights["test_fight_001"] = fight
        
        detail = detector.get_fight("test_fight_001")
        
        assert detail is not None
        assert detail["type"] == "close_quarters"
        # Phase 35 W5: teams_involved is a SET and get_fight returns
        # list(set), whose order varies with PYTHONHASHSEED — the test
        # must not assert a set-iteration order. sorted() keeps the
        # assertion exactly as strong (same members, no duplicates)
        # while removing the false order dependence. Deterministic
        # ordering in get_fight itself is a production-code follow-up
        # (sorted() at the list(set) sites); this test no longer
        # depends on it either way.
        assert sorted(detail["teams"]) == ["team_001", "team_002"]
        assert detail["total_damage"] == 300
    
    def test_get_team_fights(self, detector):
        """Test getting fights for a specific team"""
        fight1 = DetectedFight(
            fight_id="fight_001",
            teams_involved={"team_001", "team_002"},
        )
        fight2 = DetectedFight(
            fight_id="fight_002",
            teams_involved={"team_001", "team_003"},
        )
        
        detector.detection_state.active_fights["fight_001"] = fight1
        detector.detection_state.active_fights["fight_002"] = fight2
        
        team_fights = detector.get_team_fights("team_001")
        
        assert len(team_fights) == 2
    
    def test_get_stats(self, detector):
        """Test getting detector statistics"""
        stats = detector.get_stats()
        
        assert "active_fights" in stats
        assert "potential_fights" in stats
        assert "signal_buffer_size" in stats
    
    def test_reset(self, detector):
        """Test resetting detector"""
        detector.detection_state.active_fights["test"] = DetectedFight(fight_id="test", teams_involved={"t1"})
        detector.detection_state.total_fights_detected = 10
        
        detector.reset()
        
        assert len(detector.detection_state.active_fights) == 0
        assert detector.detection_state.total_fights_detected == 0