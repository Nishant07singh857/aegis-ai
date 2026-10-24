# backend/app/ml/prediction/fight_outcome_model.py
"""
AEGIS AI - Fight Outcome Prediction Model

Honest status (inspection H18): there is NO gradient-boosting +
neural-network ensemble here, and no training code exists in this
module — that claim was removed. What actually exists:

- an optional externally-trained model, loaded from disk via joblib
  (``load()``), used through ``predict_proba`` when one is supplied;
- otherwise, an explicit rule-based heuristic fallback
  (``_rule_based_predict``), whose results are labelled as such via
  ``model_name``.

Dormant: zero production callers. If a loaded model raises during
prediction, ``predict()`` now raises
:class:`FightOutcomeUnavailable` instead of silently returning a
fabricated 0.5/0.5 "normal" result.
"""

import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import structlog
import numpy as np

logger = structlog.get_logger("aegis.ml.fight_outcome")


class FightOutcomeUnavailable(RuntimeError):
    """Raised when a loaded fight-outcome model fails to predict.

    Replaces the old silent fallback that returned 0.5/0.5 with a
    TEAM_A_WIN outcome as if it were a real prediction (inspection
    H18). No probability is fabricated when the model errors.
    """


class FightOutcome(str, Enum):
    """Possible fight outcomes"""
    TEAM_A_WIN = "team_a_win"
    TEAM_B_WIN = "team_b_win"
    TRADE = "trade"
    DISENGAGE = "disengage"
    THIRD_PARTY = "third_party"


@dataclass
class FightFeatures:
    """Features extracted for fight prediction"""
    fight_id: str = ""
    timestamp: float = field(default_factory=time.time)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HP & ARMOR FEATURES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    team_a_total_hp: float = 400.0
    team_b_total_hp: float = 400.0
    team_a_hp_percent: float = 100.0
    team_b_hp_percent: float = 100.0
    team_a_armor_total: float = 400.0
    team_b_armor_total: float = 400.0
    team_a_players_alive: int = 4
    team_b_players_alive: int = 4
    team_a_knocked: int = 0
    team_b_knocked: int = 0
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DAMAGE FEATURES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    team_a_damage_dealt: float = 0.0
    team_b_damage_dealt: float = 0.0
    team_a_damage_rate: float = 0.0  # Damage per second
    team_b_damage_rate: float = 0.0
    team_a_recent_damage: float = 0.0  # Last 3 seconds
    team_b_recent_damage: float = 0.0
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # POSITION FEATURES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    distance_between_teams: float = 100.0
    team_a_cover_quality: float = 50.0
    team_b_cover_quality: float = 50.0
    team_a_has_high_ground: bool = False
    team_b_has_high_ground: bool = False
    team_a_in_compound: bool = False
    team_b_in_compound: bool = False
    team_a_formation_spread: float = 20.0
    team_b_formation_spread: float = 20.0
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # WEAPON FEATURES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    team_a_weapon_score: float = 50.0
    team_b_weapon_score: float = 50.0
    team_a_has_sniper: bool = False
    team_b_has_sniper: bool = False
    team_a_has_shotgun: bool = False
    team_b_has_shotgun: bool = False
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # UTILITY FEATURES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    team_a_grenades: int = 0
    team_b_grenades: int = 0
    team_a_smokes: int = 0
    team_b_smokes: int = 0
    team_a_molotovs: int = 0
    team_b_molotovs: int = 0
    team_a_utility_used: int = 0
    team_b_utility_used: int = 0
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PLAYER SKILL FEATURES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    team_a_avg_skill: float = 50.0
    team_b_avg_skill: float = 50.0
    team_a_clutch_rate: float = 0.1
    team_b_clutch_rate: float = 0.1
    team_a_headshot_pct: float = 15.0
    team_b_headshot_pct: float = 15.0
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CONTEXT FEATURES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    zone_phase: int = 1
    teams_alive: int = 16
    is_final_circle: bool = False
    fight_duration: float = 0.0
    is_rivalry: bool = False
    team_a_tournament_rank: int = 8
    team_b_tournament_rank: int = 8
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MOMENTUM FEATURES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    team_a_recent_kills: int = 0
    team_b_recent_kills: int = 0
    team_a_is_aggressor: bool = False
    team_b_is_aggressor: bool = False
    
    def to_array(self) -> np.ndarray:
        """Convert features to numpy array for model input"""
        return np.array([
            self.team_a_total_hp,
            self.team_b_total_hp,
            self.team_a_hp_percent,
            self.team_b_hp_percent,
            self.team_a_players_alive,
            self.team_b_players_alive,
            self.team_a_knocked,
            self.team_b_knocked,
            self.team_a_damage_dealt,
            self.team_b_damage_dealt,
            self.team_a_damage_rate,
            self.team_b_damage_rate,
            self.distance_between_teams,
            self.team_a_cover_quality,
            self.team_b_cover_quality,
            int(self.team_a_has_high_ground),
            int(self.team_b_has_high_ground),
            self.team_a_weapon_score,
            self.team_b_weapon_score,
            self.team_a_grenades,
            self.team_b_grenades,
            self.team_a_smokes,
            self.team_b_smokes,
            self.team_a_avg_skill,
            self.team_b_avg_skill,
            self.zone_phase,
            self.teams_alive,
            int(self.is_final_circle),
            self.fight_duration,
            int(self.is_rivalry),
            self.team_a_recent_kills,
            self.team_b_recent_kills,
        ], dtype=np.float32)
    
    def get_feature_names(self) -> List[str]:
        """Get feature names for interpretability"""
        return [
            "team_a_total_hp", "team_b_total_hp",
            "team_a_hp_percent", "team_b_hp_percent",
            "team_a_players_alive", "team_b_players_alive",
            "team_a_knocked", "team_b_knocked",
            "team_a_damage_dealt", "team_b_damage_dealt",
            "team_a_damage_rate", "team_b_damage_rate",
            "distance_between_teams",
            "team_a_cover_quality", "team_b_cover_quality",
            "team_a_has_high_ground", "team_b_has_high_ground",
            "team_a_weapon_score", "team_b_weapon_score",
            "team_a_grenades", "team_b_grenades",
            "team_a_smokes", "team_b_smokes",
            "team_a_avg_skill", "team_b_avg_skill",
            "zone_phase", "teams_alive",
            "is_final_circle", "fight_duration",
            "is_rivalry",
            "team_a_recent_kills", "team_b_recent_kills",
        ]


@dataclass
class OutcomePrediction:
    """Fight outcome prediction result"""
    fight_id: str
    timestamp: float = field(default_factory=time.time)
    
    # Probabilities (sum to 1.0)
    team_a_win_prob: float = 0.5
    team_b_win_prob: float = 0.5
    trade_prob: float = 0.0
    disengage_prob: float = 0.0
    
    # Predicted outcome
    predicted_outcome: FightOutcome = FightOutcome.TEAM_A_WIN
    confidence: float = 0.5
    
    # Feature importance (top factors)
    top_factors: List[Dict[str, Any]] = field(default_factory=list)
    
    # Model metadata
    model_name: str = ""
    model_version: str = ""
    inference_time_ms: float = 0.0


class FightOutcomeModel:
    """
    Fight-outcome predictor — NOT a trained ensemble.

    No training code exists in this module and no trained model ships
    with the repo. Prediction uses, in order:

    1. an externally-trained model loaded via ``load()`` (joblib,
       ``predict_proba``), if one was supplied; or
    2. an explicit rule-based heuristic over the feature values,
       labelled ``fight_outcome_rule_based_heuristic`` in results.

    Features (30+ features):
    - HP and armor status
    - Damage dealt and rates
    - Position and cover
    - Weapons and utility
    - Player skill ratings
    - Match context

    Output: Win probability for each team
    """
    
    def __init__(self, model_path: Optional[str] = None):
        self.model: Optional[Any] = None
        self.scaler: Optional[Any] = None
        self.feature_names: List[str] = []
        self.model_version: str = "1.0.0"
        
        # Statistics
        self._total_predictions = 0
        self._correct_predictions = 0
        self._outcomes_recorded = 0
        # fight_id -> predicted outcome, for honest accuracy counting
        # in record_outcome()
        self._pending_predictions: Dict[str, FightOutcome] = {}
        
        if model_path:
            self.load(model_path)
        
        self._log = logger.bind(component="fight_outcome_model")
    
    def load(self, path: str) -> bool:
        """Load trained model"""
        try:
            import joblib
            
            data = joblib.load(path)
            self.model = data.get("model")
            self.scaler = data.get("scaler")
            self.feature_names = data.get("feature_names", [])
            self.model_version = data.get("version", "1.0.0")
            
            self._log.info(f"Model loaded: {path} (v{self.model_version})")
            return True
            
        except Exception as e:
            self._log.error(f"Failed to load model: {e}")
            return False
    
    def save(self, path: str) -> bool:
        """Save model to disk"""
        try:
            import joblib
            
            data = {
                "model": self.model,
                "scaler": self.scaler,
                "feature_names": self.feature_names,
                "version": self.model_version,
            }
            
            joblib.dump(data, path)
            self._log.info(f"Model saved: {path}")
            return True
            
        except Exception as e:
            self._log.error(f"Failed to save model: {e}")
            return False
    
    def predict(self, features: FightFeatures) -> OutcomePrediction:
        """
        Predict fight outcome from features.
        
        Args:
            features: FightFeatures object
            
        Returns:
            OutcomePrediction with probabilities
        """
        
        start_time = time.time()
        self._total_predictions += 1
        
        # Convert features to array
        X = features.to_array().reshape(1, -1)
        
        # Scale if scaler available
        if self.scaler:
            X = self.scaler.transform(X)
        
        # Predict
        if self.model is not None:
            model_name = "fight_outcome_model_loaded_external"
            try:
                # Get probabilities
                probs = self.model.predict_proba(X)[0]

                # Map to outcomes (simplified)
                team_a_win = probs[0] if len(probs) > 0 else 0.5
                team_b_win = probs[1] if len(probs) > 1 else 1 - team_a_win
                trade = probs[2] if len(probs) > 2 else 0.0
                disengage = 1 - team_a_win - team_b_win - trade

                # Determine outcome
                max_prob = max(team_a_win, team_b_win, trade, disengage)

                if max_prob == team_a_win:
                    outcome = FightOutcome.TEAM_A_WIN
                    confidence = team_a_win
                elif max_prob == team_b_win:
                    outcome = FightOutcome.TEAM_B_WIN
                    confidence = team_b_win
                elif max_prob == trade:
                    outcome = FightOutcome.TRADE
                    confidence = trade
                else:
                    outcome = FightOutcome.DISENGAGE
                    confidence = disengage

            except Exception as e:
                # Honest failure (inspection H18): the old code silently
                # returned 0.5/0.5 + TEAM_A_WIN as a normal result.
                # A failed model prediction is unavailable, not 50/50.
                self._total_predictions -= 1
                self._log.error(f"Prediction error: {e}")
                raise FightOutcomeUnavailable(
                    f"Loaded fight-outcome model failed to predict "
                    f"fight {features.fight_id!r}: {e}"
                ) from e
        else:
            # Explicit rule-based heuristic fallback (labelled as such)
            model_name = "fight_outcome_rule_based_heuristic"
            team_a_win, team_b_win, trade, disengage = self._rule_based_predict(features)
            max_prob = max(team_a_win, team_b_win)
            outcome = FightOutcome.TEAM_A_WIN if team_a_win >= team_b_win else FightOutcome.TEAM_B_WIN
            confidence = max_prob

        # Get top factors
        top_factors = self._get_top_factors(features, team_a_win, team_b_win)

        inference_time = (time.time() - start_time) * 1000

        if features.fight_id:
            self._pending_predictions[features.fight_id] = outcome

        return OutcomePrediction(
            fight_id=features.fight_id,
            team_a_win_prob=round(team_a_win, 4),
            team_b_win_prob=round(team_b_win, 4),
            trade_prob=round(trade, 4),
            disengage_prob=round(max(0, disengage), 4),
            predicted_outcome=outcome,
            confidence=round(confidence, 4),
            top_factors=top_factors,
            model_name=model_name,
            model_version=self.model_version,
            inference_time_ms=round(inference_time, 2),
        )
    
    def predict_batch(self, features_list: List[FightFeatures]) -> List[OutcomePrediction]:
        """Predict outcomes for multiple fights"""
        return [self.predict(features) for features in features_list]
    
    def _rule_based_predict(self, features: FightFeatures) -> Tuple[float, float, float, float]:
        """Rule-based prediction as fallback"""
        
        team_a_score = 50.0
        team_b_score = 50.0
        
        # HP advantage
        hp_diff = features.team_a_hp_percent - features.team_b_hp_percent
        team_a_score += hp_diff * 0.2
        team_b_score -= hp_diff * 0.2
        
        # Numbers advantage
        num_diff = features.team_a_players_alive - features.team_b_players_alive
        team_a_score += num_diff * 8
        team_b_score -= num_diff * 8
        
        # Damage advantage
        dmg_diff = features.team_a_damage_rate - features.team_b_damage_rate
        team_a_score += dmg_diff * 0.5
        team_b_score -= dmg_diff * 0.5
        
        # Cover advantage
        cover_diff = features.team_a_cover_quality - features.team_b_cover_quality
        team_a_score += cover_diff * 0.1
        team_b_score -= cover_diff * 0.1
        
        # High ground
        if features.team_a_has_high_ground and not features.team_b_has_high_ground:
            team_a_score += 10
        if features.team_b_has_high_ground and not features.team_a_has_high_ground:
            team_b_score += 10
        
        # Skill advantage
        skill_diff = features.team_a_avg_skill - features.team_b_avg_skill
        team_a_score += skill_diff * 0.2
        team_b_score -= skill_diff * 0.2
        
        # Clamp scores
        team_a_score = max(5, min(95, team_a_score))
        team_b_score = max(5, min(95, team_b_score))
        
        # Normalize to probabilities
        total = team_a_score + team_b_score
        
        return (
            team_a_score / total,
            team_b_score / total,
            0.0,  # trade
            0.0,  # disengage
        )
    
    def _get_top_factors(
        self,
        features: FightFeatures,
        team_a_prob: float,
        team_b_prob: float,
    ) -> List[Dict[str, Any]]:
        """Get top contributing factors"""
        
        factors = []
        
        # HP difference
        hp_diff = features.team_a_hp_percent - features.team_b_hp_percent
        if abs(hp_diff) > 10:
            advantage = "Team A" if hp_diff > 0 else "Team B"
            factors.append({
                "factor": "HP Advantage",
                "advantage": advantage,
                "impact": round(abs(hp_diff) * 0.3, 1),
            })
        
        # Numbers
        num_diff = features.team_a_players_alive - features.team_b_players_alive
        if num_diff != 0:
            advantage = "Team A" if num_diff > 0 else "Team B"
            factors.append({
                "factor": "Player Count",
                "advantage": advantage,
                "impact": round(abs(num_diff) * 10, 1),
            })
        
        # Position
        if features.team_a_has_high_ground and not features.team_b_has_high_ground:
            factors.append({
                "factor": "High Ground",
                "advantage": "Team A",
                "impact": 10.0,
            })
        
        # Skill
        skill_diff = features.team_a_avg_skill - features.team_b_avg_skill
        if abs(skill_diff) > 10:
            advantage = "Team A" if skill_diff > 0 else "Team B"
            factors.append({
                "factor": "Skill Rating",
                "advantage": advantage,
                "impact": round(abs(skill_diff) * 0.2, 1),
            })
        
        return sorted(factors, key=lambda f: f["impact"], reverse=True)[:5]
    
    def record_outcome(self, fight_id: str, actual_outcome: FightOutcome) -> bool:
        """Record an actual outcome for accuracy tracking.

        Real (small) counting implementation — replaces the previous
        ``pass`` stub (inspection H18), under which accuracy could
        never move. Compares the actual outcome against the prediction
        previously made for ``fight_id`` by ``predict()``.

        Returns:
            True if a pending prediction for ``fight_id`` was found
            and counted; False (with a warning, and nothing counted)
            when there is no prediction to score against — accuracy is
            never credited for an unknown fight.
        """
        predicted = self._pending_predictions.pop(fight_id, None)
        if predicted is None:
            self._log.warning(
                "record_outcome for unknown fight_id; not counted",
                fight_id=fight_id,
            )
            return False
        self._outcomes_recorded += 1
        if predicted == actual_outcome:
            self._correct_predictions += 1
        return True

    def get_accuracy(self) -> float:
        """Accuracy over outcomes actually recorded via record_outcome.

        0.0 when no outcome has been recorded yet (no denominator —
        accuracy is not derived from raw prediction count).
        """
        if self._outcomes_recorded == 0:
            return 0.0
        return self._correct_predictions / self._outcomes_recorded

    def get_stats(self) -> Dict[str, Any]:
        """Get model statistics"""
        return {
            "model_version": self.model_version,
            "total_predictions": self._total_predictions,
            "outcomes_recorded": self._outcomes_recorded,
            "correct_predictions": self._correct_predictions,
            "accuracy": round(self.get_accuracy(), 4),
            "has_model": self.model is not None,
            "feature_count": len(self.feature_names),
        }