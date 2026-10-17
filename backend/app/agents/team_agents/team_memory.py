# backend/app/agents/team_agents/team_memory.py
"""
AEGIS AI - Team Memory
Long-term behavioral memory storage for teams and players.
Learns patterns, strategies, tendencies over multiple matches.
"""

import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from collections import deque
from enum import Enum
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.team_memory")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MemoryType(str, Enum):
    """Types of memory"""
    SHORT_TERM = "short_term"    # Current match
    MEDIUM_TERM = "medium_term"  # Current tournament
    LONG_TERM = "long_term"      # All time

class StrategyType(str, Enum):
    """Team strategy types"""
    AGGRESSIVE_EARLY = "aggressive_early"
    PASSIVE_EARLY = "passive_early"
    EDGE_ROTATION = "edge_rotation"
    CENTER_CONTROL = "center_control"
    COMPOUND_HOLDING = "compound_holding"
    THIRD_PARTY = "third_party"
    ZONE_CHASING = "zone_chasing"
    HOT_DROP = "hot_drop"
    SAFE_DROP = "safe_drop"
    BALANCED = "balanced"

@dataclass
class LandingSpot:
    """Team's landing spot preference"""
    location_x: float
    location_y: float
    location_name: str
    frequency: int = 0
    success_rate: float = 0.0
    average_placement: float = 0.0
    contested: bool = False
    contested_by: List[str] = field(default_factory=list)
    last_used: float = 0.0

@dataclass
class RotationPattern:
    """Learned rotation pattern"""
    from_location: Tuple[float, float]
    to_location: Tuple[float, float]
    frequency: int = 0
    average_speed: float = 0.0
    zone_phase: int = 0
    success_rate: float = 0.0
    vehicle_used: bool = False
    last_used: float = 0.0

@dataclass
class FightPattern:
    """Learned fight behavior"""
    engagement_distance: float = 0.0
    preferred_range: str = "medium"  # close, medium, long
    aggression_level: float = 5.0  # 1-10
    utility_usage: Dict[str, float] = field(default_factory=dict)
    win_rate: float = 0.0
    average_duration: float = 0.0
    third_party_tendency: float = 0.0
    clutch_success_rate: float = 0.0

@dataclass
class HeadToHead:
    """Head-to-head record between teams"""
    team_id: str
    team_name: str
    encounters: int = 0
    wins: int = 0
    losses: int = 0
    last_encounter: float = 0.0
    last_winner: Optional[str] = None
    average_fight_duration: float = 0.0
    total_kills_against: int = 0
    total_deaths_to: int = 0

@dataclass
class PlayerMemory:
    """Player-specific long-term memory"""
    player_id: str
    player_name: str
    
    # Skills
    mechanical_skill: float = 5.0  # 1-10
    game_sense: float = 5.0
    clutch_ability: float = 5.0
    consistency: float = 5.0
    
    # Preferences
    preferred_weapons: List[str] = field(default_factory=list)
    preferred_range: str = "medium"
    aggressiveness: float = 5.0
    
    # Stats
    total_kills: int = 0
    total_damage: int = 0
    average_survival_time: float = 0.0
    headshot_percentage: float = 0.0
    
    # Patterns
    common_positions: deque = field(default_factory=lambda: deque(maxlen=50))
    flank_tendency: float = 0.0
    solo_push_tendency: float = 0.0
    
    matches_analyzed: int = 0
    last_updated: float = field(default_factory=time.time)

@dataclass
class TeamMemoryStore:
    """Complete team memory"""
    team_id: str
    team_name: str
    
    # Strategy
    primary_strategy: StrategyType = StrategyType.BALANCED
    secondary_strategies: List[StrategyType] = field(default_factory=list)
    strategy_confidence: float = 0.0
    
    # Landing
    landing_spots: List[LandingSpot] = field(default_factory=list)
    preferred_landing: Optional[str] = None
    
    # Rotations
    rotation_patterns: deque = field(default_factory=lambda: deque(maxlen=100))
    
    # Fighting
    fight_pattern: FightPattern = field(default_factory=FightPattern)
    
    # Head-to-head
    head_to_head: Dict[str, HeadToHead] = field(default_factory=dict)
    
    # Rivalries
    rivalries: List[str] = field(default_factory=list)
    
    # Players
    player_memories: Dict[str, PlayerMemory] = field(default_factory=dict)
    
    # Performance
    average_placement: float = 0.0
    win_rate: float = 0.0
    top5_rate: float = 0.0
    average_kills_per_match: float = 0.0
    average_damage_per_match: float = 0.0
    
    # Trends
    performance_trend: deque = field(default_factory=lambda: deque(maxlen=50))
    strategy_evolution: deque = field(default_factory=lambda: deque(maxlen=20))
    
    # Meta
    matches_analyzed: int = 0
    tournaments_analyzed: int = 0
    last_updated: float = field(default_factory=time.time)
    
    # Weaknesses
    known_weaknesses: List[str] = field(default_factory=list)
    exploitable_patterns: List[str] = field(default_factory=list)


class TeamMemory(BaseAgent):
    """
    Long-term memory system for teams and players.
    
    Learns and stores:
    - Landing preferences
    - Rotation patterns
    - Fight behavior and tendencies
    - Head-to-head records
    - Player-specific patterns
    - Strategy evolution
    - Performance trends
    - Known weaknesses
    - Rivalry history
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus, team_id: str, team_name: str):
        super().__init__(config, event_bus)
        self.memory = TeamMemoryStore(
            team_id=team_id,
            team_name=team_name,
        )
        
        # Learning rate for updating averages
        self.learning_rate = 0.1
        
        # Confidence thresholds
        self.min_matches_for_confidence = 5
        self.high_confidence_threshold = 10
        
        self._log = logger.bind(team_name=team_name, team_id=team_id)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # LANDING SPOT LEARNING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def record_landing(
        self,
        location_x: float,
        location_y: float,
        location_name: str,
        match_placement: int = 16,
        was_contested: bool = False,
        contested_by: Optional[str] = None,
    ) -> None:
        """Record a landing spot for learning"""
        
        # Find existing landing spot
        existing = None
        for spot in self.memory.landing_spots:
            dist = ((spot.location_x - location_x) ** 2 + 
                   (spot.location_y - location_y) ** 2) ** 0.5
            if dist < 200:  # Same general area
                existing = spot
                break
        
        if existing:
            existing.frequency += 1
            # Update success rate (good placement = success)
            success = 1 if match_placement <= 8 else 0
            existing.success_rate = (
                existing.success_rate * (existing.frequency - 1) + success
            ) / existing.frequency
            existing.average_placement = (
                existing.average_placement * (existing.frequency - 1) + match_placement
            ) / existing.frequency
            existing.last_used = time.time()
            
            if was_contested and contested_by:
                if contested_by not in existing.contested_by:
                    existing.contested_by.append(contested_by)
                existing.contested = True
        else:
            self.memory.landing_spots.append(LandingSpot(
                location_x=location_x,
                location_y=location_y,
                location_name=location_name,
                frequency=1,
                success_rate=1.0 if match_placement <= 8 else 0.0,
                average_placement=float(match_placement),
                contested=was_contested,
                contested_by=[contested_by] if contested_by else [],
                last_used=time.time(),
            ))
        
        # Update preferred landing
        self._update_preferred_landing()
    
    def _update_preferred_landing(self) -> None:
        """Determine preferred landing spot"""
        if not self.memory.landing_spots:
            return
        
        # Score each landing spot
        best_spot = None
        best_score = -1
        
        for spot in self.memory.landing_spots:
            # Score based on frequency, success rate, and placement
            frequency_weight = min(spot.frequency / 10, 1.0)  # Cap at 10 matches
            success_weight = spot.success_rate
            placement_weight = max(0, (16 - spot.average_placement) / 16)
            
            # Penalty for contested spots
            contested_penalty = 0.7 if spot.contested else 1.0
            
            score = (frequency_weight * 0.4 + success_weight * 0.4 + placement_weight * 0.2) * contested_penalty
            
            if score > best_score:
                best_score = score
                best_spot = spot
        
        if best_spot:
            self.memory.preferred_landing = best_spot.location_name
    
    def get_landing_prediction(self) -> Optional[Dict[str, Any]]:
        """Predict where team will land"""
        if not self.memory.preferred_landing:
            return None
        
        for spot in self.memory.landing_spots:
            if spot.location_name == self.memory.preferred_landing:
                return {
                    "location_name": spot.location_name,
                    "coordinates": (spot.location_x, spot.location_y),
                    "confidence": min(1.0, spot.frequency / 10),
                    "contested": spot.contested,
                    "contested_by": spot.contested_by,
                    "success_rate": round(spot.success_rate * 100, 1),
                }
        
        return None
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ROTATION LEARNING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def record_rotation(
        self,
        from_x: float,
        from_y: float,
        to_x: float,
        to_y: float,
        speed: float,
        zone_phase: int,
        used_vehicle: bool = False,
        was_successful: bool = True,
    ) -> None:
        """Record a rotation for pattern learning"""
        
        self.memory.rotation_patterns.append(RotationPattern(
            from_location=(from_x, from_y),
            to_location=(to_x, to_y),
            frequency=1,
            average_speed=speed,
            zone_phase=zone_phase,
            success_rate=1.0 if was_successful else 0.0,
            vehicle_used=used_vehicle,
            last_used=time.time(),
        ))
        
        # Merge similar patterns
        self._merge_rotation_patterns()
    
    def _merge_rotation_patterns(self) -> None:
        """Merge similar rotation patterns"""
        patterns = list(self.memory.rotation_patterns)
        merged = []
        used = set()
        
        for i, p1 in enumerate(patterns):
            if i in used:
                continue
            
            merged_pattern = RotationPattern(
                from_location=p1.from_location,
                to_location=p1.to_location,
                frequency=p1.frequency,
                average_speed=p1.average_speed,
                zone_phase=p1.zone_phase,
                success_rate=p1.success_rate,
                vehicle_used=p1.vehicle_used,
                last_used=p1.last_used,
            )
            
            for j, p2 in enumerate(patterns[i+1:], start=i+1):
                if j in used:
                    continue
                
                # Check if patterns are similar
                from_dist = ((p1.from_location[0] - p2.from_location[0]) ** 2 +
                            (p1.from_location[1] - p2.from_location[1]) ** 2) ** 0.5
                to_dist = ((p1.to_location[0] - p2.to_location[0]) ** 2 +
                          (p1.to_location[1] - p2.to_location[1]) ** 2) ** 0.5
                
                if from_dist < 500 and to_dist < 500:  # Similar routes
                    merged_pattern.frequency += p2.frequency
                    merged_pattern.average_speed = (
                        merged_pattern.average_speed * merged_pattern.frequency +
                        p2.average_speed * p2.frequency
                    ) / (merged_pattern.frequency + p2.frequency)
                    merged_pattern.success_rate = (
                        merged_pattern.success_rate * merged_pattern.frequency +
                        p2.success_rate * p2.frequency
                    ) / (merged_pattern.frequency + p2.frequency)
                    merged_pattern.last_used = max(merged_pattern.last_used, p2.last_used)
                    used.add(j)
            
            merged.append(merged_pattern)
            used.add(i)
        
        self.memory.rotation_patterns = deque(merged, maxlen=100)
    
    def get_common_rotations(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Get most common rotation patterns"""
        patterns = sorted(
            self.memory.rotation_patterns,
            key=lambda p: p.frequency,
            reverse=True,
        )
        
        return [
            {
                "from": p.from_location,
                "to": p.to_location,
                "frequency": p.frequency,
                "avg_speed": round(p.average_speed, 1),
                "zone_phase": p.zone_phase,
                "success_rate": round(p.success_rate * 100, 1),
                "uses_vehicle": p.vehicle_used,
            }
            for p in patterns[:limit]
        ]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FIGHT PATTERN LEARNING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def record_fight(
        self,
        engagement_distance: float,
        duration: float,
        utility_used: Dict[str, int],
        won: bool,
        enemy_team_id: Optional[str] = None,
        was_third_party: bool = False,
        was_clutch: bool = False,
    ) -> None:
        """Record a fight for pattern learning"""
        fp = self.memory.fight_pattern
        
        # Update engagement distance
        n = self.memory.matches_analyzed or 1
        fp.engagement_distance = (
            fp.engagement_distance * (n - 1) + engagement_distance
        ) / n
        
        # Update preferred range
        if engagement_distance < 25:
            fp.preferred_range = "close"
        elif engagement_distance < 100:
            fp.preferred_range = "medium"
        else:
            fp.preferred_range = "long"
        
        # Update aggression
        aggression_delta = 0.5 if engagement_distance < 25 else -0.3
        fp.aggression_level = max(1, min(10, fp.aggression_level + aggression_delta))
        
        # Update utility usage
        for util_type, count in utility_used.items():
            if util_type not in fp.utility_usage:
                fp.utility_usage[util_type] = 0
            fp.utility_usage[util_type] = (
                fp.utility_usage[util_type] * (n - 1) + count
            ) / n
        
        # Update win rate
        fp.win_rate = (fp.win_rate * (n - 1) + (1 if won else 0)) / n
        
        # Update average duration
        fp.average_duration = (fp.average_duration * (n - 1) + duration) / n
        
        # Update third party tendency
        fp.third_party_tendency = (
            fp.third_party_tendency * (n - 1) + (1 if was_third_party else 0)
        ) / n
        
        # Update clutch success
        if was_clutch:
            fp.clutch_success_rate = (
                fp.clutch_success_rate * (n - 1) + (1 if won else 0)
            ) / n
        
        # Update head-to-head
        if enemy_team_id:
            self._update_head_to_head(enemy_team_id, won, duration)
    
    def _update_head_to_head(
        self,
        enemy_team_id: str,
        won: bool,
        duration: float,
    ) -> None:
        """Update head-to-head record"""
        if enemy_team_id not in self.memory.head_to_head:
            self.memory.head_to_head[enemy_team_id] = HeadToHead(
                team_id=enemy_team_id,
                team_name=f"Team_{enemy_team_id[:8]}",
            )
        
        h2h = self.memory.head_to_head[enemy_team_id]
        h2h.encounters += 1
        h2h.last_encounter = time.time()
        
        if won:
            h2h.wins += 1
            h2h.last_winner = self.memory.team_id
        else:
            h2h.losses += 1
            h2h.last_winner = enemy_team_id
        
        h2h.average_fight_duration = (
            h2h.average_fight_duration * (h2h.encounters - 1) + duration
        ) / h2h.encounters
        
        # Check for rivalry
        if h2h.encounters >= 5:
            if enemy_team_id not in self.memory.rivalries:
                self.memory.rivalries.append(enemy_team_id)
                self._log.info(f"New rivalry detected with {enemy_team_id}")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PLAYER LEARNING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def update_player_memory(
        self,
        player_id: str,
        player_name: str,
        kills: int = 0,
        damage: int = 0,
        survival_time: float = 0,
        headshots: int = 0,
        total_shots: int = 0,
        weapons_used: List[str] = None,
        was_clutch: bool = False,
        clutch_success: bool = False,
    ) -> None:
        """Update player memory"""
        
        if player_id not in self.memory.player_memories:
            self.memory.player_memories[player_id] = PlayerMemory(
                player_id=player_id,
                player_name=player_name,
            )
        
        pm = self.memory.player_memories[player_id]
        n = pm.matches_analyzed or 1
        
        # Update stats
        pm.total_kills += kills
        pm.total_damage += damage
        pm.average_survival_time = (
            pm.average_survival_time * (n - 1) + survival_time
        ) / n
        
        if total_shots > 0:
            pm.headshot_percentage = (
                pm.headshot_percentage * (n - 1) + (headshots / total_shots * 100)
            ) / n
        
        # Update weapon preferences
        if weapons_used:
            for weapon in weapons_used:
                if weapon not in pm.preferred_weapons:
                    pm.preferred_weapons.append(weapon)
        
        # Update clutch ability
        if was_clutch:
            pm.clutch_ability = (
                pm.clutch_ability * (n - 1) + (10 if clutch_success else 3)
            ) / n
        
        # Update skill ratings
        if kills > 3:
            pm.mechanical_skill = min(10, pm.mechanical_skill + 0.2)
        if damage > 1000:
            pm.mechanical_skill = min(10, pm.mechanical_skill + 0.1)
        
        pm.matches_analyzed += 1
        pm.last_updated = time.time()
    
    def get_player_intel(self, player_id: str) -> Optional[Dict[str, Any]]:
        """Get intelligence on a specific player"""
        pm = self.memory.player_memories.get(player_id)
        if not pm:
            return None
        
        return {
            "name": pm.player_name,
            "mechanical_skill": round(pm.mechanical_skill, 1),
            "game_sense": round(pm.game_sense, 1),
            "clutch_ability": round(pm.clutch_ability, 1),
            "aggressiveness": round(pm.aggressiveness, 1),
            "preferred_weapons": pm.preferred_weapons[:5],
            "preferred_range": pm.preferred_range,
            "headshot_percentage": round(pm.headshot_percentage, 1),
            "flank_tendency": round(pm.flank_tendency, 1),
            "matches_analyzed": pm.matches_analyzed,
        }
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STRATEGY LEARNING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def classify_strategy(self) -> StrategyType:
        """Classify team's primary strategy based on learned patterns"""
        
        # Check landing patterns
        if self.memory.landing_spots:
            contested_landings = sum(1 for s in self.memory.landing_spots if s.contested)
            if contested_landings / len(self.memory.landing_spots) > 0.5:
                self.memory.primary_strategy = StrategyType.HOT_DROP
                return StrategyType.HOT_DROP
        
        # Check fight patterns
        fp = self.memory.fight_pattern
        if fp.aggression_level > 7:
            self.memory.primary_strategy = StrategyType.AGGRESSIVE_EARLY
        elif fp.aggression_level < 3:
            self.memory.primary_strategy = StrategyType.PASSIVE_EARLY
        elif fp.third_party_tendency > 0.6:
            self.memory.primary_strategy = StrategyType.THIRD_PARTY
        else:
            # Check rotation patterns
            rotations = list(self.memory.rotation_patterns)
            if rotations:
                # Check if team rotates along edge or through center
                avg_x = sum(r.to_location[0] for r in rotations) / len(rotations)
                avg_y = sum(r.to_location[1] for r in rotations) / len(rotations)
                dist_from_center = ((avg_x - 4000) ** 2 + (avg_y - 4000) ** 2) ** 0.5
                
                if dist_from_center > 2500:
                    self.memory.primary_strategy = StrategyType.EDGE_ROTATION
                elif dist_from_center < 1000:
                    self.memory.primary_strategy = StrategyType.CENTER_CONTROL
                else:
                    self.memory.primary_strategy = StrategyType.COMPOUND_HOLDING
        
        self.memory.strategy_confidence = min(
            1.0,
            self.memory.matches_analyzed / self.high_confidence_threshold
        )
        
        return self.memory.primary_strategy
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # WEAKNESS IDENTIFICATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def identify_weaknesses(self) -> List[str]:
        """Identify team weaknesses based on learned data"""
        weaknesses = []
        
        # Check survival
        if self.memory.average_placement > 10:
            weaknesses.append("Poor early game survival")
        
        # Check fight performance
        if self.memory.fight_pattern.win_rate < 0.4:
            weaknesses.append("Struggles in team fights")
        
        # Check close range
        if self.memory.fight_pattern.preferred_range == "close" and self.memory.fight_pattern.win_rate < 0.5:
            weaknesses.append("Vulnerable in close combat despite preference")
        
        # Check long range
        if self.memory.fight_pattern.preferred_range == "long" and self.memory.fight_pattern.win_rate < 0.5:
            weaknesses.append("Ineffective at long range engagements")
        
        # Check clutch performance
        if self.memory.fight_pattern.clutch_success_rate < 0.3:
            weaknesses.append("Poor clutch performance under pressure")
        
        # Check utility usage
        if self.memory.fight_pattern.utility_usage.get("smokes", 0) < 1:
            weaknesses.append("Low smoke usage - vulnerable to open field pushes")
        
        # Check zone positioning
        if self.memory.primary_strategy == StrategyType.EDGE_ROTATION and self.memory.average_placement > 8:
            weaknesses.append("Edge rotation strategy ineffective")
        
        # Check contested landings
        contested = [s for s in self.memory.landing_spots if s.contested]
        if contested:
            contested_success = sum(s.success_rate for s in contested) / len(contested)
            if contested_success < 0.5:
                weaknesses.append("Struggles in contested landings")
        
        self.memory.known_weaknesses = weaknesses
        return weaknesses
    
    def identify_exploitable_patterns(self) -> List[str]:
        """Identify exploitable patterns for opposing teams"""
        patterns = []
        
        # Predictable landings
        if self.memory.preferred_landing and len(self.memory.landing_spots) < 3:
            patterns.append(f"Predictable landing at {self.memory.preferred_landing}")
        
        # Predictable rotations
        common_rotations = self.get_common_rotations(3)
        if len(common_rotations) >= 2:
            patterns.append("Predictable rotation paths")
        
        # Weak early game
        if self.memory.average_placement > 10:
            patterns.append("Vulnerable in early game")
        
        # Low utility
        if sum(self.memory.fight_pattern.utility_usage.values()) < 3:
            patterns.append("Low utility usage - can be pressured")
        
        # Solo players
        for pm in self.memory.player_memories.values():
            if pm.solo_push_tendency > 0.6:
                patterns.append(f"{pm.player_name} tends to push solo - can be isolated")
        
        self.memory.exploitable_patterns = patterns
        return patterns
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MATCH COMPLETION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def complete_match(
        self,
        placement: int,
        kills: int,
        damage: int,
        strategy_used: StrategyType,
        was_win: bool = False,
    ) -> None:
        """Record match completion and update all statistics"""
        
        n = self.memory.matches_analyzed
        
        # Update averages
        self.memory.average_placement = (
            self.memory.average_placement * n + placement
        ) / (n + 1)
        
        self.memory.win_rate = (
            self.memory.win_rate * n + (1 if was_win else 0)
        ) / (n + 1)
        
        self.memory.top5_rate = (
            self.memory.top5_rate * n + (1 if placement <= 5 else 0)
        ) / (n + 1)
        
        self.memory.average_kills_per_match = (
            self.memory.average_kills_per_match * n + kills
        ) / (n + 1)
        
        self.memory.average_damage_per_match = (
            self.memory.average_damage_per_match * n + damage
        ) / (n + 1)
        
        # Track performance trend
        self.memory.performance_trend.append({
            "timestamp": time.time(),
            "placement": placement,
            "kills": kills,
            "damage": damage,
            "was_win": was_win,
        })
        
        # Track strategy
        self.memory.strategy_evolution.append({
            "timestamp": time.time(),
            "strategy": strategy_used.value,
            "placement": placement,
        })
        
        self.memory.matches_analyzed += 1
        self.memory.last_updated = time.time()
        
        # Re-classify strategy
        self.classify_strategy()
        
        # Update weaknesses
        self.identify_weaknesses()
        
        self._log.info(
            f"Match recorded: Placement #{placement}, "
            f"{kills} kills, {damage} damage",
            total_matches=self.memory.matches_analyzed,
            avg_placement=round(self.memory.average_placement, 1),
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # INTELLIGENCE QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_full_intelligence(self) -> Dict[str, Any]:
        """Get complete team intelligence report"""
        return {
            "team": {
                "name": self.memory.team_name,
                "matches_analyzed": self.memory.matches_analyzed,
                "primary_strategy": self.memory.primary_strategy.value,
                "strategy_confidence": round(self.memory.strategy_confidence, 2),
            },
            "performance": {
                "avg_placement": round(self.memory.average_placement, 1),
                "win_rate": round(self.memory.win_rate * 100, 1),
                "top5_rate": round(self.memory.top5_rate * 100, 1),
                "avg_kills": round(self.memory.average_kills_per_match, 1),
                "avg_damage": round(self.memory.average_damage_per_match, 0),
            },
            "landing": self.get_landing_prediction(),
            "rotations": self.get_common_rotations(5),
            "fighting": {
                "preferred_range": self.memory.fight_pattern.preferred_range,
                "aggression": round(self.memory.fight_pattern.aggression_level, 1),
                "win_rate": round(self.memory.fight_pattern.win_rate * 100, 1),
                "avg_duration": round(self.memory.fight_pattern.average_duration, 1),
                "third_party_tendency": round(self.memory.fight_pattern.third_party_tendency * 100, 1),
                "clutch_success": round(self.memory.fight_pattern.clutch_success_rate * 100, 1),
                "utility_profile": self.memory.fight_pattern.utility_usage,
            },
            "rivalries": [
                {
                    "team_id": h2h.team_id,
                    "encounters": h2h.encounters,
                    "wins": h2h.wins,
                    "losses": h2h.losses,
                    "last_winner": h2h.last_winner,
                }
                for h2h in self.memory.head_to_head.values()
                if h2h.encounters >= 3
            ],
            "weaknesses": self.memory.known_weaknesses,
            "exploitable": self.memory.exploitable_patterns,
            "players": {
                pid: self.get_player_intel(pid)
                for pid in self.memory.player_memories
            },
        }
    
    def get_strategy_prediction(self) -> Dict[str, Any]:
        """Predict what strategy team will use"""
        return {
            "primary_strategy": self.memory.primary_strategy.value,
            "confidence": round(self.memory.strategy_confidence, 2),
            "secondary_strategies": [
                s.value for s in self.memory.secondary_strategies
            ],
            "likely_landing": self.get_landing_prediction(),
        }
    
    def get_head_to_head(self, enemy_team_id: str) -> Optional[Dict[str, Any]]:
        """Get head-to-head record"""
        h2h = self.memory.head_to_head.get(enemy_team_id)
        if not h2h:
            return None
        
        return {
            "encounters": h2h.encounters,
            "wins": h2h.wins,
            "losses": h2h.losses,
            "win_rate": round(h2h.wins / h2h.encounters * 100, 1) if h2h.encounters > 0 else 0,
            "last_encounter": h2h.last_encounter,
            "last_winner": h2h.last_winner,
            "avg_duration": round(h2h.average_fight_duration, 1),
        }
    
    def export_memory(self) -> Dict[str, Any]:
        """Export all memory data for storage"""
        return {
            "team_id": self.memory.team_id,
            "team_name": self.memory.team_name,
            "matches_analyzed": self.memory.matches_analyzed,
            "primary_strategy": self.memory.primary_strategy.value,
            "landing_spots": [
                {
                    "name": s.location_name,
                    "frequency": s.frequency,
                    "success_rate": s.success_rate,
                    "contested": s.contested,
                }
                for s in self.memory.landing_spots
            ],
            "fight_pattern": {
                "preferred_range": self.memory.fight_pattern.preferred_range,
                "aggression": self.memory.fight_pattern.aggression_level,
                "win_rate": self.memory.fight_pattern.win_rate,
            },
            "performance": {
                "avg_placement": self.memory.average_placement,
                "win_rate": self.memory.win_rate,
                "avg_kills": self.memory.average_kills_per_match,
            },
            "last_updated": self.memory.last_updated,
        }
    
    def import_memory(self, data: Dict[str, Any]) -> None:
        """Import memory data from storage"""
        self.memory.matches_analyzed = data.get("matches_analyzed", 0)
        self.memory.average_placement = data.get("performance", {}).get("avg_placement", 0)
        self.memory.win_rate = data.get("performance", {}).get("win_rate", 0)
        self.memory.average_kills_per_match = data.get("performance", {}).get("avg_kills", 0)
        self.memory.last_updated = data.get("last_updated", time.time())
        
        self._log.info(
            f"Memory imported: {self.memory.matches_analyzed} matches analyzed"
        )
    
    def reset_for_new_match(self) -> None:
        """Reset short-term tracking for new match (keep long-term memory)"""
        self._log.info("Short-term memory reset for new match")
        # Long-term memory persists

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("fight_ended")
        self.subscribe_to_event("match_state_change")

    async def tick(self) -> None:
        """Periodic maintenance tick."""
        # Heuristic engine is driven by direct method calls;
        # nothing periodic is required here.

    async def handle_event(self, record: EventRecord) -> None:
        """Handle an event from the event bus.

        The heuristic engine is driven synchronously via direct method
        calls; this hook keeps match context current for future
        event-driven use.
        """
        try:
            data = record.event.data if hasattr(record, "event") else {}
            if isinstance(data, dict) and data.get("match_id"):
                self.config.match_id = data["match_id"]
        except Exception:
            pass

    async def cleanup(self) -> None:
        """Release agent resources."""
