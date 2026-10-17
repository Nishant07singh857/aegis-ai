# backend/app/agents/analytics_agents/mvp_calculator.py
"""
AEGIS AI - MVP Calculator
Multi-factor MVP scoring engine that determines the most valuable player.
Considers kills, damage, survival, utility, clutch factor, and match impact.
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.mvp")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MVPCategory(str, Enum):
    """MVP award categories"""
    OVERALL_MVP = "overall_mvp"           # Best overall player
    BEST_FRAGGER = "best_fragger"         # Most kills/damage
    BEST_SUPPORT = "best_support"         # Healing, revives, assists
    BEST_IGL = "best_igl"                # Best strategic play
    BEST_SNIPER = "best_sniper"          # Long-range specialist
    BEST_CLUTCH = "best_clutch"          # Clutch performance
    MOST_IMPROVED = "most_improved"       # Biggest improvement
    FAN_FAVORITE = "fan_favorite"        # Fan-voted
    RISING_STAR = "rising_star"          # New talent
    BEST_UTILITY = "best_utility"        # Best grenade/smoke usage

class PerformanceWeight(str, Enum):
    """Weight categories for MVP calculation"""
    KILLS = "kills"
    DAMAGE = "damage"
    SURVIVAL = "survival"
    UTILITY = "utility"
    CLUTCH = "clutch"
    TEAMWORK = "teamwork"
    CONSISTENCY = "consistency"
    IMPACT = "impact"

@dataclass
class MVPResult:
    """Complete MVP calculation result"""
    player_id: str
    player_name: str
    team_id: str
    team_name: str
    
    # Scores (0-100)
    overall_score: float = 0.0
    combat_score: float = 0.0
    survival_score: float = 0.0
    utility_score: float = 0.0
    teamwork_score: float = 0.0
    clutch_score: float = 0.0
    impact_score: float = 0.0
    
    # Raw stats
    kills: int = 0
    knocks: int = 0
    assists: int = 0
    damage: int = 0
    headshots: int = 0
    revives: int = 0
    healing: int = 0
    survival_time: float = 0.0
    distance_traveled: float = 0.0
    smokes_used: int = 0
    grenades_used: int = 0
    clutch_attempts: int = 0
    clutch_wins: int = 0
    
    # Rankings
    rank: int = 0
    category_ranks: Dict[MVPCategory, int] = field(default_factory=dict)
    
    # Awards
    awards: List[MVPCategory] = field(default_factory=list)
    
    # Match context
    match_id: str = ""
    match_placement: int = 16
    zone_phase_at_death: int = 0
    
    # Historical
    is_personal_best: bool = False
    compared_to_average: float = 0.0  # % above/below average


class MVPCalculator(BaseAgent):
    """
    Multi-factor MVP scoring engine.
    
    Scoring Factors:
    1. Combat (35%): Kills, damage, headshots, knocks
    2. Survival (20%): Time alive, placement contribution
    3. Utility (15%): Grenades, smokes, healing effectiveness
    4. Teamwork (15%): Revives, assists, team coordination
    5. Clutch (10%): Clutch situations won
    6. Impact (5%): Match-defining moments
    
    Adjusts for:
    - Match placement (higher placement = more weight)
    - Zone phase (later deaths = more value)
    - Team performance context
    - Opponent strength
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        # Scoring weights (sum to 1.0)
        self.weights = {
            PerformanceWeight.KILLS: 0.20,
            PerformanceWeight.DAMAGE: 0.15,
            PerformanceWeight.SURVIVAL: 0.20,
            PerformanceWeight.UTILITY: 0.15,
            PerformanceWeight.CLUTCH: 0.10,
            PerformanceWeight.TEAMWORK: 0.15,
            PerformanceWeight.CONSISTENCY: 0.03,
            PerformanceWeight.IMPACT: 0.02,
        }
        
        # Base scores per category
        self.base_scores = {
            "kill": 10.0,        # Points per kill
            "knock": 5.0,        # Points per knock
            "assist": 3.0,       # Points per assist
            "damage": 0.01,      # Points per damage point
            "headshot_bonus": 5.0, # Bonus per headshot
            "revive": 8.0,       # Points per revive
            "healing": 0.005,    # Points per healing
            "survival_per_minute": 2.0,  # Points per minute survived
            "smoke": 2.0,        # Points per smoke used
            "grenade": 3.0,      # Points per grenade used
            "grenade_kill": 15.0, # Bonus for grenade kill
            "clutch_attempt": 10.0, # Points per clutch attempt
            "clutch_win": 30.0,  # Bonus for winning clutch
            "first_blood": 5.0,  # First kill bonus
            "last_kill": 10.0,   # Match-winning kill
        }
        
        # Placement multiplier (higher placement = more points)
        self.placement_multiplier = {
            1: 1.5, 2: 1.4, 3: 1.3, 4: 1.2, 5: 1.15,
            6: 1.1, 7: 1.05, 8: 1.0, 9: 0.95, 10: 0.9,
            11: 0.85, 12: 0.8, 13: 0.75, 14: 0.7,
            15: 0.65, 16: 0.6,
        }
        
        # Historical data
        self.player_averages: Dict[str, Dict[str, float]] = {}
        self.tournament_mvps: List[MVPResult] = []
        
        self._log = logger.bind(component="mvp_calculator")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MVP CALCULATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def calculate_mvp(
        self,
        players: List[Dict[str, Any]],
        match_context: Dict[str, Any],
    ) -> List[MVPResult]:
        """
        Calculate MVP scores for all players in a match.
        
        Args:
            players: List of player stats dictionaries
            match_context: Match-level context
            
        Returns:
            List of MVPResult sorted by overall_score descending
        """
        
        results = []
        
        for player in players:
            result = self._calculate_player_score(player, match_context)
            results.append(result)
        
        # Sort by overall score
        results.sort(key=lambda r: r.overall_score, reverse=True)
        
        # Assign ranks
        for i, result in enumerate(results):
            result.rank = i + 1
        
        # Determine category winners
        self._assign_category_awards(results)
        
        # Store top performers
        if results:
            self.tournament_mvps.append(results[0])
        
        self._log.info(
            f"MVP Calculated: {results[0].player_name if results else 'N/A'} "
            f"({results[0].overall_score:.1f})" if results else "No players"
        )
        
        return results
    
    def _calculate_player_score(
        self,
        player: Dict[str, Any],
        context: Dict[str, Any],
    ) -> MVPResult:
        """Calculate MVP score for a single player"""
        
        # Extract stats
        kills = player.get("kills", 0)
        knocks = player.get("knocks", 0)
        assists = player.get("assists", 0)
        damage = player.get("damage", 0)
        headshots = player.get("headshots", 0)
        revives = player.get("revives", 0)
        healing = player.get("healing", 0)
        survival_time = player.get("survival_time", 0)
        smokes = player.get("smokes", 0)
        grenades = player.get("grenades", 0)
        clutch_attempts = player.get("clutch_attempts", 0)
        clutch_wins = player.get("clutch_wins", 0)
        
        placement = context.get("team_placement", 16)
        multiplier = self.placement_multiplier.get(placement, 1.0)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Combat Score
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        combat_score = 0.0
        
        # Kill points
        combat_score += kills * self.base_scores["kill"]
        combat_score += knocks * self.base_scores["knock"]
        combat_score += assists * self.base_scores["assist"]
        
        # Damage points
        combat_score += damage * self.base_scores["damage"]
        
        # Headshot bonus
        combat_score += headshots * self.base_scores["headshot_bonus"]
        
        # Apply multiplier
        combat_score *= multiplier
        
        # Normalize to 0-100
        combat_score = min(100, combat_score * self.weights[PerformanceWeight.KILLS] * 0.5)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Survival Score
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        survival_minutes = survival_time / 60
        survival_score = survival_minutes * self.base_scores["survival_per_minute"]
        
        # Bonus for surviving to late game
        zone_phase = player.get("zone_phase_at_death", 0)
        if zone_phase >= 6:
            survival_score *= 1.5
        elif zone_phase >= 4:
            survival_score *= 1.2
        
        survival_score *= multiplier
        survival_score = min(100, survival_score * self.weights[PerformanceWeight.SURVIVAL] * 0.5)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Utility Score
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        utility_score = 0.0
        utility_score += smokes * self.base_scores["smoke"]
        utility_score += grenades * self.base_scores["grenade"]
        utility_score += player.get("grenade_kills", 0) * self.base_scores["grenade_kill"]
        utility_score += healing * self.base_scores["healing"]
        
        utility_score *= multiplier
        utility_score = min(100, utility_score * self.weights[PerformanceWeight.UTILITY] * 0.5)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Teamwork Score
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        teamwork_score = 0.0
        teamwork_score += revives * self.base_scores["revive"]
        teamwork_score += assists * self.base_scores["assist"] * 2  # Assists show teamwork
        
        # Bonus for team support actions
        if revives >= 3:
            teamwork_score += 10
        if assists >= 5:
            teamwork_score += 10
        
        teamwork_score *= multiplier
        teamwork_score = min(100, teamwork_score * self.weights[PerformanceWeight.TEAMWORK] * 0.5)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Clutch Score
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        clutch_score = 0.0
        clutch_score += clutch_attempts * self.base_scores["clutch_attempt"]
        clutch_score += clutch_wins * self.base_scores["clutch_win"]
        
        # Big bonus for multiple clutch wins
        if clutch_wins >= 2:
            clutch_score += 25
        
        clutch_score *= multiplier
        clutch_score = min(100, clutch_score * self.weights[PerformanceWeight.CLUTCH] * 0.5)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Impact Score
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        impact_score = 0.0
        
        # First blood bonus
        if player.get("first_blood", False):
            impact_score += self.base_scores["first_blood"]
        
        # Match-winning kill
        if player.get("last_kill", False):
            impact_score += self.base_scores["last_kill"]
        
        # High damage percentage of team
        team_damage = context.get("team_total_damage", 1)
        if team_damage > 0:
            damage_share = damage / team_damage
            if damage_share > 0.5:  # Did more than 50% of team damage
                impact_score += 20
            elif damage_share > 0.4:
                impact_score += 10
        
        impact_score *= multiplier
        impact_score = min(100, impact_score * self.weights[PerformanceWeight.IMPACT] * 0.5)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Consistency Bonus
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        consistency_score = 0.0
        
        # Check if player performed above their average
        player_id = player.get("player_id", "")
        if player_id in self.player_averages:
            avg = self.player_averages[player_id]
            if kills > avg.get("avg_kills", 0) * 1.5:
                consistency_score += 15
            if damage > avg.get("avg_damage", 0) * 1.5:
                consistency_score += 15
        
        consistency_score = min(100, consistency_score * self.weights[PerformanceWeight.CONSISTENCY] * 0.5)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Calculate Overall
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        overall_score = (
            combat_score +
            survival_score +
            utility_score +
            teamwork_score +
            clutch_score +
            impact_score +
            consistency_score
        )
        
        # Create result
        result = MVPResult(
            player_id=player.get("player_id", ""),
            player_name=player.get("player_name", "Unknown"),
            team_id=player.get("team_id", ""),
            team_name=player.get("team_name", "Unknown"),
            overall_score=round(overall_score, 1),
            combat_score=round(combat_score, 1),
            survival_score=round(survival_score, 1),
            utility_score=round(utility_score, 1),
            teamwork_score=round(teamwork_score, 1),
            clutch_score=round(clutch_score, 1),
            impact_score=round(impact_score, 1),
            kills=kills,
            knocks=knocks,
            assists=assists,
            damage=damage,
            headshots=headshots,
            revives=revives,
            healing=healing,
            survival_time=survival_time,
            smokes_used=smokes,
            grenades_used=grenades,
            clutch_attempts=clutch_attempts,
            clutch_wins=clutch_wins,
            match_placement=placement,
            match_id=context.get("match_id", ""),
        )
        
        return result
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CATEGORY AWARDS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _assign_category_awards(self, results: List[MVPResult]) -> None:
        """Assign category-specific awards"""
        
        if not results:
            return
        
        # Best Fragger (highest combat score)
        best_fragger = max(results, key=lambda r: r.combat_score)
        best_fragger.awards.append(MVPCategory.BEST_FRAGGER)
        best_fragger.category_ranks[MVPCategory.BEST_FRAGGER] = 1
        
        # Best Support (highest teamwork + utility)
        best_support = max(results, key=lambda r: r.teamwork_score + r.utility_score)
        best_support.awards.append(MVPCategory.BEST_SUPPORT)
        best_support.category_ranks[MVPCategory.BEST_SUPPORT] = 1
        
        # Best Clutch (highest clutch score)
        best_clutch = max(results, key=lambda r: r.clutch_score)
        if best_clutch.clutch_score > 0:
            best_clutch.awards.append(MVPCategory.BEST_CLUTCH)
            best_clutch.category_ranks[MVPCategory.BEST_CLUTCH] = 1
        
        # Best Utility
        best_utility = max(results, key=lambda r: r.utility_score)
        if best_utility.utility_score > 20:
            best_utility.awards.append(MVPCategory.BEST_UTILITY)
        
        # Overall MVP (highest overall)
        if results:
            results[0].awards.append(MVPCategory.OVERALL_MVP)
            results[0].category_ranks[MVPCategory.OVERALL_MVP] = 1
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HISTORICAL TRACKING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def update_player_averages(self, player_id: str, stats: Dict[str, float]) -> None:
        """Update player historical averages"""
        if player_id not in self.player_averages:
            self.player_averages[player_id] = {}
        
        for key, value in stats.items():
            if key in self.player_averages[player_id]:
                # Exponential moving average
                self.player_averages[player_id][key] = (
                    self.player_averages[player_id][key] * 0.7 + value * 0.3
                )
            else:
                self.player_averages[player_id][key] = value
    
    def get_player_average(self, player_id: str) -> Dict[str, float]:
        """Get player historical averages"""
        return self.player_averages.get(player_id, {})
    
    def is_personal_best(self, player_id: str, score: float) -> bool:
        """Check if this is a personal best score"""
        avg = self.player_averages.get(player_id, {})
        avg_score = avg.get("avg_mvp_score", 0)
        return score > avg_score * 1.3  # 30% above average
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_top_players(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top MVP performers"""
        sorted_mvps = sorted(
            self.tournament_mvps,
            key=lambda m: m.overall_score,
            reverse=True,
        )
        
        return [
            {
                "rank": i + 1,
                "player_name": m.player_name,
                "team_name": m.team_name,
                "score": m.overall_score,
                "kills": m.kills,
                "damage": m.damage,
                "awards": [a.value for a in m.awards],
            }
            for i, m in enumerate(sorted_mvps[:limit])
        ]
    
    def get_match_mvp(self, match_id: str) -> Optional[Dict[str, Any]]:
        """Get MVP for a specific match"""
        for mvp in self.tournament_mvps:
            if mvp.match_id == match_id:
                return {
                    "player_name": mvp.player_name,
                    "team_name": mvp.team_name,
                    "score": mvp.overall_score,
                    "kills": mvp.kills,
                    "damage": mvp.damage,
                    "awards": [a.value for a in mvp.awards],
                }
        return None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get MVP calculator statistics"""
        return {
            "total_mvps_calculated": len(self.tournament_mvps),
            "players_tracked": len(self.player_averages),
            "top_players": self.get_top_players(5),
        }
    
    def reset(self) -> None:
        """Reset calculator"""
        self.tournament_mvps.clear()
        self._log.info("MVP calculator reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("kill_confirmed")
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
