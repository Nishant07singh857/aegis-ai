# backend/app/analytics/comparative_analysis.py
"""
AEGIS AI - Comparative Analysis
Compares teams and players side-by-side with detailed metrics.
Identifies strengths, weaknesses, and matchup advantages.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.analytics.comparison")


class ComparisonMetric(str, Enum):
    """Comparison metrics"""
    KILLS = "kills"
    DAMAGE = "damage"
    SURVIVAL = "survival"
    PLACEMENT = "placement"
    ACCURACY = "accuracy"
    UTILITY = "utility"
    AGGRESSION = "aggression"
    TEAMWORK = "teamwork"
    CONSISTENCY = "consistency"
    CLUTCH = "clutch"


@dataclass
class Comparison:
    """Side-by-side comparison result"""
    comparison_id: str
    subject_a_id: str
    subject_b_id: str
    subject_a_name: str = ""
    subject_b_name: str = ""
    
    # Metrics comparison
    metrics: Dict[str, Dict[str, float]] = field(default_factory=dict)
    
    # Advantage summary
    subject_a_advantages: List[str] = field(default_factory=list)
    subject_b_advantages: List[str] = field(default_factory=list)
    
    # Overall
    overall_winner: str = ""
    confidence: float = 0.5
    
    # Head-to-head
    head_to_head_record: Dict[str, int] = field(default_factory=dict)
    
    generated_at: float = field(default_factory=time.time)


class ComparativeAnalyzer:
    """
    Compares teams and players with detailed metrics.
    
    Features:
    - Side-by-side metric comparison
    - Advantage identification
    - Head-to-head analysis
    - Overall winner prediction
    - Matchup-specific insights
    """
    
    def __init__(self):
        self.comparisons: Dict[str, Comparison] = {}
        self._log = logger.bind(component="comparative_analyzer")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # COMPARISON
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def compare_teams(
        self,
        team_a: Dict[str, Any],
        team_b: Dict[str, Any],
        head_to_head: Optional[Dict[str, Any]] = None,
    ) -> Comparison:
        """Compare two teams"""
        
        comparison = Comparison(
            comparison_id=f"compare_teams_{int(time.time())}",
            subject_a_id=team_a.get("team_id", ""),
            subject_b_id=team_b.get("team_id", ""),
            subject_a_name=team_a.get("team_name", "Team A"),
            subject_b_name=team_b.get("team_name", "Team B"),
        )
        
        # Compare metrics
        metrics_to_compare = [
            ("total_kills", "Kills", "higher"),
            ("total_damage", "Damage", "higher"),
            ("avg_placement", "Placement", "lower"),
            ("avg_survival", "Survival", "higher"),
            ("win_rate", "Win Rate", "higher"),
            ("top5_rate", "Top 5 Rate", "higher"),
        ]
        
        for key, label, direction in metrics_to_compare:
            val_a = team_a.get(key, 0)
            val_b = team_b.get(key, 0)
            
            if direction == "higher":
                advantage = "a" if val_a > val_b else "b" if val_b > val_a else "tie"
            else:
                advantage = "a" if val_a < val_b else "b" if val_b < val_a else "tie"
            
            comparison.metrics[label] = {
                "team_a": round(val_a, 1),
                "team_b": round(val_b, 1),
                "advantage": advantage,
            }
            
            if advantage == "a":
                comparison.subject_a_advantages.append(label)
            elif advantage == "b":
                comparison.subject_b_advantages.append(label)
        
        # Head-to-head
        if head_to_head:
            comparison.head_to_head_record = {
                "team_a_wins": head_to_head.get("team_a_wins", 0),
                "team_b_wins": head_to_head.get("team_b_wins", 0),
                "encounters": head_to_head.get("encounters", 0),
            }
        
        # Overall winner
        a_advantages = len(comparison.subject_a_advantages)
        b_advantages = len(comparison.subject_b_advantages)
        
        if a_advantages > b_advantages:
            comparison.overall_winner = team_a.get("team_name", "Team A")
            comparison.confidence = a_advantages / (a_advantages + b_advantages)
        elif b_advantages > a_advantages:
            comparison.overall_winner = team_b.get("team_name", "Team B")
            comparison.confidence = b_advantages / (a_advantages + b_advantages)
        else:
            comparison.overall_winner = "Too close to call"
            comparison.confidence = 0.5
        
        self.comparisons[comparison.comparison_id] = comparison
        
        return comparison
    
    def compare_players(
        self,
        player_a: Dict[str, Any],
        player_b: Dict[str, Any],
    ) -> Comparison:
        """Compare two players"""
        
        comparison = Comparison(
            comparison_id=f"compare_players_{int(time.time())}",
            subject_a_id=player_a.get("player_id", ""),
            subject_b_id=player_b.get("player_id", ""),
            subject_a_name=player_a.get("player_name", "Player A"),
            subject_b_name=player_b.get("player_name", "Player B"),
        )
        
        metrics_to_compare = [
            ("avg_kills", "Kills", "higher"),
            ("avg_damage", "Damage", "higher"),
            ("avg_survival", "Survival", "higher"),
            ("headshot_pct", "Headshot %", "higher"),
            ("clutch_rate", "Clutch Rate", "higher"),
            ("mvp_count", "MVP Awards", "higher"),
        ]
        
        for key, label, direction in metrics_to_compare:
            val_a = player_a.get(key, 0)
            val_b = player_b.get(key, 0)
            
            if direction == "higher":
                advantage = "a" if val_a > val_b else "b" if val_b > val_a else "tie"
            else:
                advantage = "a" if val_a < val_b else "b" if val_b < val_a else "tie"
            
            comparison.metrics[label] = {
                "player_a": round(val_a, 1),
                "player_b": round(val_b, 1),
                "advantage": advantage,
            }
            
            if advantage == "a":
                comparison.subject_a_advantages.append(label)
            elif advantage == "b":
                comparison.subject_b_advantages.append(label)
        
        # Overall winner
        a_advantages = len(comparison.subject_a_advantages)
        b_advantages = len(comparison.subject_b_advantages)
        
        if a_advantages > b_advantages:
            comparison.overall_winner = player_a.get("player_name", "Player A")
            comparison.confidence = a_advantages / (a_advantages + b_advantages)
        elif b_advantages > a_advantages:
            comparison.overall_winner = player_b.get("player_name", "Player B")
            comparison.confidence = b_advantages / (a_advantages + b_advantages)
        else:
            comparison.overall_winner = "Too close to call"
            comparison.confidence = 0.5
        
        self.comparisons[comparison.comparison_id] = comparison
        
        return comparison
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_comparison(self, comparison_id: str) -> Optional[Dict[str, Any]]:
        """Get comparison result"""
        comp = self.comparisons.get(comparison_id)
        if not comp:
            return None
        
        return {
            "team_a": {
                "name": comp.subject_a_name,
                "advantages": comp.subject_a_advantages,
            },
            "team_b": {
                "name": comp.subject_b_name,
                "advantages": comp.subject_b_advantages,
            },
            "metrics": comp.metrics,
            "overall_winner": comp.overall_winner,
            "confidence": round(comp.confidence, 2),
            "head_to_head": comp.head_to_head_record,
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get comparative analyzer statistics"""
        return {
            "comparisons_made": len(self.comparisons),
        }
    
    def reset(self) -> None:
        """Reset analyzer"""
        self.comparisons.clear()
        self._log.info("Comparative analyzer reset")