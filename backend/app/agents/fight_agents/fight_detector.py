# backend/app/agents/fight_agents/fight_detector.py
"""
AEGIS AI - Fight Detector
Real-time fight detection using multiple signals:
HP changes, kill feed, player proximity, utility usage, audio cues
"""

import time
import math
from typing import Dict, List, Optional, Any, Set, Deque
from dataclasses import dataclass, field
from collections import deque
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.agents.fight_detector")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & CONSTANTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FightPhase(str, Enum):
    """Phases of a fight"""
    INITIATION = "initiation"       # First shots fired
    ENGAGEMENT = "engagement"       # Active fighting
    PEAK = "peak"                  # Maximum intensity
    WINDING_DOWN = "winding_down"  # Fight slowing
    RESOLVED = "resolved"          # Fight complete
    STALEMATE = "stalemate"        # Neither side advancing

class FightType(str, Enum):
    """Types of fights"""
    CLOSE_QUARTERS = "close_quarters"    # Building/compound fight
    MID_RANGE = "mid_range"             # 25-100m engagement
    LONG_RANGE = "long_range"           # 100-300m sniping
    THIRD_PARTY = "third_party"         # Third team joins
    AMBUSH = "ambush"                   # Surprise attack
    HOLD_AND_DEFEND = "hold_and_defend" # Defensive position
    PUSH = "push"                       # Aggressive push
    RETREAT = "retreat"                 # Fighting withdrawal
    CLUTCH = "clutch"                   # 1vX situation
    UTILITY_FIGHT = "utility_fight"     # Heavy grenade/smoke usage

class FightSignal(str, Enum):
    """Signals used for fight detection"""
    HP_DROP = "hp_drop"
    KILL_FEED = "kill_feed"
    PROXIMITY = "proximity"
    UTILITY = "utility"
    SHOOTING = "shooting"
    MOVEMENT = "movement"
    KNOCK = "knock"
    HEALING = "healing"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class FightSignalData:
    """Data for a single fight detection signal"""
    signal_type: FightSignal
    timestamp: float
    source_team_id: str
    target_team_id: Optional[str] = None
    source_player_id: Optional[str] = None
    target_player_id: Optional[str] = None
    value: float = 0.0  # Signal strength
    location_x: float = 0.0
    location_y: float = 0.0
    damage: int = 0
    weapon: Optional[str] = None
    confidence: float = 1.0

@dataclass
class FightDetectionState:
    """State for ongoing fight detection"""
    # Potential fights being tracked
    potential_fights: Dict[str, 'PotentialFight'] = field(default_factory=dict)
    
    # Active fights
    active_fights: Dict[str, 'DetectedFight'] = field(default_factory=dict)
    
    # Recent signals buffer
    signal_buffer: Deque[FightSignalData] = field(default_factory=lambda: deque(maxlen=500))
    
    # Team engagement tracking
    team_engagements: Dict[str, Set[str]] = field(default_factory=dict)  # team_id -> {engaged_teams}
    
    # Cooldown tracking
    team_fight_cooldowns: Dict[str, float] = field(default_factory=dict)
    
    # Statistics
    total_fights_detected: int = 0
    false_positives: int = 0
    missed_fights: int = 0
    average_detection_time_ms: float = 0.0

@dataclass
class PotentialFight:
    """A potential fight being evaluated"""
    fight_id: str
    teams_involved: Set[str] = field(default_factory=set)
    first_signal_time: float = field(default_factory=time.time)
    last_signal_time: float = field(default_factory=time.time)
    signal_count: int = 0
    signal_types: Set[FightSignal] = field(default_factory=set)
    
    # Accumulated evidence
    hp_drops: int = 0
    total_damage: int = 0
    knocks: int = 0
    kills: int = 0
    utility_used: int = 0
    proximity_events: int = 0
    
    # Location
    location_x: float = 0.0
    location_y: float = 0.0
    location_confidence: float = 0.0
    
    # Confidence
    fight_probability: float = 0.0
    detection_confidence: float = 0.0
    
    def should_promote(self) -> bool:
        """Check if potential fight should be promoted to active"""
        # Need multiple signals for confirmation
        if len(self.signal_types) < 2:
            return False
        
        # Need sufficient evidence
        if self.signal_count < 3:
            return False
        
        # High confidence threshold
        if self.fight_probability > 0.7:
            return True
        
        # HP drops + proximity = almost certainly a fight
        if (FightSignal.HP_DROP in self.signal_types and 
            FightSignal.PROXIMITY in self.signal_types and
            self.hp_drops >= 2):
            return True
        
        # Kill/knock is definitive
        if self.kills > 0 or self.knocks > 0:
            return True
        
        return False

@dataclass
class DetectedFight:
    """A confirmed active fight"""
    fight_id: str
    fight_type: FightType = FightType.MID_RANGE
    phase: FightPhase = FightPhase.INITIATION
    
    # Teams
    teams_involved: Set[str] = field(default_factory=set)
    primary_attacker: Optional[str] = None
    primary_defender: Optional[str] = None
    
    # Timing
    detected_at: float = field(default_factory=time.time)
    phase_started_at: float = field(default_factory=time.time)
    last_activity_at: float = field(default_factory=time.time)
    
    # Location
    location_x: float = 0.0
    location_y: float = 0.0
    location_name: Optional[str] = None
    zone_phase: int = 0
    
    # Statistics
    total_damage: int = 0
    total_kills: int = 0
    total_knocks: int = 0
    total_revives: int = 0
    total_utility: int = 0
    
    # Intensity
    current_intensity: float = 0.0
    peak_intensity: float = 0.0
    
    # Players
    players_involved: Dict[str, str] = field(default_factory=dict)  # player_id -> team_id
    
    # Events
    event_ids: List[str] = field(default_factory=list)
    
    # Prediction
    predicted_winner: Optional[str] = None
    predicted_duration: float = 0.0
    is_clutch: bool = False
    
    def get_duration(self) -> float:
        """Get fight duration in seconds"""
        return time.time() - self.detected_at
    
    def get_inactivity_time(self) -> float:
        """Get time since last activity"""
        return time.time() - self.last_activity_at


class FightDetector:
    """
    Real-time fight detection engine.
    
    Detects fights using multiple signal types:
    1. HP drops - Rapid health decrease
    2. Kill feed - Kills and knocks
    3. Proximity - Teams within engagement range
    4. Utility usage - Grenades, smokes, molotovs
    5. Shooting patterns - Sustained firing
    6. Movement patterns - Aggressive positioning
    7. Healing patterns - Combat healing
    
    Uses a confidence-based system:
    - Potential fights: Being evaluated
    - Confirmed fights: Multiple signals verified
    - Fight phases: initiation → engagement → peak → winding down → resolved
    """
    
    def __init__(self):
        self.state = FightDetectionState()
        
        # Detection thresholds
        self.thresholds = {
            "hp_drop_min_damage": 20,        # Minimum HP drop to consider
            "hp_drop_window_seconds": 2.0,    # Time window for rapid HP drops
            "proximity_max_distance": 150,    # Max distance for proximity signal (meters)
            "signal_buffer_window": 10.0,     # How long to keep signals
            "fight_cooldown_seconds": 8.0,    # Cooldown between fights for same team
            "promotion_confidence": 0.7,      # Confidence to promote potential fight
            "stalemate_timeout": 30.0,        # Time before stalemate
            "resolution_inactivity": 8.0,      # Inactivity before fight resolved
            "min_fight_duration": 2.0,         # Minimum fight duration
        }
        
        # Signal weights for confidence calculation
        self.signal_weights = {
            FightSignal.KILL_FEED: 0.40,    # Kill/knock is very strong signal
            FightSignal.HP_DROP: 0.25,      # HP changes are strong
            FightSignal.PROXIMITY: 0.15,    # Proximity is moderate
            FightSignal.UTILITY: 0.10,      # Utility usage is weaker
            FightSignal.SHOOTING: 0.05,     # Shooting alone is weak
            FightSignal.MOVEMENT: 0.03,     # Movement is weakest
            FightSignal.HEALING: 0.02,      # Healing is contextual
        }
        
        self._log = logger.bind(component="fight_detector")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SIGNAL PROCESSING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def process_signal(self, signal: FightSignalData) -> Optional[str]:
        """
        Process a fight detection signal.
        Returns fight_id if a new fight is detected.
        """
        
        # Add to buffer
        self.state.signal_buffer.append(signal)
        
        # Clean old signals
        self._cleanup_old_signals()
        
        # Check cooldown
        if self._is_in_cooldown(signal.source_team_id):
            return None
        
        # Find or create potential fight
        fight_id = self._find_related_fight(signal)
        
        if not fight_id:
            fight_id = self._create_potential_fight(signal)
        
        # Update potential fight
        fight = self.state.potential_fights.get(fight_id)
        if fight:
            self._update_potential_fight(fight, signal)
            
            # Check if should promote to active fight
            if fight.should_promote():
                return self._promote_to_active(fight_id)
        
        return None
    
    def process_hp_change(
        self,
        player_id: str,
        team_id: str,
        old_hp: int,
        new_hp: int,
        source_player_id: Optional[str] = None,
        source_team_id: Optional[str] = None,
        location_x: float = 0.0,
        location_y: float = 0.0,
        weapon: Optional[str] = None,
    ) -> Optional[str]:
        """Process HP change as a fight signal"""
        damage = old_hp - new_hp
        
        if damage < self.thresholds["hp_drop_min_damage"]:
            return None
        
        signal = FightSignalData(
            signal_type=FightSignal.HP_DROP,
            timestamp=time.time(),
            source_team_id=team_id,
            target_team_id=source_team_id,
            source_player_id=player_id,
            target_player_id=source_player_id,
            value=damage,
            location_x=location_x,
            location_y=location_y,
            damage=damage,
            weapon=weapon,
            confidence=0.9 if source_team_id else 0.5,
        )
        
        return self.process_signal(signal)
    
    def process_kill_feed(
        self,
        killer_id: str,
        killer_team_id: str,
        victim_id: str,
        victim_team_id: str,
        is_knock: bool = False,
        location_x: float = 0.0,
        location_y: float = 0.0,
        weapon: Optional[str] = None,
        damage: int = 100,
    ) -> Optional[str]:
        """Process kill feed event as a fight signal"""
        
        signal = FightSignalData(
            signal_type=FightSignal.KILL_FEED,
            timestamp=time.time(),
            source_team_id=killer_team_id,
            target_team_id=victim_team_id,
            source_player_id=killer_id,
            target_player_id=victim_id,
            value=100.0 if not is_knock else 60.0,
            location_x=location_x,
            location_y=location_y,
            damage=damage,
            weapon=weapon,
            confidence=1.0,  # Kill feed is definitive
        )
        
        return self.process_signal(signal)
    
    def process_proximity(
        self,
        team_id: str,
        other_team_id: str,
        distance: float,
        location_x: float = 0.0,
        location_y: float = 0.0,
    ) -> Optional[str]:
        """Process team proximity as a fight signal"""
        
        if distance > self.thresholds["proximity_max_distance"]:
            return None
        
        # Closer = stronger signal
        proximity_strength = max(0, 1.0 - (distance / self.thresholds["proximity_max_distance"]))
        
        signal = FightSignalData(
            signal_type=FightSignal.PROXIMITY,
            timestamp=time.time(),
            source_team_id=team_id,
            target_team_id=other_team_id,
            value=proximity_strength * 100,
            location_x=location_x,
            location_y=location_y,
            confidence=proximity_strength * 0.8,
        )
        
        return self.process_signal(signal)
    
    def process_utility(
        self,
        player_id: str,
        team_id: str,
        utility_type: str,
        target_x: float = 0.0,
        target_y: float = 0.0,
        source_x: float = 0.0,
        source_y: float = 0.0,
        hit_enemy: bool = False,
    ) -> Optional[str]:
        """Process utility usage as a fight signal"""
        
        # Offensive utility (grenade, molotov) is stronger signal
        is_offensive = utility_type in ["grenade", "molotov"]
        
        signal = FightSignalData(
            signal_type=FightSignal.UTILITY,
            timestamp=time.time(),
            source_team_id=team_id,
            value=80.0 if (is_offensive and hit_enemy) else 30.0,
            location_x=source_x,
            location_y=source_y,
            confidence=0.8 if is_offensive else 0.4,
        )
        
        return self.process_signal(signal)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FIGHT MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _find_related_fight(self, signal: FightSignalData) -> Optional[str]:
        """Find existing fight related to this signal"""
        
        # Check active fights first
        for fight_id, fight in self.state.active_fights.items():
            if signal.source_team_id in fight.teams_involved:
                return fight_id
            if signal.target_team_id and signal.target_team_id in fight.teams_involved:
                return fight_id
        
        # Check potential fights
        for fight_id, fight in self.state.potential_fights.items():
            if signal.source_team_id in fight.teams_involved:
                return fight_id
            if signal.target_team_id and signal.target_team_id in fight.teams_involved:
                return fight_id
        
        # Check for nearby fights by location
        if signal.location_x and signal.location_y:
            for fight_id, fight in self.state.potential_fights.items():
                dist = math.sqrt(
                    (signal.location_x - fight.location_x) ** 2 +
                    (signal.location_y - fight.location_y) ** 2
                )
                if dist < 100:  # Within 100 meters
                    return fight_id
        
        return None
    
    def _create_potential_fight(self, signal: FightSignalData) -> str:
        """Create a new potential fight"""
        fight_id = f"potential_fight_{int(time.time() * 1000)}_{signal.source_team_id[:8]}"
        
        fight = PotentialFight(
            fight_id=fight_id,
            location_x=signal.location_x,
            location_y=signal.location_y,
        )
        
        fight.teams_involved.add(signal.source_team_id)
        if signal.target_team_id:
            fight.teams_involved.add(signal.target_team_id)
        
        self.state.potential_fights[fight_id] = fight
        
        self._log.debug(f"New potential fight: {fight_id} | Teams: {fight.teams_involved}")
        
        return fight_id
    
    def _update_potential_fight(
        self,
        fight: PotentialFight,
        signal: FightSignalData,
    ) -> None:
        """Update a potential fight with new signal"""
        
        fight.last_signal_time = time.time()
        fight.signal_count += 1
        fight.signal_types.add(signal.signal_type)
        fight.teams_involved.add(signal.source_team_id)
        
        if signal.target_team_id:
            fight.teams_involved.add(signal.target_team_id)
        
        # Accumulate evidence
        if signal.signal_type == FightSignal.HP_DROP:
            fight.hp_drops += 1
            fight.total_damage += signal.damage
        elif signal.signal_type == FightSignal.KILL_FEED:
            if signal.value >= 100:
                fight.kills += 1
            else:
                fight.knocks += 1
        elif signal.signal_type == FightSignal.PROXIMITY:
            fight.proximity_events += 1
        elif signal.signal_type == FightSignal.UTILITY:
            fight.utility_used += 1
        
        # Update location (weighted average)
        if signal.location_x and signal.location_y:
            if fight.location_confidence < 0.1:
                fight.location_x = signal.location_x
                fight.location_y = signal.location_y
            else:
                weight = signal.confidence
                fight.location_x = (
                    fight.location_x * fight.location_confidence +
                    signal.location_x * weight
                ) / (fight.location_confidence + weight)
                fight.location_y = (
                    fight.location_y * fight.location_confidence +
                    signal.location_y * weight
                ) / (fight.location_confidence + weight)
            
            fight.location_confidence = min(1.0, fight.location_confidence + 0.1)
        
        # Calculate fight probability
        fight.fight_probability = self._calculate_fight_probability(fight)
        fight.detection_confidence = min(1.0, fight.signal_count / 10)
    
    def _calculate_fight_probability(self, fight: PotentialFight) -> float:
        """Calculate probability that this is a real fight"""
        probability = 0.0
        total_weight = 0.0
        
        for signal_type in fight.signal_types:
            weight = self.signal_weights.get(signal_type, 0.1)
            total_weight += weight
        
        if total_weight == 0:
            return 0.0
        
        # Base probability from signal types present
        probability = total_weight
        
        # Boost from multiple signals
        if len(fight.signal_types) >= 3:
            probability += 0.2
        
        # Boost from damage
        if fight.total_damage > 50:
            probability += 0.1
        if fight.total_damage > 150:
            probability += 0.1
        
        # Boost from kills/knocks
        if fight.kills > 0:
            probability += 0.3
        elif fight.knocks > 0:
            probability += 0.2
        
        # Boost from sustained activity
        duration = time.time() - fight.first_signal_time
        if duration > 3 and fight.signal_count >= 5:
            probability += 0.1
        
        # Multiple teams = more likely a fight
        if len(fight.teams_involved) >= 3:
            probability += 0.1
        
        return min(1.0, probability)
    
    def _promote_to_active(self, fight_id: str) -> str:
        """Promote a potential fight to an active fight"""
        
        potential = self.state.potential_fights.pop(fight_id)
        
        # Generate active fight ID
        active_fight_id = f"fight_{int(time.time())}_{list(potential.teams_involved)[0][:8]}"
        
        # Determine fight type
        fight_type = self._determine_fight_type(potential)
        
        # Create active fight
        active_fight = DetectedFight(
            fight_id=active_fight_id,
            fight_type=fight_type,
            phase=FightPhase.ENGAGEMENT,
            teams_involved=potential.teams_involved,
            detected_at=time.time(),
            phase_started_at=time.time(),
            last_activity_at=time.time(),
            location_x=potential.location_x,
            location_y=potential.location_y,
            total_damage=potential.total_damage,
            total_kills=potential.kills,
            total_knocks=potential.knocks,
            total_utility=potential.utility_used,
            current_intensity=self._calculate_initial_intensity(potential),
            peak_intensity=0.0,
        )
        
        # Set primary attacker/defender
        if len(potential.teams_involved) >= 2:
            teams = list(potential.teams_involved)
            active_fight.primary_attacker = teams[0]
            active_fight.primary_defender = teams[1]
        
        self.state.active_fights[active_fight_id] = active_fight
        self.state.total_fights_detected += 1
        
        # Set cooldowns
        for team_id in potential.teams_involved:
            self.state.team_fight_cooldowns[team_id] = time.time()
            self.state.team_engagements.setdefault(team_id, set())
        
        detection_time = (time.time() - potential.first_signal_time) * 1000
        n = self.state.total_fights_detected
        self.state.average_detection_time_ms = (
            self.state.average_detection_time_ms * (n - 1) + detection_time
        ) / n
        
        self._log.warning(
            f"⚔️ FIGHT DETECTED: {active_fight_id} | "
            f"Type: {fight_type.value} | "
            f"Teams: {active_fight.teams_involved} | "
            f"Detection: {detection_time:.0f}ms | "
            f"Confidence: {potential.fight_probability:.2f}"
        )
        
        return active_fight_id
    
    def _determine_fight_type(self, fight: PotentialFight) -> FightType:
        """Determine the type of fight"""
        
        # Check for clutch situation
        if fight.knocks > 0 and len(fight.teams_involved) >= 2:
            # If one team has taken significant damage
            pass
        
        # Check utility usage
        if fight.utility_used >= 3:
            return FightType.UTILITY_FIGHT
        
        # Check for close quarters (multiple HP drops, proximity)
        if (FightSignal.HP_DROP in fight.signal_types and 
            FightSignal.PROXIMITY in fight.signal_types and
            fight.hp_drops >= 3):
            return FightType.CLOSE_QUARTERS
        
        # Check for long range (HP drops without proximity)
        if (FightSignal.HP_DROP in fight.signal_types and 
            FightSignal.PROXIMITY not in fight.signal_types):
            return FightType.LONG_RANGE
        
        # Check for ambush (sudden kill/knock)
        if fight.knocks > 0 or fight.kills > 0:
            if fight.signal_count < 5:
                return FightType.AMBUSH
        
        # Check for third party
        if len(fight.teams_involved) >= 3:
            return FightType.THIRD_PARTY
        
        return FightType.MID_RANGE
    
    def _calculate_initial_intensity(self, fight: PotentialFight) -> float:
        """Calculate initial fight intensity"""
        intensity = 30.0  # Base
        
        # Damage-based
        intensity += min(40, fight.total_damage * 0.3)
        
        # Kill-based
        intensity += fight.kills * 20
        intensity += fight.knocks * 10
        
        # Utility
        intensity += fight.utility_used * 5
        
        # Team count
        intensity += (len(fight.teams_involved) - 2) * 10
        
        # Signal density
        duration = max(1, time.time() - fight.first_signal_time)
        signals_per_second = fight.signal_count / duration
        intensity += min(20, signals_per_second * 20)
        
        return min(100, intensity)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FIGHT UPDATES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def update_active_fight(
        self,
        fight_id: str,
        event_type: str,
        data: Dict[str, Any],
    ) -> None:
        """Update an active fight with new events"""
        
        fight = self.state.active_fights.get(fight_id)
        if not fight:
            return
        
        fight.last_activity_at = time.time()
        
        if event_type == "damage":
            fight.total_damage += data.get("damage", 0)
            fight.current_intensity = min(100, fight.current_intensity + data.get("damage", 0) * 0.2)
        
        elif event_type == "knock":
            fight.total_knocks += 1
            fight.current_intensity = min(100, fight.current_intensity + 10)
        
        elif event_type == "kill":
            fight.total_kills += 1
            fight.current_intensity = min(100, fight.current_intensity + 15)
        
        elif event_type == "revive":
            fight.total_revives += 1
        
        elif event_type == "utility":
            fight.total_utility += 1
            fight.current_intensity = min(100, fight.current_intensity + 5)
        
        # Update peak intensity
        fight.peak_intensity = max(fight.peak_intensity, fight.current_intensity)
        
        # Update phase
        self._update_fight_phase(fight)
        
        # Track players
        player_id = data.get("player_id")
        team_id = data.get("team_id")
        if player_id and team_id:
            fight.players_involved[player_id] = team_id
    
    def _update_fight_phase(self, fight: DetectedFight) -> None:
        """Update the phase of an active fight"""
        
        duration = fight.get_duration()
        inactivity = fight.get_inactivity_time()
        intensity = fight.current_intensity
        
        if intensity > 70:
            new_phase = FightPhase.PEAK
        elif intensity > 40:
            new_phase = FightPhase.ENGAGEMENT
        elif intensity > 10:
            new_phase = FightPhase.WINDING_DOWN
        elif inactivity > self.thresholds["resolution_inactivity"]:
            new_phase = FightPhase.RESOLVED
        elif duration > self.thresholds["stalemate_timeout"] and intensity < 20:
            new_phase = FightPhase.STALEMATE
        else:
            new_phase = FightPhase.WINDING_DOWN
        
        if new_phase != fight.phase:
            fight.phase = new_phase
            fight.phase_started_at = time.time()
            
            self._log.debug(
                f"Fight {fight.fight_id} phase: {new_phase.value} "
                f"(intensity: {intensity:.0f})"
            )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FIGHT RESOLUTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def check_fight_resolution(self, fight_id: str) -> bool:
        """Check if a fight should be resolved"""
        
        fight = self.state.active_fights.get(fight_id)
        if not fight:
            return False
        
        # Check resolution conditions
        should_resolve = False
        
        if fight.phase == FightPhase.RESOLVED:
            should_resolve = True
        elif fight.phase == FightPhase.STALEMATE and fight.get_inactivity_time() > 15:
            should_resolve = True
        elif fight.get_inactivity_time() > self.thresholds["resolution_inactivity"] * 2:
            should_resolve = True
        elif fight.total_kills >= len(fight.players_involved) * 0.5:
            should_resolve = True
        
        return should_resolve
    
    def resolve_fight(self, fight_id: str) -> Optional[Dict[str, Any]]:
        """Resolve a fight and return summary"""
        
        fight = self.state.active_fights.pop(fight_id, None)
        if not fight:
            return None
        
        duration = fight.get_duration()
        
        summary = {
            "fight_id": fight.fight_id,
            "fight_type": fight.fight_type.value,
            "teams_involved": list(fight.teams_involved),
            "duration_seconds": round(duration, 1),
            "total_damage": fight.total_damage,
            "total_kills": fight.total_kills,
            "total_knocks": fight.total_knocks,
            "total_revives": fight.total_revives,
            "total_utility": fight.total_utility,
            "peak_intensity": round(fight.peak_intensity, 1),
            "location": (round(fight.location_x, 1), round(fight.location_y, 1)),
            "phases": {
                "initiation": fight.detected_at,
                "resolved": time.time(),
            },
            "is_clutch": fight.is_clutch,
        }
        
        self._log.warning(
            f"🏁 FIGHT RESOLVED: {fight.fight_id} | "
            f"Duration: {duration:.1f}s | "
            f"Kills: {fight.total_kills} | "
            f"Peak Intensity: {fight.peak_intensity:.0f}"
        )
        
        return summary
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MAINTENANCE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _cleanup_old_signals(self) -> None:
        """Remove signals older than the buffer window"""
        cutoff = time.time() - self.thresholds["signal_buffer_window"]
        
        while self.state.signal_buffer and self.state.signal_buffer[0].timestamp < cutoff:
            self.state.signal_buffer.popleft()
    
    def _is_in_cooldown(self, team_id: str) -> bool:
        """Check if a team is in fight cooldown"""
        last_fight = self.state.team_fight_cooldowns.get(team_id, 0)
        return (time.time() - last_fight) < self.thresholds["fight_cooldown_seconds"]
    
    def cleanup_stale_fights(self) -> List[str]:
        """Remove stale potential fights and resolved active fights"""
        resolved = []
        
        # Clean potential fights
        stale_potential = []
        for fight_id, fight in self.state.potential_fights.items():
            inactivity = time.time() - fight.last_signal_time
            if inactivity > self.thresholds["signal_buffer_window"] * 3:
                stale_potential.append(fight_id)
        
        for fight_id in stale_potential:
            self.state.potential_fights.pop(fight_id, None)
        
        # Resolve inactive active fights
        for fight_id in list(self.state.active_fights.keys()):
            if self.check_fight_resolution(fight_id):
                summary = self.resolve_fight(fight_id)
                if summary:
                    resolved.append(fight_id)
        
        return resolved
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_active_fights(self) -> List[Dict[str, Any]]:
        """Get all active fights"""
        return [
            {
                "fight_id": f.fight_id,
                "type": f.fight_type.value,
                "phase": f.phase.value,
                "teams": list(f.teams_involved),
                "duration": round(f.get_duration(), 1),
                "intensity": round(f.current_intensity, 1),
                "peak_intensity": round(f.peak_intensity, 1),
                "total_damage": f.total_damage,
                "total_kills": f.total_kills,
                "total_knocks": f.total_knocks,
                "location": (round(f.location_x, 1), round(f.location_y, 1)),
                "is_clutch": f.is_clutch,
            }
            for f in self.state.active_fights.values()
        ]
    
    def get_fight(self, fight_id: str) -> Optional[Dict[str, Any]]:
        """Get specific fight details"""
        fight = self.state.active_fights.get(fight_id)
        if not fight:
            return None
        
        return {
            "fight_id": fight.fight_id,
            "type": fight.fight_type.value,
            "phase": fight.phase.value,
            "teams": list(fight.teams_involved),
            "primary_attacker": fight.primary_attacker,
            "primary_defender": fight.primary_defender,
            "duration": round(fight.get_duration(), 1),
            "intensity": round(fight.current_intensity, 1),
            "peak_intensity": round(fight.peak_intensity, 1),
            "total_damage": fight.total_damage,
            "total_kills": fight.total_kills,
            "total_knocks": fight.total_knocks,
            "total_revives": fight.total_revives,
            "total_utility": fight.total_utility,
            "location": {
                "x": round(fight.location_x, 1),
                "y": round(fight.location_y, 1),
                "name": fight.location_name,
            },
            "zone_phase": fight.zone_phase,
            "players": len(fight.players_involved),
            "is_clutch": fight.is_clutch,
            "predicted_winner": fight.predicted_winner,
        }
    
    def is_team_fighting(self, team_id: str) -> bool:
        """Check if a team is currently in an active fight"""
        for fight in self.state.active_fights.values():
            if team_id in fight.teams_involved:
                return True
        return False
    
    def get_team_fights(self, team_id: str) -> List[Dict[str, Any]]:
        """Get all active fights involving a team"""
        return [
            {
                "fight_id": f.fight_id,
                "opponents": list(f.teams_involved - {team_id}),
                "intensity": round(f.current_intensity, 1),
                "phase": f.phase.value,
                "duration": round(f.get_duration(), 1),
            }
            for f in self.state.active_fights.values()
            if team_id in f.teams_involved
        ]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get detection statistics"""
        return {
            "total_fights_detected": self.state.total_fights_detected,
            "active_fights": len(self.state.active_fights),
            "potential_fights": len(self.state.potential_fights),
            "false_positives": self.state.false_positives,
            "missed_fights": self.state.missed_fights,
            "average_detection_time_ms": round(self.state.average_detection_time_ms, 1),
            "signal_buffer_size": len(self.state.signal_buffer),
        }
    
    def reset(self) -> None:
        """Reset detector state"""
        self.state = FightDetectionState()
        self._log.info("Fight detector reset")