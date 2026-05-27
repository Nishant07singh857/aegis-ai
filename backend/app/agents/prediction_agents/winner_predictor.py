# backend/app/agents/prediction_agents/winner_predictor.py
"""
AEGIS AI - Winner Predictor
Predicts the overall match winner based on team performance,
positioning, momentum, and historical data.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from collections import deque
import structlog

logger = structlog.get_logger("aegis.agents.winner_predictor")

@dataclass
class WinnerPrediction:
    """Match winner prediction"""
    prediction_id: str
    match_id: str
    timestamp: float = field(default_factory=time.time)
    
    # Rankings with probabilities
    team_predictions: List[Dict[str, Any]] = field(default_factory=list)
    
    # Top 3 prediction
    predicted_winner: str = ""
    predicted_runner_up: str = ""
    predicted_third: str = ""
    
    # Confidence
    overall_confidence: float = 0.0
    
    # Context
    teams_alive: int = 16
    zone_phase: int = 0
    match_time: float = 0.0


class WinnerPredictor:
    """
    Predicts overall match winner.
    
    Factors:
    1. Current placement and kills
    2. Survival probability
    3. Team strength and momentum
    4. Position quality
    5. Historical performance
    6. Head-to-head advantages
    """
    
    def __init__(self):
        self.predictions: deque = deque(maxlen=50)
        self.team_ratings: Dict[str, float] = {}
        self._log = logger.bind(component="winner_predictor")
    
    def predict_winner(
        self,
        match_id: str,
        teams: List[Dict[str, Any]],
        match_context: Dict[str, Any],
    ) -> WinnerPrediction:
        """Predict match winner"""
        
        pred = WinnerPrediction(
            prediction_id=f"win_pred_{int(time.time())}",
            match_id=match_id,
            teams_alive=match_context.get("teams_alive", len(teams)),
            zone_phase=match_context.get("zone_phase", 0),
            match_time=match_context.get("match_time", 0),
        )
        
        # Score each team
        team_scores = []
        for team in teams:
            score = self._score_team(team, match_context)
            team_scores.append({
                "team_id": team.get("team_id", ""),
                "team_name": team.get("team_name", "Unknown"),
                "score": score,
                "alive_count": team.get("alive_count", 0),
                "kills": team.get("kills", 0),
                "in_zone": team.get("in_zone", True),
            })
        
        # Sort by score
        team_scores.sort(key=lambda t: t["score"], reverse=True)
        
        # Calculate win probabilities
        total_score = sum(t["score"] for t in team_scores)
        
        if total_score > 0:
            for team in team_scores:
                team["win_probability"] = round((team["score"] / total_score) * 100, 1)
        
        pred.team_predictions = team_scores
        
        # Set top 3
        if len(team_scores) >= 1:
            pred.predicted_winner = team_scores[0]["team_name"]
        if len(team_scores) >= 2:
            pred.predicted_runner_up = team_scores[1]["team_name"]
        if len(team_scores) >= 3:
            pred.predicted_third = team_scores[2]["team_name"]
        
        # Confidence
        if team_scores:
            top_score = team_scores[0]["score"]
            second_score = team_scores[1]["score"] if len(team_scores) > 1 else 0
            if second_score > 0:
                pred.overall_confidence = round(min(90, (top_score - second_score) / top_score * 100), 1)
            else:
                pred.overall_confidence = 80
        
        self.predictions.append(pred)
        
        self._log.info(
            f"Winner predicted: {pred.predicted_winner}",
            confidence=pred.overall_confidence,
        )
        
        return pred
    
    def _score_team(
        self,
        team: Dict[str, Any],
        context: Dict[str, Any],
    ) -> float:
        """Score a team's chances"""
        score = 100.0  # Base
        
        # Alive players (most important)
        alive = team.get("alive_count", 0)
        score *= (alive / 4)  # Full squad = full multiplier
        
        # Kills indicate momentum
        kills = team.get("kills", 0)
        score += kills * 3
        
        # Zone position
        if team.get("in_zone", True):
            score += 15
        else:
            score -= 20
        
        # Compound holding
        if team.get("in_compound", False):
            score += 10
        
        # High ground
        if team.get("has_high_ground", False):
            score += 10
        
        # HP percentage
        hp_pct = team.get("hp_percent", 100)
        score += (hp_pct - 50) * 0.3
        
        # Team rating
        team_id = team.get("team_id", "")
        rating = self.team_ratings.get(team_id, 50)
        score += (rating - 50) * 0.2
        
        # Late game bonus (teams that survive long are stronger)
        if context.get("zone_phase", 0) >= 5:
            score += 10
        
        return max(10, score)
    
    def get_winner_prediction(self) -> Optional[Dict[str, Any]]:
        """Get latest winner prediction"""
        if not self.predictions:
            return None
        
        pred = self.predictions[-1]
        return {
            "predicted_winner": pred.predicted_winner,
            "runner_up": pred.predicted_runner_up,
            "third": pred.predicted_third,
            "confidence": pred.overall_confidence,
            "top5": pred.team_predictions[:5],
            "teams_alive": pred.teams_alive,
            "zone_phase": pred.zone_phase,
        }
    
    def set_team_rating(self, team_id: str, rating: float) -> None:
        """Set team rating for predictions"""
        self.team_ratings[team_id] = rating
    
    def get_stats(self) -> Dict[str, Any]:
        """Get predictor statistics"""
        return {
            "predictions_made": len(self.predictions),
            "latest": self.get_winner_prediction(),
            "teams_rated": len(self.team_ratings),
        }
    
    def reset(self) -> None:
        """Reset predictor"""
        self.predictions.clear()
        self._log.info("Winner predictor reset")