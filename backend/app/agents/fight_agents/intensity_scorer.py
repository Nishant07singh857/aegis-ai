# backend/app/agents/fight_agents/intensity_scorer.py
"""
AEGIS AI - Fight Intensity Scorer
Scores fight importance for broadcast priority.
Considers multiple factors to rank fights for the observer director.
"""

import time
from typing import Dict, List, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.intensity_scorer")

class BroadcastPriority(str, Enum):
    """Broadcast priority levels"""
    MUST_SHOW = "must_show"       # Critical - show immediately
    HIGH = "high"                 # Very important
    MEDIUM = "medium"             # Worth showing
    LOW = "low"                   # Can skip
    SKIP = "skip"                 # Don't show
    HOLD = "hold"                 # Stay on current scene

@dataclass
class FightScore:
    """Complete fight importance score"""
    fight_id: str
    
    # Core scores (0-100)
    intensity_score: float = 0.0       # Raw fight intensity
    entertainment_score: float = 0.0   # Viewer entertainment value
    narrative_score: float = 0.0       # Storyline importance
    strategic_score: float = 0.0       # Tactical significance
    broadcast_score: float = 0.0       # Final broadcast priority score
    
    # Component weights
    intensity_weight: float = 0.35
    entertainment_weight: float = 0.30
    narrative_weight: float = 0.20
    strategic_weight: float = 0.15
    
    # Factors
    is_clutch: bool = False
    is_final_circle: bool = False
    is_rivalry: bool = False
    is_comeback: bool = False
    is_underdog: bool = False
    is_multi_team: bool = False
    is_high_kill: bool = False
    
    # Timing
    scored_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    
    # Priority
    priority: BroadcastPriority = BroadcastPriority.MEDIUM
    
    def get_final_score(self) -> float:
        """Calculate weighted final score"""
        self.broadcast_score = (
            self.intensity_score * self.intensity_weight +
            self.entertainment_score * self.entertainment_weight +
            self.narrative_score * self.narrative_weight +
            self.strategic_score * self.strategic_weight
        )
        return self.broadcast_score
    
    def get_priority(self) -> BroadcastPriority:
        """Determine broadcast priority based on score"""
        score = self.get_final_score()
        
        if score >= 85:
            self.priority = BroadcastPriority.MUST_SHOW
        elif score >= 65:
            self.priority = BroadcastPriority.HIGH
        elif score >= 40:
            self.priority = BroadcastPriority.MEDIUM
        elif score >= 20:
            self.priority = BroadcastPriority.LOW
        else:
            self.priority = BroadcastPriority.SKIP
        
        return self.priority


class IntensityScorer(BaseAgent):
    """
    Scores fight importance for broadcast prioritization.
    
    Factors considered:
    1. Intensity - HP drops, kills, damage rate
    2. Entertainment - Clutch, multi-team, close fights
    3. Narrative - Rivalries, comebacks, underdogs
    4. Strategic - Zone position, match impact, placement
    5. Timing - Recent fights, duration, staleness
    
    Used by Observer Director to decide which fight to show.
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        # Scoring weights
        self.weights = {
            # Intensity components
            "damage_rate": 0.15,
            "kill_rate": 0.20,
            "player_density": 0.10,
            "utility_density": 0.05,
            
            # Entertainment components
            "clutch_bonus": 0.30,
            "multi_team_bonus": 0.15,
            "close_fight_bonus": 0.10,
            "comeback_bonus": 0.15,
            
            # Narrative components
            "rivalry_bonus": 0.20,
            "underdog_bonus": 0.10,
            "star_player_bonus": 0.10,
            
            # Strategic components
            "zone_importance": 0.10,
            "placement_impact": 0.10,
            "momentum_impact": 0.05,
        }
        
        # Decay rate for scores (per second)
        self.score_decay_rate = 0.5  # Points per second
        
        # Score history
        self.score_history: Dict[str, List[float]] = {}
        
        self._log = logger.bind(component="intensity_scorer")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # INTENSITY SCORING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def calculate_intensity(
        self,
        total_damage: int,
        damage_window_seconds: float,
        total_kills: int,
        total_knocks: int,
        duration_seconds: float,
        players_involved: int,
        utility_used: int,
    ) -> float:
        """Calculate raw fight intensity score (0-100)"""
        score = 0.0
        
        # Damage rate (damage per second)
        if damage_window_seconds > 0:
            dps = total_damage / damage_window_seconds
            damage_score = min(40, dps * 0.5)  # Cap at 40
            score += damage_score * self.weights["damage_rate"] * 100
        
        # Kill/knock rate
        if duration_seconds > 0:
            kill_rate = (total_kills * 2 + total_knocks) / duration_seconds * 60  # Per minute
            kill_score = min(40, kill_rate * 10)
            score += kill_score * self.weights["kill_rate"] * 100
        
        # Player density
        player_score = min(30, players_involved * 7.5)
        score += player_score * self.weights["player_density"] * 100
        
        # Utility density
        if duration_seconds > 0:
            utility_rate = utility_used / max(1, duration_seconds) * 60
            utility_score = min(20, utility_rate * 10)
            score += utility_score * self.weights["utility_density"] * 100
        
        return min(100, score)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ENTERTAINMENT SCORING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def calculate_entertainment(
        self,
        is_clutch: bool = False,
        clutch_odds: float = 0.5,
        is_multi_team: bool = False,
        team_count: int = 2,
        is_close_fight: bool = False,
        hp_differential: float = 0.5,
        is_comeback: bool = False,
        comeback_magnitude: float = 0.5,
    ) -> float:
        """Calculate entertainment value (0-100)"""
        score = 0.0
        
        # Clutch situations are most entertaining
        if is_clutch:
            clutch_score = min(50, (1 - clutch_odds) * 100)  # Lower odds = more exciting
            score += clutch_score * self.weights["clutch_bonus"] * 100
        
        # Multi-team fights
        if is_multi_team and team_count >= 3:
            multi_score = min(30, (team_count - 2) * 15)
            score += multi_score * self.weights["multi_team_bonus"] * 100
        
        # Close fights (even HP)
        if is_close_fight:
            closeness = 1 - abs(hp_differential - 0.5) * 2  # 0.5 = even, most exciting
            close_score = closeness * 30
            score += close_score * self.weights["close_fight_bonus"] * 100
        
        # Comeback potential
        if is_comeback:
            comeback_score = comeback_magnitude * 40
            score += comeback_score * self.weights["comeback_bonus"] * 100
        
        return min(100, score)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # NARRATIVE SCORING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def calculate_narrative(
        self,
        is_rivalry: bool = False,
        rivalry_intensity: float = 0.5,
        is_underdog: bool = False,
        underdog_rating: float = 0.3,
        has_star_player: bool = False,
        star_player_fame: float = 0.5,
        is_rematch: bool = False,
        is_elimination_match: bool = False,
    ) -> float:
        """Calculate narrative/storyline importance (0-100)"""
        score = 0.0
        
        # Rivalry matches
        if is_rivalry:
            rivalry_score = 20 + rivalry_intensity * 30
            score += rivalry_score * self.weights["rivalry_bonus"] * 100
        
        # Underdog story
        if is_underdog:
            underdog_score = 10 + underdog_rating * 25
            score += underdog_score * self.weights["underdog_bonus"] * 100
        
        # Star player involved
        if has_star_player:
            star_score = 5 + star_player_fame * 20
            score += star_score * self.weights["star_player_bonus"] * 100
        
        # Rematch bonus
        if is_rematch:
            score += 15
        
        # Elimination match (high stakes)
        if is_elimination_match:
            score += 25
        
        return min(100, score)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STRATEGIC SCORING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def calculate_strategic(
        self,
        zone_phase: int = 1,
        is_final_circle: bool = False,
        teams_alive: int = 16,
        placement_impact: float = 0.5,
        point_impact: float = 0.5,
        is_high_kill_team: bool = False,
    ) -> float:
        """Calculate strategic importance (0-100)"""
        score = 0.0
        
        # Zone phase importance (later = more important)
        zone_score = min(30, zone_phase * 4)
        score += zone_score * self.weights["zone_importance"] * 100
        
        # Final circle
        if is_final_circle:
            score += 30
        
        # Placement impact (how much this fight affects final standings)
        impact_score = placement_impact * 30
        score += impact_score * self.weights["placement_impact"] * 100
        
        # Point impact
        point_score = point_impact * 20
        score += point_score * self.weights["momentum_impact"] * 100
        
        # High kill team involved
        if is_high_kill_team:
            score += 10
        
        # Fewer teams = more important fights
        if teams_alive <= 5:
            score += 25
        elif teams_alive <= 10:
            score += 15
        elif teams_alive <= 15:
            score += 5
        
        return min(100, score)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # COMPLETE SCORING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def score_fight(
        self,
        fight_id: str,
        intensity_factors: Dict[str, Any],
        entertainment_factors: Dict[str, Any],
        narrative_factors: Dict[str, Any],
        strategic_factors: Dict[str, Any],
    ) -> FightScore:
        """Calculate complete fight score"""
        
        score = FightScore(fight_id=fight_id)
        
        # Calculate component scores
        score.intensity_score = self.calculate_intensity(**intensity_factors)
        score.entertainment_score = self.calculate_entertainment(**entertainment_factors)
        score.narrative_score = self.calculate_narrative(**narrative_factors)
        score.strategic_score = self.calculate_strategic(**strategic_factors)
        
        # Set flags from factors
        score.is_clutch = entertainment_factors.get("is_clutch", False)
        score.is_final_circle = strategic_factors.get("is_final_circle", False)
        score.is_rivalry = narrative_factors.get("is_rivalry", False)
        score.is_comeback = entertainment_factors.get("is_comeback", False)
        score.is_underdog = narrative_factors.get("is_underdog", False)
        score.is_multi_team = entertainment_factors.get("is_multi_team", False)
        
        # Calculate final score and priority
        score.get_final_score()
        score.get_priority()
        
        # Store in history
        if fight_id not in self.score_history:
            self.score_history[fight_id] = []
        self.score_history[fight_id].append(score.broadcast_score)
        
        return score
    
    def quick_score(
        self,
        fight_data: Dict[str, Any],
    ) -> FightScore:
        """Quick scoring from fight data dict"""
        
        intensity_factors = {
            "total_damage": fight_data.get("total_damage", 0),
            "damage_window_seconds": fight_data.get("damage_window", 5.0),
            "total_kills": fight_data.get("total_kills", 0),
            "total_knocks": fight_data.get("total_knocks", 0),
            "duration_seconds": fight_data.get("duration", 1.0),
            "players_involved": fight_data.get("players_involved", 4),
            "utility_used": fight_data.get("utility_used", 0),
        }
        
        entertainment_factors = {
            "is_clutch": fight_data.get("is_clutch", False),
            "clutch_odds": fight_data.get("clutch_odds", 0.5),
            "is_multi_team": fight_data.get("team_count", 2) >= 3,
            "team_count": fight_data.get("team_count", 2),
            "is_close_fight": fight_data.get("is_close", False),
        }
        
        narrative_factors = {
            "is_rivalry": fight_data.get("is_rivalry", False),
            "is_underdog": fight_data.get("is_underdog", False),
            "has_star_player": fight_data.get("has_star", False),
        }
        
        strategic_factors = {
            "zone_phase": fight_data.get("zone_phase", 1),
            "is_final_circle": fight_data.get("is_final_circle", False),
            "teams_alive": fight_data.get("teams_alive", 16),
            "placement_impact": fight_data.get("placement_impact", 0.5),
        }
        
        return self.score_fight(
            fight_data.get("fight_id", "unknown"),
            intensity_factors,
            entertainment_factors,
            narrative_factors,
            strategic_factors,
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SCORE COMPARISON
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def compare_fights(
        self,
        current_fight_score: FightScore,
        candidate_fight_score: FightScore,
        current_fight_duration: float = 10.0,
        min_scene_time: float = 10.0,
    ) -> Tuple[bool, str]:
        """
        Compare two fights to decide if should switch.
        Returns (should_switch, reason).
        """
        
        # Don't switch if current fight hasn't been shown long enough
        if current_fight_duration < min_scene_time:
            # Unless candidate is MUST_SHOW
            if candidate_fight_score.priority == BroadcastPriority.MUST_SHOW:
                return True, "Critical fight detected"
            return False, "Minimum scene time not met"
        
        current_score = current_fight_score.broadcast_score
        candidate_score = candidate_fight_score.broadcast_score
        
        # Must switch for MUST_SHOW priority
        if candidate_fight_score.priority == BroadcastPriority.MUST_SHOW:
            if current_fight_score.priority != BroadcastPriority.MUST_SHOW:
                return True, "Critical priority fight"
        
        # Switch if candidate is significantly better
        score_difference = candidate_score - current_score
        
        # Need minimum difference to switch (avoid flip-flopping)
        min_difference = 15.0
        
        # Lower threshold if current fight is stale
        if current_fight_duration > 30:
            min_difference = 10.0
        
        # Lower threshold if candidate is clutch
        if candidate_fight_score.is_clutch:
            min_difference = 5.0
        
        if score_difference > min_difference:
            return True, f"Candidate score {score_difference:.0f} points higher"
        
        # Switch if current fight is winding down
        if current_fight_score.broadcast_score < 30 and candidate_score > 50:
            return True, "Current fight winding down"
        
        return False, "Current fight has priority"
    
    def rank_fights(
        self,
        fights: List[FightScore],
    ) -> List[FightScore]:
        """Rank fights by broadcast priority"""
        return sorted(
            fights,
            key=lambda f: f.broadcast_score,
            reverse=True,
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # UTILITIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def apply_score_decay(self, score: FightScore) -> None:
        """Apply time-based decay to a fight score"""
        age = time.time() - score.scored_at
        
        # Decay scores over time
        decay = age * self.score_decay_rate
        
        score.intensity_score = max(0, score.intensity_score - decay * 0.3)
        score.entertainment_score = max(0, score.entertainment_score - decay * 0.2)
        score.narrative_score = max(0, score.narrative_score - decay * 0.1)
        score.strategic_score = max(0, score.strategic_score - decay * 0.15)
        
        # Recalculate
        score.get_final_score()
        score.get_priority()
    
    def get_score_trend(self, fight_id: str) -> List[float]:
        """Get score trend for a fight"""
        return self.score_history.get(fight_id, [])
    
    def reset(self) -> None:
        """Reset scorer"""
        self.score_history.clear()
        self._log.info("Intensity scorer reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("fight_detected")

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
