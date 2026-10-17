# backend/app/agents/fight_agents/clutch_detector.py
"""
AEGIS AI - Clutch Detector
Detects 1vX situations, calculates clutch probability,
tracks clutch attempts, and determines entertainment value.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from collections import deque
from enum import Enum
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.clutch_detector")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & CONSTANTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ClutchType(str, Enum):
    """Types of clutch situations"""
    SOLO_VS_SQUAD = "1v4"
    SOLO_VS_TRIO = "1v3"
    SOLO_VS_DUO = "1v2"
    SOLO_VS_SOLO = "1v1"
    DUO_VS_SQUAD = "2v4"
    DUO_VS_TRIO = "2v3"
    KNOCKED_CLUTCH = "knocked_clutch"  # Last alive is knocked

class ClutchPhase(str, Enum):
    """Phases of a clutch attempt"""
    DETECTED = "detected"        # Clutch situation identified
    ENGAGED = "engaged"          # Fighting started
    PROGRESSING = "progressing"  # Making progress (getting kills)
    FINAL_OPPONENT = "final_opponent"  # Down to last enemy
    WON = "won"                  # Clutch successful
    LOST = "lost"                # Clutch failed
    INTERRUPTED = "interrupted"  # Third party interfered

class ClutchDifficulty(str, Enum):
    """Difficulty assessment of clutch"""
    IMPOSSIBLE = "impossible"    # < 5% chance
    EXTREME = "extreme"          # 5-15%
    HARD = "hard"                # 15-30%
    MODERATE = "moderate"        # 30-50%
    FAVORABLE = "favorable"      # 50-70%
    LIKELY = "likely"            # > 70%

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class ClutchSituation:
    """A detected clutch situation"""
    clutch_id: str
    
    # The clutch player/team
    player_id: str
    player_name: str
    team_id: str
    team_name: str
    
    # Situation
    clutch_type: ClutchType
    phase: ClutchPhase = ClutchPhase.DETECTED
    difficulty: ClutchDifficulty = ClutchDifficulty.MODERATE
    
    # Odds
    alive_teammates: int = 0
    knocked_teammates: int = 0
    enemy_players: int = 4
    enemy_teams: int = 1
    
    # Player state
    player_hp: int = 100
    player_armor: int = 100
    player_weapons: List[str] = field(default_factory=list)
    player_utility: Dict[str, int] = field(default_factory=dict)
    has_cover: bool = False
    has_high_ground: bool = False
    
    # Enemy state
    enemy_hp_total: int = 400
    enemy_positions_known: int = 0
    
    # Location
    location_x: float = 0.0
    location_y: float = 0.0
    zone_phase: int = 1
    in_zone: bool = True
    
    # Timing
    detected_at: float = field(default_factory=time.time)
    engaged_at: Optional[float] = None
    resolved_at: Optional[float] = None
    
    # Progress
    enemies_killed: int = 0
    enemies_knocked: int = 0
    damage_dealt: int = 0
    damage_taken: int = 0
    
    # Probability
    base_probability: float = 0.0
    adjusted_probability: float = 0.0
    probability_history: deque = field(default_factory=lambda: deque(maxlen=50))
    
    # Result
    won: Optional[bool] = None
    highlight_worthy: bool = False
    entertainment_value: float = 0.0
    
    # Fight context
    fight_id: Optional[str] = None
    
    def get_disadvantage_ratio(self) -> float:
        """Get the player disadvantage ratio (enemies / friendlies)"""
        friendlies = self.alive_teammates + 1  # +1 for the clutch player
        if friendlies == 0:
            return float('inf')
        return self.enemy_players / friendlies
    
    def is_active(self) -> bool:
        """Check if clutch is still in progress"""
        return self.phase not in [ClutchPhase.WON, ClutchPhase.LOST, ClutchPhase.INTERRUPTED]

@dataclass
class ClutchHistory:
    """Historical clutch performance"""
    player_id: str
    total_attempts: int = 0
    successful: int = 0
    failed: int = 0
    success_rate: float = 0.0
    
    # By type
    by_type: Dict[str, Dict[str, int]] = field(default_factory=dict)
    
    # Average stats
    avg_enemies_killed_per_attempt: float = 0.0
    avg_damage_per_attempt: float = 0.0
    
    # Best clutches
    best_clutch: Optional[str] = None  # clutch_id of most impressive
    most_recent: Optional[float] = None
    
    # Streaks
    current_streak: int = 0
    best_streak: int = 0


class ClutchDetector(BaseAgent):
    """
    Detects and tracks clutch situations in real-time.
    
    A clutch is when a player/team is at a significant
    numerical disadvantage and must overcome odds to win.
    
    Classic clutch: 1v4, 1v3, 1v2
    Also detects: 2v4, knocked player clutches
    
    Calculates:
    - Base probability from numbers
    - Adjusted probability considering HP, weapons, position
    - Real-time probability updates
    - Entertainment value for broadcasting
    - Highlight-worthiness
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        # Active clutches
        self.active_clutches: Dict[str, ClutchSituation] = {}  # clutch_id -> Clutch
        
        # Player clutch history
        self.player_history: Dict[str, ClutchHistory] = {}
        
        # Team clutch tracking
        self.team_clutch_attempts: Dict[str, int] = {}
        
        # Probability calculation weights
        self.probability_weights = {
            "numbers": 0.35,      # Numerical disadvantage
            "hp": 0.25,           # HP comparison
            "position": 0.15,     # Cover/high ground
            "weapons": 0.10,      # Weapon advantage
            "utility": 0.10,      # Utility items
            "zone": 0.05,         # Zone position
        }
        
        # Base probabilities for clutch types
        self.base_probabilities = {
            ClutchType.SOLO_VS_SQUAD: 0.08,    # 8%
            ClutchType.SOLO_VS_TRIO: 0.15,     # 15%
            ClutchType.SOLO_VS_DUO: 0.30,      # 30%
            ClutchType.SOLO_VS_SOLO: 0.50,     # 50%
            ClutchType.DUO_VS_SQUAD: 0.20,     # 20%
            ClutchType.DUO_VS_TRIO: 0.35,      # 35%
            ClutchType.KNOCKED_CLUTCH: 0.05,   # 5% (teammate must revive)
        }
        
        # Thresholds
        self.highlight_threshold = 0.15  # Below this probability = highlight worthy
        self.entertainment_threshold = 0.25  # Below this = high entertainment
        
        self._log = logger.bind(component="clutch_detector")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CLUTCH DETECTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def detect_clutch(
        self,
        player_id: str,
        player_name: str,
        team_id: str,
        team_name: str,
        alive_teammates: int,
        knocked_teammates: int,
        enemy_players: int,
        enemy_teams: int = 1,
        player_hp: int = 100,
        player_armor: int = 100,
        player_weapons: List[str] = None,
        player_utility: Dict[str, int] = None,
        location_x: float = 0.0,
        location_y: float = 0.0,
        has_cover: bool = False,
        has_high_ground: bool = False,
        enemy_hp_total: int = 400,
        enemy_positions_known: int = 0,
        zone_phase: int = 1,
        in_zone: bool = True,
        fight_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Detect if current situation is a clutch.
        Returns clutch_id if clutch detected, None otherwise.
        """
        
        # Determine if this is a clutch situation
        friendly_count = alive_teammates + 1  # +1 for this player
        
        if friendly_count >= enemy_players:
            return None  # Not a clutch - fair fight or advantage
        
        if knocked_teammates > 0 and alive_teammates == 0:
            return None  # All teammates knocked - check for knocked clutch
        
        # Determine clutch type
        clutch_type = self._determine_clutch_type(
            alive_teammates, enemy_players
        )
        
        if not clutch_type:
            return None
        
        # Check if already tracking a clutch for this player
        for existing_id, existing in self.active_clutches.items():
            if existing.player_id == player_id and existing.is_active():
                # Update existing clutch
                self._update_clutch(existing, enemy_players, player_hp, player_armor)
                return existing_id
        
        # Create new clutch
        clutch_id = f"clutch_{int(time.time() * 1000)}_{player_id[:8]}"
        
        clutch = ClutchSituation(
            clutch_id=clutch_id,
            player_id=player_id,
            player_name=player_name,
            team_id=team_id,
            team_name=team_name,
            clutch_type=clutch_type,
            alive_teammates=alive_teammates,
            knocked_teammates=knocked_teammates,
            enemy_players=enemy_players,
            enemy_teams=enemy_teams,
            player_hp=player_hp,
            player_armor=player_armor,
            player_weapons=player_weapons or [],
            player_utility=player_utility or {},
            has_cover=has_cover,
            has_high_ground=has_high_ground,
            enemy_hp_total=enemy_hp_total,
            enemy_positions_known=enemy_positions_known,
            location_x=location_x,
            location_y=location_y,
            zone_phase=zone_phase,
            in_zone=in_zone,
            fight_id=fight_id,
        )
        
        # Calculate probabilities
        clutch.base_probability = self.base_probabilities.get(clutch_type, 0.3)
        clutch.adjusted_probability = self._calculate_adjusted_probability(clutch)
        clutch.probability_history.append(clutch.adjusted_probability)
        
        # Determine difficulty
        clutch.difficulty = self._determine_difficulty(clutch.adjusted_probability)
        
        # Check if highlight-worthy
        clutch.highlight_worthy = clutch.adjusted_probability < self.highlight_threshold
        clutch.entertainment_value = self._calculate_entertainment_value(clutch)
        
        self.active_clutches[clutch_id] = clutch
        
        # Update player history
        self._init_player_history(player_id, player_name)
        
        self._log.warning(
            f"🎯 CLUTCH DETECTED: {clutch_type.value} | "
            f"Player: {player_name} | "
            f"Probability: {clutch.adjusted_probability:.1%} | "
            f"Difficulty: {clutch.difficulty.value} | "
            f"Highlight: {clutch.highlight_worthy}"
        )
        
        return clutch_id
    
    def _determine_clutch_type(
        self,
        alive_teammates: int,
        enemy_players: int,
    ) -> Optional[ClutchType]:
        """Determine the type of clutch"""
        friendly = alive_teammates + 1
        
        if friendly == 1:
            if enemy_players >= 4:
                return ClutchType.SOLO_VS_SQUAD
            elif enemy_players == 3:
                return ClutchType.SOLO_VS_TRIO
            elif enemy_players == 2:
                return ClutchType.SOLO_VS_DUO
            elif enemy_players == 1:
                return ClutchType.SOLO_VS_SOLO
        elif friendly == 2:
            if enemy_players >= 4:
                return ClutchType.DUO_VS_SQUAD
            elif enemy_players == 3:
                return ClutchType.DUO_VS_TRIO
        
        return None
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PROBABILITY CALCULATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _calculate_adjusted_probability(self, clutch: ClutchSituation) -> float:
        """Calculate adjusted win probability"""
        
        base = clutch.base_probability
        adjustments = []
        
        # HP adjustment
        hp_ratio = clutch.player_hp / 100
        enemy_hp_per_player = clutch.enemy_hp_total / max(1, clutch.enemy_players)
        enemy_hp_ratio = enemy_hp_per_player / 100
        hp_adjustment = (hp_ratio - enemy_hp_ratio) * 0.2
        adjustments.append(("hp", hp_adjustment * self.probability_weights["hp"]))
        
        # Armor adjustment
        armor_bonus = (clutch.player_armor / 100) * 0.1
        adjustments.append(("armor", armor_bonus * 0.5))
        
        # Position adjustment
        position_bonus = 0.0
        if clutch.has_cover:
            position_bonus += 0.08
        if clutch.has_high_ground:
            position_bonus += 0.10
        adjustments.append(("position", position_bonus * self.probability_weights["position"]))
        
        # Weapon adjustment
        weapon_bonus = 0.0
        good_weapons = ["AWM", "M416", "AKM", "GROZA", "MK14", "AUG"]
        for weapon in clutch.player_weapons:
            if weapon.upper() in good_weapons:
                weapon_bonus += 0.03
        adjustments.append(("weapons", weapon_bonus * self.probability_weights["weapons"]))
        
        # Utility adjustment
        utility_bonus = 0.0
        utility_bonus += clutch.player_utility.get("grenades", 0) * 0.02
        utility_bonus += clutch.player_utility.get("smokes", 0) * 0.015
        utility_bonus += clutch.player_utility.get("molotovs", 0) * 0.02
        utility_bonus = min(0.15, utility_bonus)  # Cap at 15%
        adjustments.append(("utility", utility_bonus * self.probability_weights["utility"]))
        
        # Zone adjustment
        zone_bonus = 0.0
        if clutch.in_zone:
            zone_bonus += 0.03
        if clutch.zone_phase >= 6:
            zone_bonus += 0.02  # Late game - more pressure on everyone
        adjustments.append(("zone", zone_bonus * self.probability_weights["zone"]))
        
        # Enemy knowledge adjustment
        knowledge_bonus = clutch.enemy_positions_known / max(1, clutch.enemy_players) * 0.05
        adjustments.append(("knowledge", knowledge_bonus))
        
        # Apply all adjustments
        total_adjustment = sum(adj for _, adj in adjustments)
        adjusted = base + total_adjustment
        
        # Player history adjustment
        history = self.player_history.get(clutch.player_id)
        if history and history.total_attempts >= 5:
            # Experienced clutch player bonus
            if history.success_rate > 0.3:
                adjusted += 0.05
            elif history.success_rate > 0.5:
                adjusted += 0.10
        
        return max(0.01, min(0.95, adjusted))  # Clamp between 1% and 95%
    
    def _determine_difficulty(self, probability: float) -> ClutchDifficulty:
        """Determine difficulty from probability"""
        if probability < 0.05:
            return ClutchDifficulty.IMPOSSIBLE
        elif probability < 0.15:
            return ClutchDifficulty.EXTREME
        elif probability < 0.30:
            return ClutchDifficulty.HARD
        elif probability < 0.50:
            return ClutchDifficulty.MODERATE
        elif probability < 0.70:
            return ClutchDifficulty.FAVORABLE
        else:
            return ClutchDifficulty.LIKELY
    
    def _calculate_entertainment_value(self, clutch: ClutchSituation) -> float:
        """Calculate entertainment value for broadcasting (0-100)"""
        value = 50.0  # Base
        
        # Lower probability = more exciting
        value += (1 - clutch.adjusted_probability) * 40
        
        # Higher disadvantage = more exciting
        disadvantage = clutch.get_disadvantage_ratio()
        value += min(20, disadvantage * 5)
        
        # Known clutch player
        history = self.player_history.get(clutch.player_id)
        if history and history.success_rate > 0.4:
            value += 10  # This player can actually do it
        
        # Late game
        if clutch.zone_phase >= 6:
            value += 10
        
        return min(100, value)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CLUTCH UPDATES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def update_clutch_progress(
        self,
        clutch_id: str,
        event_type: str,
        data: Dict[str, Any],
    ) -> Optional[ClutchSituation]:
        """Update a clutch with new events"""
        
        clutch = self.active_clutches.get(clutch_id)
        if not clutch or not clutch.is_active():
            return None
        
        if event_type == "enemy_knocked":
            clutch.enemies_knocked += 1
            clutch.enemy_players -= 1
            clutch.phase = ClutchPhase.PROGRESSING
            
            # Recalculate probability (improving)
            new_prob = self._calculate_adjusted_probability(clutch)
            clutch.adjusted_probability = new_prob
            clutch.probability_history.append(new_prob)
            
            # Check if down to final opponent
            if clutch.enemy_players <= 1:
                clutch.phase = ClutchPhase.FINAL_OPPONENT
        
        elif event_type == "enemy_killed":
            clutch.enemies_killed += 1
            clutch.damage_dealt += data.get("damage", 0)
            
            # Check if clutch won
            if clutch.enemy_players <= 1 and event_type == "enemy_killed":
                clutch.enemy_players = 0
                self._resolve_clutch(clutch_id, won=True)
        
        elif event_type == "damage_taken":
            damage = data.get("damage", 0)
            clutch.damage_taken += damage
            clutch.player_hp = max(0, clutch.player_hp - damage)
            
            if clutch.player_hp <= 0:
                self._resolve_clutch(clutch_id, won=False)
            else:
                # Recalculate with lower HP
                new_prob = self._calculate_adjusted_probability(clutch)
                clutch.adjusted_probability = new_prob
                clutch.probability_history.append(new_prob)
        
        elif event_type == "teammate_revived":
            clutch.alive_teammates += 1
            clutch.knocked_teammates -= 1
            # Re-evaluate if still a clutch
            new_prob = self._calculate_adjusted_probability(clutch)
            clutch.adjusted_probability = new_prob
        
        elif event_type == "player_knocked":
            if clutch.player_hp <= 0:
                self._resolve_clutch(clutch_id, won=False)
        
        elif event_type == "engaged":
            if not clutch.engaged_at:
                clutch.engaged_at = time.time()
                clutch.phase = ClutchPhase.ENGAGED
        
        elif event_type == "third_party":
            clutch.phase = ClutchPhase.INTERRUPTED
            self._resolve_clutch(clutch_id, won=None)
        
        return clutch
    
    def update_clutch_state(
        self,
        clutch_id: str,
        player_hp: Optional[int] = None,
        player_armor: Optional[int] = None,
        enemy_count: Optional[int] = None,
        enemy_hp_total: Optional[int] = None,
        has_cover: Optional[bool] = None,
    ) -> None:
        """Update clutch state and recalculate"""
        
        clutch = self.active_clutches.get(clutch_id)
        if not clutch:
            return
        
        if player_hp is not None:
            clutch.player_hp = player_hp
        if player_armor is not None:
            clutch.player_armor = player_armor
        if enemy_count is not None:
            clutch.enemy_players = enemy_count
        if enemy_hp_total is not None:
            clutch.enemy_hp_total = enemy_hp_total
        if has_cover is not None:
            clutch.has_cover = has_cover
        
        clutch.adjusted_probability = self._calculate_adjusted_probability(clutch)
        clutch.probability_history.append(clutch.adjusted_probability)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CLUTCH RESOLUTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _resolve_clutch(self, clutch_id: str, won: Optional[bool]) -> Optional[Dict[str, Any]]:
        """Resolve a clutch attempt"""
        
        clutch = self.active_clutches.get(clutch_id)
        if not clutch:
            return None
        
        clutch.resolved_at = time.time()
        clutch.won = won
        
        if won:
            clutch.phase = ClutchPhase.WON
        elif won is False:
            clutch.phase = ClutchPhase.LOST
        
        duration = (clutch.resolved_at - clutch.detected_at) if clutch.resolved_at else 0
        
        # Update player history
        self._update_player_history(clutch.player_id, won)
        
        # Update team tracking
        if clutch.team_id not in self.team_clutch_attempts:
            self.team_clutch_attempts[clutch.team_id] = 0
        self.team_clutch_attempts[clutch.team_id] += 1
        
        # Build summary
        summary = {
            "clutch_id": clutch.clutch_id,
            "player_name": clutch.player_name,
            "clutch_type": clutch.clutch_type.value,
            "result": "won" if won else ("lost" if won is False else "interrupted"),
            "difficulty": clutch.difficulty.value,
            "base_probability": round(clutch.base_probability, 3),
            "final_probability": round(clutch.adjusted_probability, 3),
            "duration_seconds": round(duration, 1),
            "enemies_killed": clutch.enemies_killed,
            "enemies_knocked": clutch.enemies_knocked,
            "damage_dealt": clutch.damage_dealt,
            "damage_taken": clutch.damage_taken,
            "highlight_worthy": clutch.highlight_worthy,
            "entertainment_value": round(clutch.entertainment_value, 1),
            "zone_phase": clutch.zone_phase,
            "fight_id": clutch.fight_id,
        }
        
        if won:
            self._log.warning(
                f"🏆 CLUTCH WON: {clutch.player_name} | "
                f"{clutch.clutch_type.value} | "
                f"Probability: {clutch.adjusted_probability:.1%} | "
                f"Duration: {duration:.1f}s"
            )
        else:
            self._log.info(
                f"❌ Clutch failed: {clutch.player_name} | "
                f"{clutch.clutch_type.value} | "
                f"Killed {clutch.enemies_killed} enemies"
            )
        
        # Keep in active for a moment then remove
        # (caller should remove after processing)
        return summary
    
    def remove_clutch(self, clutch_id: str) -> None:
        """Remove a resolved clutch from tracking"""
        self.active_clutches.pop(clutch_id, None)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PLAYER HISTORY
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _init_player_history(self, player_id: str, player_name: str) -> None:
        """Initialize player clutch history"""
        if player_id not in self.player_history:
            self.player_history[player_id] = ClutchHistory(
                player_id=player_id,
            )
    
    def _update_player_history(self, player_id: str, won: Optional[bool]) -> None:
        """Update player clutch history"""
        history = self.player_history.get(player_id)
        if not history:
            return
        
        history.total_attempts += 1
        if won:
            history.successful += 1
            history.current_streak += 1
            history.best_streak = max(history.best_streak, history.current_streak)
        elif won is False:
            history.failed += 1
            history.current_streak = 0
        
        history.success_rate = (
            history.successful / history.total_attempts
            if history.total_attempts > 0 else 0
        )
        
        history.most_recent = time.time()
    
    def get_player_clutch_stats(self, player_id: str) -> Optional[Dict[str, Any]]:
        """Get player clutch statistics"""
        history = self.player_history.get(player_id)
        if not history:
            return None
        
        return {
            "player_id": history.player_id,
            "total_attempts": history.total_attempts,
            "successful": history.successful,
            "failed": history.failed,
            "success_rate": round(history.success_rate * 100, 1),
            "current_streak": history.current_streak,
            "best_streak": history.best_streak,
            "avg_enemies_killed": round(history.avg_enemies_killed_per_attempt, 1),
            "avg_damage": round(history.avg_damage_per_attempt, 0),
        }
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_active_clutches(self) -> List[Dict[str, Any]]:
        """Get all active clutch situations"""
        return [
            {
                "clutch_id": c.clutch_id,
                "player_name": c.player_name,
                "team_name": c.team_name,
                "type": c.clutch_type.value,
                "phase": c.phase.value,
                "probability": round(c.adjusted_probability, 3),
                "difficulty": c.difficulty.value,
                "enemy_players": c.enemy_players,
                "player_hp": c.player_hp,
                "entertainment": round(c.entertainment_value, 1),
                "highlight_worthy": c.highlight_worthy,
                "duration": round(time.time() - c.detected_at, 1),
            }
            for c in self.active_clutches.values()
            if c.is_active()
        ]
    
    def get_clutch(self, clutch_id: str) -> Optional[Dict[str, Any]]:
        """Get specific clutch details"""
        clutch = self.active_clutches.get(clutch_id)
        if not clutch:
            return None
        
        return {
            "clutch_id": clutch.clutch_id,
            "player": {
                "id": clutch.player_id,
                "name": clutch.player_name,
                "team": clutch.team_name,
                "hp": clutch.player_hp,
                "armor": clutch.player_armor,
                "weapons": clutch.player_weapons,
                "utility": clutch.player_utility,
            },
            "situation": {
                "type": clutch.clutch_type.value,
                "phase": clutch.phase.value,
                "difficulty": clutch.difficulty.value,
                "enemy_players": clutch.enemy_players,
                "enemy_teams": clutch.enemy_teams,
                "disadvantage_ratio": round(clutch.get_disadvantage_ratio(), 1),
            },
            "probability": {
                "base": round(clutch.base_probability, 3),
                "adjusted": round(clutch.adjusted_probability, 3),
                "history": [round(p, 3) for p in list(clutch.probability_history)[-10:]],
            },
            "progress": {
                "enemies_killed": clutch.enemies_killed,
                "enemies_knocked": clutch.enemies_knocked,
                "damage_dealt": clutch.damage_dealt,
                "damage_taken": clutch.damage_taken,
            },
            "position": {
                "has_cover": clutch.has_cover,
                "has_high_ground": clutch.has_high_ground,
                "in_zone": clutch.in_zone,
                "zone_phase": clutch.zone_phase,
            },
            "broadcast": {
                "entertainment_value": round(clutch.entertainment_value, 1),
                "highlight_worthy": clutch.highlight_worthy,
            },
            "timing": {
                "detected_at": clutch.detected_at,
                "engaged_at": clutch.engaged_at,
                "duration": round(time.time() - clutch.detected_at, 1),
            },
        }
    
    def get_most_entertaining_clutch(self) -> Optional[Dict[str, Any]]:
        """Get the most entertaining active clutch for broadcasting"""
        active = [c for c in self.active_clutches.values() if c.is_active()]
        if not active:
            return None
        
        best = max(active, key=lambda c: c.entertainment_value)
        return self.get_clutch(best.clutch_id)
    
    def is_player_in_clutch(self, player_id: str) -> bool:
        """Check if a player is currently in a clutch"""
        for clutch in self.active_clutches.values():
            if clutch.player_id == player_id and clutch.is_active():
                return True
        return False
    
    def get_stats(self) -> Dict[str, Any]:
        """Get clutch detection statistics"""
        return {
            "active_clutches": len(self.active_clutches),
            "total_players_tracked": len(self.player_history),
            "clutches_by_type": {
                c.clutch_type.value: len([
                    x for x in self.active_clutches.values()
                    if x.clutch_type == c.clutch_type
                ])
                for c in self.active_clutches.values()
            },
        }
    
    def reset(self) -> None:
        """Reset detector state"""
        self.active_clutches.clear()
        self._log.info("Clutch detector reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("player_knocked")
        self.subscribe_to_event("kill_confirmed")

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
