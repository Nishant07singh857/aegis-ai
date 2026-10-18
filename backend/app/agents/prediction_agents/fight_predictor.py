# backend/app/agents/prediction_agents/fight_predictor.py
"""
AEGIS AI - Fight Predictor
Predicts fight outcomes in real-time using multiple factors:
HP comparison, positioning, weapons, utility, player skill, and history.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.fight_predictor")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PredictionConfidence(str, Enum):
    """Confidence levels for predictions"""
    VERY_HIGH = "very_high"     # >90% confidence
    HIGH = "high"              # 75-90%
    MODERATE = "moderate"      # 50-75%
    LOW = "low"               # 25-50%
    SPECULATIVE = "speculative" # <25%

class FightOutcome(str, Enum):
    """Possible fight outcomes"""
    TEAM_A_WIN = "team_a_win"
    TEAM_B_WIN = "team_b_win"
    TRADE = "trade"           # Both teams lose players
    DISENGAGE = "disengage"   # Fight ends without clear winner
    THIRD_PARTY = "third_party" # Third team intervenes
    ONGOING = "ongoing"

@dataclass
class FightPrediction:
    """Complete fight outcome prediction"""
    prediction_id: str
    fight_id: str
    timestamp: float = field(default_factory=time.time)
    
    # Teams
    team_a_id: str = ""
    team_b_id: str = ""
    team_a_name: str = ""
    team_b_name: str = ""
    
    # Predicted outcome
    predicted_winner: str = ""
    predicted_outcome: FightOutcome = FightOutcome.ONGOING
    
    # Win probabilities (0-100)
    team_a_win_probability: float = 50.0
    team_b_win_probability: float = 50.0
    trade_probability: float = 0.0
    disengage_probability: float = 0.0
    
    # Confidence
    confidence: float = 0.0
    confidence_level: PredictionConfidence = PredictionConfidence.LOW
    
    # Factors considered
    factors: Dict[str, float] = field(default_factory=dict)
    
    # Key advantages
    team_a_advantages: List[str] = field(default_factory=list)
    team_b_advantages: List[str] = field(default_factory=list)
    
    # Context
    zone_phase: int = 0
    fight_duration: float = 0.0
    teams_alive: int = 16
    
    # Historical accuracy tracking
    actual_outcome: Optional[str] = None
    was_correct: Optional[bool] = None
    
    # Model info
    model_version: str = "1.0"
    feature_count: int = 0


class FightPredictor(BaseAgent):
    """
    Predicts fight outcomes in real-time.
    
    Factors considered:
    1. HP Comparison (25%): Total HP, armor, healing capacity
    2. Positioning (20%): Cover, high ground, zone position
    3. Weapon Advantage (15%): Weapon types, attachments
    4. Utility (15%): Grenades, smokes, molotovs available
    5. Player Skill (15%): Historical performance, clutch ability
    6. Numbers (10%): Players alive, knocked status
    
    Updates predictions in real-time as fights evolve.
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        # Active predictions
        self.active_predictions: Dict[str, FightPrediction] = {}
        
        # Prediction history
        self.prediction_history: deque = deque(maxlen=200)
        
        # Accuracy tracking
        self.total_predictions: int = 0
        self.correct_predictions: int = 0
        self.accuracy: float = 0.0
        
        # Factor weights
        self.weights = {
            "hp_comparison": 0.25,
            "positioning": 0.20,
            "weapons": 0.15,
            "utility": 0.15,
            "player_skill": 0.15,
            "numbers": 0.10,
        }
        
        # Player skill database (would be populated from player profiler)
        self.player_skills: Dict[str, Dict[str, float]] = {}
        
        self._log = logger.bind(component="fight_predictor")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PREDICTION GENERATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def predict_fight(
        self,
        fight_id: str,
        team_a: Dict[str, Any],
        team_b: Dict[str, Any],
        context: Dict[str, Any],
    ) -> FightPrediction:
        """
        Predict the outcome of a fight.
        
        Args:
            fight_id: Fight identifier
            team_a: Team A state (players, HP, positions, etc.)
            team_b: Team B state
            context: Match context (zone, teams alive, etc.)
            
        Returns:
            FightPrediction with win probabilities
        """
        
        prediction = FightPrediction(
            prediction_id=f"pred_{int(time.time())}_{fight_id[:8]}",
            fight_id=fight_id,
            team_a_id=team_a.get("team_id", ""),
            team_b_id=team_b.get("team_id", ""),
            team_a_name=team_a.get("team_name", "Team A"),
            team_b_name=team_b.get("team_name", "Team B"),
            zone_phase=context.get("zone_phase", 0),
            teams_alive=context.get("teams_alive", 16),
        )
        
        # Calculate each factor
        factors = {}
        
        # 1. HP Comparison
        hp_factor = self._calculate_hp_advantage(team_a, team_b)
        factors["hp_comparison"] = hp_factor
        
        # 2. Positioning
        pos_factor = self._calculate_positioning_advantage(team_a, team_b, context)
        factors["positioning"] = pos_factor
        
        # 3. Weapons
        weapon_factor = self._calculate_weapon_advantage(team_a, team_b)
        factors["weapons"] = weapon_factor
        
        # 4. Utility
        utility_factor = self._calculate_utility_advantage(team_a, team_b)
        factors["utility"] = utility_factor
        
        # 5. Player Skill
        skill_factor = self._calculate_skill_advantage(team_a, team_b)
        factors["player_skill"] = skill_factor
        
        # 6. Numbers
        numbers_factor = self._calculate_numbers_advantage(team_a, team_b)
        factors["numbers"] = numbers_factor
        
        prediction.factors = factors
        
        # Calculate weighted win probability for Team A
        team_a_score = sum(
            factors[key] * self.weights[key]
            for key in factors
        )
        
        # Convert to probability (0-100)
        # Team A score > 0 means Team A advantage
        team_a_prob = 50 + team_a_score * 0.5  # Scale appropriately
        team_a_prob = max(5, min(95, team_a_prob))  # Clamp
        
        prediction.team_a_win_probability = round(team_a_prob, 1)
        prediction.team_b_win_probability = round(100 - team_a_prob, 1)
        
        # Determine predicted winner
        if team_a_prob >= 55:
            prediction.predicted_winner = team_a.get("team_id", "")
            prediction.predicted_outcome = FightOutcome.TEAM_A_WIN
        elif team_a_prob <= 45:
            prediction.predicted_winner = team_b.get("team_id", "")
            prediction.predicted_outcome = FightOutcome.TEAM_B_WIN
        else:
            prediction.predicted_winner = ""
            prediction.predicted_outcome = FightOutcome.TRADE
        
        # Calculate confidence
        margin = abs(team_a_prob - 50)
        prediction.confidence = margin * 2  # 0-100 scale
        
        if prediction.confidence >= 80:
            prediction.confidence_level = PredictionConfidence.VERY_HIGH
        elif prediction.confidence >= 60:
            prediction.confidence_level = PredictionConfidence.HIGH
        elif prediction.confidence >= 35:
            prediction.confidence_level = PredictionConfidence.MODERATE
        elif prediction.confidence >= 15:
            prediction.confidence_level = PredictionConfidence.LOW
        else:
            prediction.confidence_level = PredictionConfidence.SPECULATIVE
        
        # Identify advantages
        prediction.team_a_advantages = [
            key.replace("_", " ").title()
            for key, value in factors.items()
            if value > 5
        ]
        prediction.team_b_advantages = [
            key.replace("_", " ").title()
            for key, value in factors.items()
            if value < -5
        ]
        
        # Store prediction
        self.active_predictions[fight_id] = prediction
        
        self._log.info(
            f"Fight predicted: {prediction.team_a_name} vs {prediction.team_b_name}",
            winner=prediction.predicted_winner,
            confidence=prediction.confidence_level.value,
        )
        
        return prediction
    
    def update_prediction(
        self,
        fight_id: str,
        team_a: Dict[str, Any],
        team_b: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[FightPrediction]:
        """Update an existing prediction with new information"""
        
        prediction = self.active_predictions.get(fight_id)
        if not prediction:
            return self.predict_fight(fight_id, team_a, team_b, context)
        
        # Recalculate with updated data
        new_prediction = self.predict_fight(fight_id, team_a, team_b, context)
        new_prediction.prediction_id = prediction.prediction_id
        
        # Track changes
        old_prob = prediction.team_a_win_probability
        new_prob = new_prediction.team_a_win_probability
        shift = new_prob - old_prob
        
        if abs(shift) > 10:
            self._log.info(
                f"Prediction shift: {shift:+.1f}%",
                fight_id=fight_id,
                old_winner=prediction.predicted_winner,
                new_winner=new_prediction.predicted_winner if shift > 0 else prediction.predicted_winner,
            )
        
        self.active_predictions[fight_id] = new_prediction
        return new_prediction
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FACTOR CALCULATIONS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _calculate_hp_advantage(
        self,
        team_a: Dict[str, Any],
        team_b: Dict[str, Any],
    ) -> float:
        """Calculate HP advantage (returns positive for Team A advantage)"""
        
        # Get player HP totals
        team_a_players = team_a.get("players", [])
        team_b_players = team_b.get("players", [])
        
        team_a_hp = sum(
            p.get("hp", 0) + p.get("armor", 0) * 0.5
            for p in team_a_players
            if p.get("state") == "alive"
        )
        
        team_b_hp = sum(
            p.get("hp", 0) + p.get("armor", 0) * 0.5
            for p in team_b_players
            if p.get("state") == "alive"
        )
        
        # Calculate advantage
        total_hp = team_a_hp + team_b_hp
        if total_hp == 0:
            return 0
        
        team_a_share = team_a_hp / total_hp
        # Convert to advantage score (-50 to +50)
        return (team_a_share - 0.5) * 100
    
    def _calculate_positioning_advantage(
        self,
        team_a: Dict[str, Any],
        team_b: Dict[str, Any],
        context: Dict[str, Any],
    ) -> float:
        """Calculate positioning advantage"""
        score = 0
        
        # Cover advantage
        team_a_cover = team_a.get("cover_quality", 50)
        team_b_cover = team_b.get("cover_quality", 50)
        score += (team_a_cover - team_b_cover) * 0.15
        
        # High ground
        team_a_high = team_a.get("has_high_ground", False)
        team_b_high = team_b.get("has_high_ground", False)
        if team_a_high and not team_b_high:
            score += 15
        elif team_b_high and not team_a_high:
            score -= 15
        
        # Zone position
        team_a_in_zone = team_a.get("in_zone", True)
        team_b_in_zone = team_b.get("in_zone", True)
        if team_a_in_zone and not team_b_in_zone:
            score += 10
        
        # Compound
        team_a_compound = team_a.get("in_compound", False)
        team_b_compound = team_b.get("in_compound", False)
        if team_a_compound:
            score += 8
        if team_b_compound:
            score -= 8
        
        return max(-30, min(30, score))
    
    def _calculate_weapon_advantage(
        self,
        team_a: Dict[str, Any],
        team_b: Dict[str, Any],
    ) -> float:
        """Calculate weapon advantage"""
        score = 0
        
        # Weapon tier scoring
        weapon_tiers = {
            "AWM": 10, "GROZA": 9, "MK14": 9, "AUG": 8,
            "M416": 7, "AKM": 7, "M762": 7, "SCAR-L": 6,
            "Mini14": 6, "SKS": 6, "SLR": 7, "M24": 8,
            "UMP45": 5, "Vector": 6, "UZI": 4, "MP5K": 5,
            "DP-28": 6, "M249": 7, "DBS": 6, "S12K": 4,
        }
        
        team_a_weapons = team_a.get("weapons", [])
        team_b_weapons = team_b.get("weapons", [])
        
        team_a_score = sum(weapon_tiers.get(w, 3) for w in team_a_weapons[:4])
        team_b_score = sum(weapon_tiers.get(w, 3) for w in team_b_weapons[:4])
        
        max_score = max(team_a_score, team_b_score)
        if max_score > 0:
            score = ((team_a_score - team_b_score) / max_score) * 20
        
        return max(-15, min(15, score))
    
    def _calculate_utility_advantage(
        self,
        team_a: Dict[str, Any],
        team_b: Dict[str, Any],
    ) -> float:
        """Calculate utility advantage"""
        score = 0
        
        team_a_util = team_a.get("utility", {})
        team_b_util = team_b.get("utility", {})
        
        # Grenades are most impactful
        team_a_nades = team_a_util.get("grenades", 0)
        team_b_nades = team_b_util.get("grenades", 0)
        score += (team_a_nades - team_b_nades) * 5
        
        # Smokes for pushes
        team_a_smokes = team_a_util.get("smokes", 0)
        team_b_smokes = team_b_util.get("smokes", 0)
        score += (team_a_smokes - team_b_smokes) * 2
        
        # Molotovs
        team_a_molotovs = team_a_util.get("molotovs", 0)
        team_b_molotovs = team_b_util.get("molotovs", 0)
        score += (team_a_molotovs - team_b_molotovs) * 3
        
        return max(-15, min(15, score))
    
    def _calculate_skill_advantage(
        self,
        team_a: Dict[str, Any],
        team_b: Dict[str, Any],
    ) -> float:
        """Calculate player skill advantage"""
        score = 0
        
        # Average skill of alive players
        team_a_players = team_a.get("players", [])
        team_b_players = team_b.get("players", [])
        
        team_a_skill = 0
        for p in team_a_players:
            if p.get("state") == "alive":
                player_skill = self.player_skills.get(
                    p.get("player_id", ""), {}
                ).get("overall", 50)
                team_a_skill += player_skill
        
        team_b_skill = 0
        for p in team_b_players:
            if p.get("state") == "alive":
                player_skill = self.player_skills.get(
                    p.get("player_id", ""), {}
                ).get("overall", 50)
                team_b_skill += player_skill
        
        team_a_avg = team_a_skill / max(1, len([p for p in team_a_players if p.get("state") == "alive"]))
        team_b_avg = team_b_skill / max(1, len([p for p in team_b_players if p.get("state") == "alive"]))
        
        skill_diff = team_a_avg - team_b_avg
        score = skill_diff * 0.3  # Scale to reasonable range
        
        return max(-15, min(15, score))
    
    def _calculate_numbers_advantage(
        self,
        team_a: Dict[str, Any],
        team_b: Dict[str, Any],
    ) -> float:
        """Calculate numerical advantage"""
        
        team_a_alive = sum(
            1 for p in team_a.get("players", [])
            if p.get("state") == "alive"
        )
        team_b_alive = sum(
            1 for p in team_b.get("players", [])
            if p.get("state") == "alive"
        )
        
        team_a_knocked = sum(
            1 for p in team_a.get("players", [])
            if p.get("state") == "knocked"
        )
        team_b_knocked = sum(
            1 for p in team_b.get("players", [])
            if p.get("state") == "knocked"
        )
        
        # Alive players count fully, knocked count half
        team_a_effective = team_a_alive + team_a_knocked * 0.5
        team_b_effective = team_b_alive + team_b_knocked * 0.5
        
        total = team_a_effective + team_b_effective
        if total == 0:
            return 0
        
        team_a_share = team_a_effective / total
        return (team_a_share - 0.5) * 100
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ACCURACY TRACKING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def record_outcome(
        self,
        fight_id: str,
        actual_winner: str,
        actual_outcome: FightOutcome,
    ) -> None:
        """Record actual fight outcome for accuracy tracking"""
        
        prediction = self.active_predictions.pop(fight_id, None)
        if not prediction:
            return
        
        prediction.actual_outcome = actual_outcome
        prediction.was_correct = (
            prediction.predicted_winner == actual_winner
        )
        
        # Update accuracy
        self.total_predictions += 1
        if prediction.was_correct:
            self.correct_predictions += 1
        
        self.accuracy = (
            self.correct_predictions / self.total_predictions * 100
            if self.total_predictions > 0 else 0
        )
        
        # Store in history
        self.prediction_history.append(prediction)
        
        self._log.info(
            f"Prediction result: {'✓' if prediction.was_correct else '✗'}",
            predicted=prediction.predicted_winner,
            actual=actual_winner,
            accuracy=round(self.accuracy, 1),
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_active_prediction(self, fight_id: str) -> Optional[Dict[str, Any]]:
        """Get active prediction for a fight"""
        pred = self.active_predictions.get(fight_id)
        if not pred:
            return None
        
        return {
            "fight_id": pred.fight_id,
            "team_a": {
                "name": pred.team_a_name,
                "win_probability": pred.team_a_win_probability,
                "advantages": pred.team_a_advantages,
            },
            "team_b": {
                "name": pred.team_b_name,
                "win_probability": pred.team_b_win_probability,
                "advantages": pred.team_b_advantages,
            },
            "predicted_winner": pred.predicted_winner,
            "confidence": {
                "score": round(pred.confidence, 1),
                "level": pred.confidence_level.value,
            },
            "factors": pred.factors,
        }
    
    def get_accuracy(self) -> Dict[str, Any]:
        """Get prediction accuracy statistics"""
        return {
            "total_predictions": self.total_predictions,
            "correct_predictions": self.correct_predictions,
            "accuracy": round(self.accuracy, 1),
            "active_predictions": len(self.active_predictions),
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get predictor statistics"""
        return {
            **self.get_accuracy(),
            "active_fights": len(self.active_predictions),
            "history_size": len(self.prediction_history),
        }
    
    def reset(self) -> None:
        """Reset predictor"""
        self.active_predictions.clear()
        self.prediction_history.clear()
        self._log.info("Fight predictor reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("fight_detected")
        # fight_ended closes the accuracy loop: each ended fight is matched
        # against its active prediction in handle_event -> record_outcome.
        self.subscribe_to_event("fight_ended")

    async def tick(self) -> None:
        """Periodic maintenance tick."""
        # Heuristic engine is driven by direct method calls;
        # nothing periodic is required here.

    async def handle_event(self, record: EventRecord) -> None:
        """Handle an event from the event bus.

        The heuristic engine is driven synchronously via direct method
        calls; this hook keeps match context current for future
        event-driven use, and closes the prediction-accuracy loop on
        ``fight_ended`` events by recording the real outcome against the
        active prediction for that fight.
        """
        try:
            event = record.event if hasattr(record, "event") else None
            event_type = getattr(event, "event_type", "") or ""
            data = event.data if event is not None else {}
            if not isinstance(data, dict):
                data = {}

            if data.get("match_id"):
                self.config.match_id = data["match_id"]

            if event_type == "fight_ended":
                self._on_fight_ended_event(data)
        except Exception:
            pass

    def _on_fight_ended_event(self, data: Dict[str, Any]) -> None:
        """Record a real fight outcome for accuracy tracking.

        Expected event data keys (tolerant of variants):
          fight_id, winner_team_id (or winner_id/winner),
          winner_team_name, outcome (a FightOutcome value, optional).
        If no ``outcome`` is given it is derived from the winner vs the
        active prediction's teams.
        """
        fight_id = data.get("fight_id") or data.get("id")
        if not fight_id:
            return

        actual_winner = (
            data.get("winner_team_id")
            or data.get("winner_id")
            or data.get("winner")
            or ""
        )

        outcome = FightOutcome.TRADE  # fallback when nothing better is known
        raw_outcome = data.get("outcome")
        if raw_outcome:
            try:
                outcome = FightOutcome(str(raw_outcome))
            except ValueError:
                pass
        else:
            prediction = self.active_predictions.get(fight_id)
            if prediction is not None and actual_winner:
                if actual_winner == prediction.team_a_id:
                    outcome = FightOutcome.TEAM_A_WIN
                elif actual_winner == prediction.team_b_id:
                    outcome = FightOutcome.TEAM_B_WIN
                else:
                    outcome = FightOutcome.THIRD_PARTY

        self.record_outcome(
            fight_id=str(fight_id),
            actual_winner=str(actual_winner),
            actual_outcome=outcome,
        )

    async def cleanup(self) -> None:
        """Release agent resources."""
