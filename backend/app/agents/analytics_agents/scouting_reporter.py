# backend/app/agents/analytics_agents/scouting_reporter.py
"""
AEGIS AI - Scouting Reporter
Automated talent identification and scouting reports.
Analyzes player potential, compares players, and generates
professional scouting reports for team recruitment.
"""

import time
import math
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict, deque
import structlog

logger = structlog.get_logger("aegis.agents.scouting")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PotentialLevel(str, Enum):
    """Player potential assessment"""
    S_TIER = "s_tier"           # Generational talent
    A_TIER = "a_tier"           # Elite prospect
    B_TIER = "b_tier"           # Strong prospect
    C_TIER = "c_tier"           # Solid player
    D_TIER = "d_tier"           # Developmental
    UNPROVEN = "unproven"       # Not enough data

class ScoutCategory(str, Enum):
    """Scouting evaluation categories"""
    MECHANICS = "mechanics"         # Aim, movement, reaction
    GAME_SENSE = "game_sense"       # Decision making, positioning
    CLUTCH_FACTOR = "clutch_factor" # Performance under pressure
    CONSISTENCY = "consistency"     # Reliable performance
    ADAPTABILITY = "adaptability"   # Adjusting to situations
    TEAMWORK = "teamwork"          # Team coordination
    COMMUNICATION = "communication" # Implied from behavior
    LEADERSHIP = "leadership"      # IGL potential
    GROWTH_RATE = "growth_rate"    # Improvement speed
    MENTAL_FORTITUDE = "mental"    # Mental toughness

class RecommendationType(str, Enum):
    """Scouting recommendations"""
    SIGN_IMMEDIATELY = "sign_immediately"
    STRONG_CONSIDER = "strong_consider"
    WORTH_MONITORING = "worth_monitoring"
    NEEDS_DEVELOPMENT = "needs_development"
    NOT_RECOMMENDED = "not_recommended"
    ROLE_SPECIFIC = "role_specific"

@dataclass
class ScoutReport:
    """Complete scouting report for a player"""
    player_id: str
    player_name: str
    current_team: str
    report_date: float = field(default_factory=time.time)
    
    # Overall assessment
    overall_rating: float = 0.0  # 0-100
    potential_level: PotentialLevel = PotentialLevel.UNPROVEN
    recommendation: RecommendationType = RecommendationType.WORTH_MONITORING
    
    # Category scores (0-100)
    mechanics_score: float = 0.0
    game_sense_score: float = 0.0
    clutch_score: float = 0.0
    consistency_score: float = 0.0
    adaptability_score: float = 0.0
    teamwork_score: float = 0.0
    leadership_score: float = 0.0
    growth_score: float = 0.0
    mental_score: float = 0.0
    
    # Strengths & weaknesses
    top_strengths: List[str] = field(default_factory=list)
    key_weaknesses: List[str] = field(default_factory=list)
    
    # Stats summary
    matches_analyzed: int = 0
    avg_kills: float = 0.0
    avg_damage: float = 0.0
    avg_placement: float = 0.0
    clutch_rate: float = 0.0
    headshot_pct: float = 0.0
    
    # Potential fit
    ideal_role: str = ""
    compatible_teams: List[str] = field(default_factory=list)
    playstyle_match: str = ""
    
    # Comparison
    similar_to_player: str = ""  # Player they play like
    ceiling_comparison: str = ""  # What they could become
    
    # Growth
    improvement_rate: float = 0.0
    projected_peak: float = 0.0  # Projected peak rating
    
    # Market value (relative scale)
    estimated_value: str = ""  # e.g., "High", "Medium", "Entry"
    
    # Narrative
    scout_notes: str = ""
    standout_qualities: List[str] = field(default_factory=list)
    red_flags: List[str] = field(default_factory=list)


class ScoutingReporter:
    """
    Automated talent scouting and player evaluation.
    
    Generates professional scouting reports with:
    - Multi-category player evaluation
    - Potential and growth projections
    - Role and team fit analysis
    - Comparable player identification
    - Market value estimation
    - Detailed strength/weakness analysis
    
    Used for:
    - Team recruitment decisions
    - Tournament analyst desk
    - Player development tracking
    - Draft/auction preparation
    """
    
    def __init__(self):
        # Scouting reports
        self.reports: Dict[str, ScoutReport] = {}
        
        # Report history
        self.report_history: deque = deque(maxlen=100)
        
        # Player comparison database
        self.player_database: Dict[str, Dict[str, Any]] = {}
        
        # Category weights
        self.category_weights = {
            ScoutCategory.MECHANICS: 0.20,
            ScoutCategory.GAME_SENSE: 0.20,
            ScoutCategory.CLUTCH_FACTOR: 0.15,
            ScoutCategory.CONSISTENCY: 0.15,
            ScoutCategory.ADAPTABILITY: 0.10,
            ScoutCategory.TEAMWORK: 0.10,
            ScoutCategory.GROWTH_RATE: 0.05,
            ScoutCategory.MENTAL_FORTITUDE: 0.05,
        }
        
        # Thresholds for ratings
        self.rating_thresholds = {
            PotentialLevel.S_TIER: 90,
            PotentialLevel.A_TIER: 78,
            PotentialLevel.B_TIER: 65,
            PotentialLevel.C_TIER: 50,
            PotentialLevel.D_TIER: 30,
        }
        
        self._log = logger.bind(component="scouting_reporter")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SCOUTING REPORT GENERATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def generate_report(
        self,
        player_id: str,
        player_name: str,
        current_team: str,
        match_history: List[Dict[str, Any]],
        player_profile: Optional[Dict[str, Any]] = None,
        performance_data: Optional[Dict[str, Any]] = None,
    ) -> ScoutReport:
        """
        Generate a complete scouting report.
        
        Args:
            player_id: Player identifier
            player_name: Player name
            current_team: Current team name
            match_history: List of match performances
            player_profile: Player profile data
            performance_data: Performance tracking data
            
        Returns:
            Complete ScoutReport
        """
        
        # Create report
        report = ScoutReport(
            player_id=player_id,
            player_name=player_name,
            current_team=current_team,
        )
        
        matches = len(match_history)
        report.matches_analyzed = matches
        
        if matches < 3:
            report.potential_level = PotentialLevel.UNPROVEN
            report.scout_notes = "Insufficient data for complete analysis. Need more matches."
            self.reports[player_id] = report
            return report
        
        # Calculate base stats
        self._calculate_base_stats(report, match_history)
        
        # Score each category
        report.mechanics_score = self._score_mechanics(match_history, player_profile)
        report.game_sense_score = self._score_game_sense(match_history, player_profile)
        report.clutch_score = self._score_clutch_factor(match_history)
        report.consistency_score = self._score_consistency(match_history)
        report.adaptability_score = self._score_adaptability(match_history)
        report.teamwork_score = self._score_teamwork(match_history, player_profile)
        report.leadership_score = self._score_leadership(match_history, player_profile)
        report.growth_score = self._score_growth(match_history, performance_data)
        report.mental_score = self._score_mental(match_history)
        
        # Calculate overall rating
        report.overall_rating = (
            report.mechanics_score * self.category_weights[ScoutCategory.MECHANICS] +
            report.game_sense_score * self.category_weights[ScoutCategory.GAME_SENSE] +
            report.clutch_score * self.category_weights[ScoutCategory.CLUTCH_FACTOR] +
            report.consistency_score * self.category_weights[ScoutCategory.CONSISTENCY] +
            report.adaptability_score * self.category_weights[ScoutCategory.ADAPTABILITY] +
            report.teamwork_score * self.category_weights[ScoutCategory.TEAMWORK] +
            report.growth_score * self.category_weights[ScoutCategory.GROWTH_RATE] +
            report.mental_score * self.category_weights[ScoutCategory.MENTAL_FORTITUDE]
        )
        
        # Determine potential level
        report.potential_level = self._determine_potential(report)
        
        # Identify strengths and weaknesses
        self._identify_strengths_weaknesses(report)
        
        # Determine ideal role
        report.ideal_role = self._determine_ideal_role(report, player_profile)
        
        # Find comparable players
        self._find_comparable_players(report)
        
        # Project growth
        self._project_growth(report, match_history)
        
        # Generate recommendation
        report.recommendation = self._generate_recommendation(report)
        
        # Estimate value
        report.estimated_value = self._estimate_value(report)
        
        # Generate scout notes
        report.scout_notes = self._generate_scout_notes(report)
        
        # Store report
        self.reports[player_id] = report
        self.report_history.append({
            "player_id": player_id,
            "player_name": player_name,
            "rating": report.overall_rating,
            "potential": report.potential_level.value,
            "timestamp": time.time(),
        })
        
        self._log.info(
            f"Scout report generated: {player_name}",
            rating=round(report.overall_rating, 1),
            potential=report.potential_level.value,
            recommendation=report.recommendation.value,
        )
        
        return report
    
    def _calculate_base_stats(
        self,
        report: ScoutReport,
        history: List[Dict[str, Any]],
    ) -> None:
        """Calculate base statistics from match history"""
        
        if not history:
            return
        
        report.avg_kills = sum(h.get("kills", 0) for h in history) / len(history)
        report.avg_damage = sum(h.get("damage", 0) for h in history) / len(history)
        report.avg_placement = sum(h.get("placement", 16) for h in history) / len(history)
        
        # Clutch rate
        clutch_attempts = sum(h.get("clutch_attempts", 0) for h in history)
        clutch_wins = sum(h.get("clutch_wins", 0) for h in history)
        report.clutch_rate = clutch_wins / max(1, clutch_attempts)
        
        # Headshot percentage
        total_kills = sum(h.get("kills", 0) for h in history)
        total_headshots = sum(h.get("headshots", 0) for h in history)
        report.headshot_pct = (total_headshots / max(1, total_kills)) * 100
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CATEGORY SCORING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _score_mechanics(
        self,
        history: List[Dict[str, Any]],
        profile: Optional[Dict[str, Any]],
    ) -> float:
        """Score mechanical skills (aim, movement, reactions)"""
        score = 30.0  # Base
        
        # Kill efficiency
        avg_kills = sum(h.get("kills", 0) for h in history) / len(history)
        score += min(30, avg_kills * 6)
        
        # Damage output
        avg_damage = sum(h.get("damage", 0) for h in history) / len(history)
        score += min(25, avg_damage / 40)
        
        # Headshot accuracy
        total_kills = sum(h.get("kills", 0) for h in history)
        total_hs = sum(h.get("headshots", 0) for h in history)
        hs_pct = (total_hs / max(1, total_kills)) * 100
        score += min(15, hs_pct / 2)
        
        return min(100, score)
    
    def _score_game_sense(
        self,
        history: List[Dict[str, Any]],
        profile: Optional[Dict[str, Any]],
    ) -> float:
        """Score game sense and decision making"""
        score = 30.0
        
        # Survival time (indicates smart positioning)
        avg_survival = sum(h.get("survival_time", 0) for h in history) / len(history)
        survival_minutes = avg_survival / 60
        score += min(25, survival_minutes * 1.5)
        
        # Placement consistency
        placements = [h.get("placement", 16) for h in history]
        avg_placement = sum(placements) / len(placements)
        score += min(25, (17 - avg_placement) * 2)
        
        # Death zone phase (dying later = better game sense)
        zone_phases = [h.get("zone_phase_at_death", 0) for h in history]
        avg_zone = sum(zone_phases) / len(zone_phases)
        score += min(20, avg_zone * 3)
        
        return min(100, score)
    
    def _score_clutch_factor(self, history: List[Dict[str, Any]]) -> float:
        """Score clutch performance"""
        score = 20.0
        
        clutch_attempts = sum(h.get("clutch_attempts", 0) for h in history)
        clutch_wins = sum(h.get("clutch_wins", 0) for h in history)
        
        if clutch_attempts >= 3:
            clutch_rate = clutch_wins / clutch_attempts
            score += clutch_rate * 60
        
        # Bonus for attempting clutches (shows willingness)
        score += min(20, clutch_attempts * 4)
        
        return min(100, score)
    
    def _score_consistency(self, history: List[Dict[str, Any]]) -> float:
        """Score performance consistency"""
        score = 50.0
        
        if len(history) < 3:
            return score
        
        kills = [h.get("kills", 0) for h in history]
        mean_kills = sum(kills) / len(kills)
        
        if mean_kills > 0:
            variance = sum((k - mean_kills) ** 2 for k in kills) / len(kills)
            cv = math.sqrt(variance) / mean_kills  # Coefficient of variation
            
            # Lower CV = more consistent
            if cv < 0.3:
                score += 40
            elif cv < 0.5:
                score += 25
            elif cv < 0.8:
                score += 10
            else:
                score -= 15
        
        return min(100, max(0, score))
    
    def _score_adaptability(self, history: List[Dict[str, Any]]) -> float:
        """Score adaptability to different situations"""
        score = 30.0
        
        # Weapon variety
        all_weapons = set()
        for h in history:
            weapons = h.get("weapons_used", [])
            all_weapons.update(weapons)
        score += min(25, len(all_weapons) * 3)
        
        # Performance in different zone phases
        zone_performances = defaultdict(list)
        for h in history:
            zone = h.get("zone_phase_at_death", 0)
            zone_performances[zone].append(h.get("kills", 0))
        
        # Good adaptability = performs well across zones
        zones_with_kills = sum(
            1 for kills in zone_performances.values()
            if sum(kills) / len(kills) > 1
        )
        score += min(25, zones_with_kills * 5)
        
        # Comeback ability
        comebacks = sum(1 for h in history if h.get("comeback", False))
        score += min(20, comebacks * 10)
        
        return min(100, score)
    
    def _score_teamwork(
        self,
        history: List[Dict[str, Any]],
        profile: Optional[Dict[str, Any]],
    ) -> float:
        """Score teamwork and coordination"""
        score = 25.0
        
        # Assists
        avg_assists = sum(h.get("assists", 0) for h in history) / len(history)
        score += min(25, avg_assists * 8)
        
        # Revives
        avg_revives = sum(h.get("revives", 0) for h in history) / len(history)
        score += min(25, avg_revives * 10)
        
        # Team damage share (not hogging kills)
        damage_shares = [h.get("team_damage_share", 0.25) for h in history]
        avg_share = sum(damage_shares) / len(damage_shares)
        if 0.2 <= avg_share <= 0.35:  # Balanced contribution
            score += 25
        elif avg_share > 0.5:  # Carrying team
            score += 10
        
        return min(100, score)
    
    def _score_leadership(
        self,
        history: List[Dict[str, Any]],
        profile: Optional[Dict[str, Any]],
    ) -> float:
        """Score leadership potential"""
        score = 20.0
        
        # Survival (IGLs tend to survive longer)
        avg_survival = sum(h.get("survival_time", 0) for h in history) / len(history)
        score += min(20, avg_survival / 60)
        
        # Team performance correlation
        team_placements = [h.get("team_placement", 16) for h in history]
        avg_team_placement = sum(team_placements) / len(team_placements)
        score += min(25, (17 - avg_team_placement) * 2)
        
        # Assist/revive ratio (leaders enable others)
        assists = sum(h.get("assists", 0) for h in history)
        kills = sum(h.get("kills", 0) for h in history)
        if kills > 0:
            assist_ratio = assists / kills
            score += min(20, assist_ratio * 20)
        
        # Experience
        score += min(15, len(history) * 0.5)
        
        return min(100, score)
    
    def _score_growth(
        self,
        history: List[Dict[str, Any]],
        perf_data: Optional[Dict[str, Any]],
    ) -> float:
        """Score growth rate and improvement"""
        score = 30.0
        
        if len(history) < 5:
            return score
        
        # Split into first half and second half
        mid = len(history) // 2
        first_half = history[:mid]
        second_half = history[mid:]
        
        first_avg = sum(h.get("kills", 0) for h in first_half) / len(first_half)
        second_avg = sum(h.get("kills", 0) for h in second_half) / len(second_half)
        
        improvement = ((second_avg - first_avg) / max(0.1, first_avg)) * 100
        
        if improvement > 30:
            score += 50
        elif improvement > 15:
            score += 35
        elif improvement > 5:
            score += 20
        elif improvement > 0:
            score += 10
        
        # Consistency of improvement
        if perf_data:
            improvement_rate = perf_data.get("improvement_rate", 0)
            score += min(20, improvement_rate * 100)
        
        return min(100, score)
    
    def _score_mental(self, history: List[Dict[str, Any]]) -> float:
        """Score mental fortitude"""
        score = 30.0
        
        # Performance after deaths (bounce back)
        deaths = [i for i, h in enumerate(history) if h.get("placement", 16) > 10]
        bounce_backs = 0
        for death_idx in deaths:
            if death_idx + 1 < len(history):
                next_match = history[death_idx + 1]
                if next_match.get("kills", 0) > next_match.get("prev_avg_kills", 0):
                    bounce_backs += 1
        
        if deaths:
            bounce_rate = bounce_backs / len(deaths)
            score += bounce_rate * 40
        
        # Clutch performance (pressure handling)
        clutch_attempts = sum(h.get("clutch_attempts", 0) for h in history)
        clutch_wins = sum(h.get("clutch_wins", 0) for h in history)
        if clutch_attempts > 0:
            score += (clutch_wins / clutch_attempts) * 30
        
        return min(100, score)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ANALYSIS & RECOMMENDATIONS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _determine_potential(self, report: ScoutReport) -> PotentialLevel:
        """Determine player potential level"""
        rating = report.overall_rating
        
        if rating >= self.rating_thresholds[PotentialLevel.S_TIER]:
            return PotentialLevel.S_TIER
        elif rating >= self.rating_thresholds[PotentialLevel.A_TIER]:
            return PotentialLevel.A_TIER
        elif rating >= self.rating_thresholds[PotentialLevel.B_TIER]:
            return PotentialLevel.B_TIER
        elif rating >= self.rating_thresholds[PotentialLevel.C_TIER]:
            return PotentialLevel.C_TIER
        elif rating >= self.rating_thresholds[PotentialLevel.D_TIER]:
            return PotentialLevel.D_TIER
        
        return PotentialLevel.UNPROVEN
    
    def _identify_strengths_weaknesses(self, report: ScoutReport) -> None:
        """Identify top strengths and key weaknesses"""
        
        category_scores = {
            "Mechanics": report.mechanics_score,
            "Game Sense": report.game_sense_score,
            "Clutch Factor": report.clutch_score,
            "Consistency": report.consistency_score,
            "Adaptability": report.adaptability_score,
            "Teamwork": report.teamwork_score,
            "Leadership": report.leadership_score,
            "Growth Rate": report.growth_score,
            "Mental Fortitude": report.mental_score,
        }
        
        # Top 3 strengths
        strengths = sorted(category_scores.items(), key=lambda x: x[1], reverse=True)
        report.top_strengths = [
            f"{name} ({score:.0f}/100)"
            for name, score in strengths[:3]
            if score >= 60
        ]
        
        # Key weaknesses (lowest scores)
        weaknesses = sorted(category_scores.items(), key=lambda x: x[1])
        report.key_weaknesses = [
            f"{name} ({score:.0f}/100)"
            for name, score in weaknesses[:3]
            if score < 50
        ]
    
    def _determine_ideal_role(
        self,
        report: ScoutReport,
        profile: Optional[Dict[str, Any]],
    ) -> str:
        """Determine ideal role for the player"""
        
        scores = {
            "Entry Fragger": report.mechanics_score * 0.6 + report.clutch_score * 0.4,
            "Support": report.teamwork_score * 0.7 + report.game_sense_score * 0.3,
            "IGL": report.leadership_score * 0.5 + report.game_sense_score * 0.5,
            "Sniper": report.mechanics_score * 0.4 + report.consistency_score * 0.6,
            "Anchor": report.game_sense_score * 0.6 + report.consistency_score * 0.4,
            "Flex": report.adaptability_score * 0.7 + report.mechanics_score * 0.3,
        }
        
        best_role = max(scores, key=scores.get)
        return best_role
    
    def _find_comparable_players(self, report: ScoutReport) -> None:
        """Find similar players in the database"""
        
        if not self.player_database:
            return
        
        best_match = None
        best_similarity = -1
        
        for pid, data in self.player_database.items():
            if pid == report.player_id:
                continue
            
            # Calculate similarity based on key metrics
            similarity = 0
            
            db_kills = data.get("avg_kills", 0)
            if report.avg_kills > 0 and db_kills > 0:
                kill_ratio = min(report.avg_kills, db_kills) / max(report.avg_kills, db_kills)
                similarity += kill_ratio * 30
            
            db_rating = data.get("overall_rating", 0)
            if report.overall_rating > 0 and db_rating > 0:
                rating_ratio = min(report.overall_rating, db_rating) / max(report.overall_rating, db_rating)
                similarity += rating_ratio * 40
            
            db_role = data.get("ideal_role", "")
            if db_role == report.ideal_role:
                similarity += 30
            
            if similarity > best_similarity:
                best_similarity = similarity
                best_match = data.get("player_name", "")
        
        if best_match and best_similarity > 50:
            report.similar_to_player = best_match
    
    def _project_growth(
        self,
        report: ScoutReport,
        history: List[Dict[str, Any]],
    ) -> None:
        """Project future growth and peak rating"""
        
        if len(history) < 5:
            report.projected_peak = report.overall_rating
            return
        
        # Calculate growth rate
        mid = len(history) // 2
        first_half = history[:mid]
        second_half = history[mid:]
        
        first_avg = sum(h.get("kills", 0) for h in first_half) / len(first_half)
        second_avg = sum(h.get("kills", 0) for h in second_half) / len(second_half)
        
        if first_avg > 0:
            growth_per_match = ((second_avg - first_avg) / first_avg) / mid
            report.improvement_rate = growth_per_match
            
            # Project peak (max realistic improvement over 50 matches)
            remaining_growth = min(50, 100 - report.overall_rating)
            projected_improvement = growth_per_match * 50 * 50  # 50 matches, scaled
            report.projected_peak = min(99, report.overall_rating + projected_improvement)
        else:
            report.projected_peak = report.overall_rating
    
    def _generate_recommendation(self, report: ScoutReport) -> RecommendationType:
        """Generate scouting recommendation"""
        
        rating = report.overall_rating
        
        if rating >= 85:
            return RecommendationType.SIGN_IMMEDIATELY
        elif rating >= 72:
            return RecommendationType.STRONG_CONSIDER
        elif rating >= 55:
            if report.growth_score > 70:
                return RecommendationType.STRONG_CONSIDER
            return RecommendationType.WORTH_MONITORING
        elif rating >= 40:
            if report.growth_score > 75:
                return RecommendationType.WORTH_MONITORING
            return RecommendationType.NEEDS_DEVELOPMENT
        else:
            return RecommendationType.NOT_RECOMMENDED
    
    def _estimate_value(self, report: ScoutReport) -> str:
        """Estimate player market value"""
        rating = report.overall_rating
        
        if rating >= 90:
            return "Franchise Player"
        elif rating >= 78:
            return "Premium"
        elif rating >= 65:
            return "High Value"
        elif rating >= 50:
            return "Solid Investment"
        elif rating >= 35:
            return "Developmental"
        else:
            return "Entry Level"
    
    def _generate_scout_notes(self, report: ScoutReport) -> str:
        """Generate narrative scout notes"""
        
        notes_parts = []
        
        # Overall impression
        if report.overall_rating >= 80:
            notes_parts.append(f"Elite talent with exceptional {report.top_strengths[0] if report.top_strengths else 'skills'}.")
        elif report.overall_rating >= 60:
            notes_parts.append(f"Solid player showing promise in {report.top_strengths[0] if report.top_strengths else 'multiple areas'}.")
        else:
            notes_parts.append("Developing player who needs more experience.")
        
        # Growth potential
        if report.growth_score > 70:
            notes_parts.append("High growth trajectory - improving rapidly.")
        
        # Key concern
        if report.key_weaknesses:
            notes_parts.append(f"Primary area for improvement: {report.key_weaknesses[0]}.")
        
        # Role fit
        notes_parts.append(f"Best suited as {report.ideal_role}.")
        
        return " ".join(notes_parts)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # COMPARISON
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def compare_players(
        self,
        player_a_id: str,
        player_b_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Compare two players side by side"""
        
        report_a = self.reports.get(player_a_id)
        report_b = self.reports.get(player_b_id)
        
        if not report_a or not report_b:
            return None
        
        return {
            "player_a": {
                "name": report_a.player_name,
                "team": report_a.current_team,
                "rating": round(report_a.overall_rating, 1),
                "potential": report_a.potential_level.value,
                "strengths": report_a.top_strengths,
            },
            "player_b": {
                "name": report_b.player_name,
                "team": report_b.current_team,
                "rating": round(report_b.overall_rating, 1),
                "potential": report_b.potential_level.value,
                "strengths": report_b.top_strengths,
            },
            "comparison": {
                "better_rated": report_a.player_name if report_a.overall_rating > report_b.overall_rating else report_b.player_name,
                "better_mechanics": report_a.player_name if report_a.mechanics_score > report_b.mechanics_score else report_b.player_name,
                "better_game_sense": report_a.player_name if report_a.game_sense_score > report_b.game_sense_score else report_b.player_name,
                "better_clutch": report_a.player_name if report_a.clutch_score > report_b.clutch_score else report_b.player_name,
                "better_teamwork": report_a.player_name if report_a.teamwork_score > report_b.teamwork_score else report_b.player_name,
            },
        }
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_report(self, player_id: str) -> Optional[Dict[str, Any]]:
        """Get scouting report summary"""
        report = self.reports.get(player_id)
        if not report:
            return None
        
        return {
            "player_name": report.player_name,
            "team": report.current_team,
            "overall_rating": round(report.overall_rating, 1),
            "potential": report.potential_level.value,
            "recommendation": report.recommendation.value,
            "ideal_role": report.ideal_role,
            "estimated_value": report.estimated_value,
            "strengths": report.top_strengths,
            "weaknesses": report.key_weaknesses,
            "stats": {
                "avg_kills": round(report.avg_kills, 1),
                "avg_damage": round(report.avg_damage, 0),
                "avg_placement": round(report.avg_placement, 1),
                "clutch_rate": round(report.clutch_rate * 100, 1),
                "headshot_pct": round(report.headshot_pct, 1),
            },
            "projected_peak": round(report.projected_peak, 1),
            "matches_analyzed": report.matches_analyzed,
            "scout_notes": report.scout_notes,
        }
    
    def get_top_prospects(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top scouted prospects"""
        sorted_reports = sorted(
            self.reports.values(),
            key=lambda r: r.overall_rating,
            reverse=True,
        )[:limit]
        
        return [
            {
                "name": r.player_name,
                "team": r.current_team,
                "rating": round(r.overall_rating, 1),
                "potential": r.potential_level.value,
                "recommendation": r.recommendation.value,
                "ideal_role": r.ideal_role,
            }
            for r in sorted_reports
        ]
    
    def get_reports_by_potential(self, level: PotentialLevel) -> List[Dict[str, Any]]:
        """Get players at a specific potential level"""
        return [
            {
                "name": r.player_name,
                "team": r.current_team,
                "rating": round(r.overall_rating, 1),
                "role": r.ideal_role,
            }
            for r in self.reports.values()
            if r.potential_level == level
        ]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get scouting reporter statistics"""
        return {
            "total_reports": len(self.reports),
            "top_prospects": self.get_top_prospects(5),
            "by_potential": {
                level.value: len(self.get_reports_by_potential(level))
                for level in PotentialLevel
            },
        }
    
    def reset(self) -> None:
        """Reset scouting reporter"""
        self.reports.clear()
        self.report_history.clear()
        self._log.info("Scouting reporter reset")