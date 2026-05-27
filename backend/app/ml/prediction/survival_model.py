# backend/app/ml/prediction/survival_model.py
"""
AEGIS AI - Survival Analysis Model
Predicts team survival time and placement probability.
Uses Cox proportional hazards + XGBoost ensemble.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import structlog
import numpy as np

logger = structlog.get_logger("aegis.ml.survival")


class SurvivalRisk(str, Enum):
    """Survival risk levels"""
    VERY_LOW = "very_low"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"
    CRITICAL = "critical"


@dataclass
class SurvivalFeatures:
    """Features for survival prediction"""
    team_id: str = ""
    timestamp: float = field(default_factory=time.time)
    
    # Team state
    players_alive: int = 4
    players_knocked: int = 0
    total_hp: float = 400.0
    hp_percent: float = 100.0
    total_armor: float = 400.0
    
    # Combat
    total_kills: int = 0
    total_damage: int = 0
    recent_damage_taken: int = 0
    is_fighting: bool = False
    fight_count: int = 0
    
    # Position
    distance_to_zone_center: float = 0.0
    distance_to_zone_edge: float = 0.0
    in_zone: bool = True
    cover_quality: float = 50.0
    has_high_ground: bool = False
    in_compound: bool = False
    
    # Resources
    total_healing: int = 0
    total_boosts: int = 0
    smokes_available: int = 0
    grenades_available: int = 0
    
    # Threats
    nearby_enemies: int = 0
    nearest_enemy_distance: float = 9999.0
    flank_risk: float = 0.0
    
    # Context
    zone_phase: int = 1
    teams_alive: int = 16
    match_time: float = 0.0
    team_placement: int = 1  # Current predicted placement
    
    # Historical
    team_avg_survival: float = 600.0
    team_win_rate: float = 0.0
    team_top5_rate: float = 0.0
    
    def to_array(self) -> np.ndarray:
        """Convert to numpy array"""
        return np.array([
            self.players_alive, self.players_knocked,
            self.total_hp, self.hp_percent, self.total_armor,
            self.total_kills, self.total_damage, self.recent_damage_taken,
            int(self.is_fighting), self.fight_count,
            self.distance_to_zone_center, self.distance_to_zone_edge,
            int(self.in_zone), self.cover_quality,
            int(self.has_high_ground), int(self.in_compound),
            self.total_healing, self.total_boosts,
            self.smokes_available, self.grenades_available,
            self.nearby_enemies, self.nearest_enemy_distance,
            self.flank_risk, self.zone_phase, self.teams_alive,
            self.match_time, self.team_avg_survival,
            self.team_win_rate, self.team_top5_rate,
        ], dtype=np.float32)


@dataclass
class SurvivalPrediction:
    """Survival prediction result"""
    team_id: str
    timestamp: float = field(default_factory=time.time)
    
    # Survival probability
    survival_probability: float = 0.5  # 0-1
    risk_level: SurvivalRisk = SurvivalRisk.MODERATE
    
    # Time estimates
    estimated_survival_seconds: float = 600.0
    survival_curve: List[float] = field(default_factory=list)  # Probability at each minute
    
    # Placement probabilities
    win_probability: float = 0.0
    top3_probability: float = 0.0
    top5_probability: float = 0.0
    top10_probability: float = 0.0
    estimated_placement: int = 8
    
    # Risk factors
    top_risk_factors: List[Dict[str, Any]] = field(default_factory=list)
    protective_factors: List[Dict[str, Any]] = field(default_factory=list)
    
    # Confidence
    confidence: float = 0.5
    model_version: str = ""


class SurvivalModel:
    """
    Predicts team survival time and placement probability.
    
    Uses survival analysis techniques:
    - Cox proportional hazards for risk scoring
    - XGBoost for placement prediction
    - Ensemble for final probability
    
    Features (25+):
    - Team state (HP, players, resources)
    - Combat status
    - Position and zone
    - Threats nearby
    - Historical performance
    """
    
    def __init__(self, model_path: Optional[str] = None):
        self.cox_model: Optional[Any] = None
        self.placement_model: Optional[Any] = None
        self.scaler: Optional[Any] = None
        
        self._total_predictions = 0
        
        if model_path:
            self.load(model_path)
        
        self._log = logger.bind(component="survival_model")
    
    def load(self, path: str) -> bool:
        """Load trained models"""
        try:
            import joblib
            data = joblib.load(path)
            self.cox_model = data.get("cox_model")
            self.placement_model = data.get("placement_model")
            self.scaler = data.get("scaler")
            return True
        except Exception as e:
            self._log.error(f"Failed to load model: {e}")
            return False
    
    def predict(self, features: SurvivalFeatures) -> SurvivalPrediction:
        """Predict survival for a team"""
        
        start_time = time.time()
        self._total_predictions += 1
        
        X = features.to_array().reshape(1, -1)
        
        if self.scaler:
            X = self.scaler.transform(X)
        
        # Calculate survival probability
        if self.cox_model is not None:
            try:
                risk_score = float(self.cox_model.predict(X)[0])
                survival_prob = 1.0 / (1.0 + np.exp(risk_score))
            except Exception:
                survival_prob = self._rule_based_survival(features)
        else:
            survival_prob = self._rule_based_survival(features)
        
        # Estimate survival time
        base_time = 1800  # 30 minutes max
        estimated_time = base_time * survival_prob
        
        # Placement prediction
        if self.placement_model is not None:
            try:
                placement = int(self.placement_model.predict(X)[0])
            except Exception:
                placement = self._estimate_placement(features)
        else:
            placement = self._estimate_placement(features)
        
        # Calculate placement probabilities
        win_prob = max(0.01, survival_prob ** 3)
        top3_prob = min(0.95, survival_prob ** 1.5)
        top5_prob = min(0.95, survival_prob)
        top10_prob = min(0.98, survival_prob ** 0.5)
        
        # Determine risk level
        risk_level = self._determine_risk(survival_prob)
        
        # Get factors
        risk_factors = self._get_risk_factors(features)
        protective_factors = self._get_protective_factors(features)
        
        return SurvivalPrediction(
            team_id=features.team_id,
            survival_probability=round(survival_prob, 4),
            risk_level=risk_level,
            estimated_survival_seconds=round(estimated_time, 0),
            win_probability=round(win_prob, 4),
            top3_probability=round(top3_prob, 4),
            top5_probability=round(top5_prob, 4),
            top10_probability=round(top10_prob, 4),
            estimated_placement=placement,
            top_risk_factors=risk_factors,
            protective_factors=protective_factors,
            confidence=round(0.6 + 0.1 * features.teams_alive / 16, 4),
        )
    
    def _rule_based_survival(self, features: SurvivalFeatures) -> float:
        """Rule-based survival probability"""
        
        prob = 0.5  # Base
        
        # Players alive
        prob += (features.players_alive - 2) * 0.1
        
        # HP
        prob += (features.hp_percent - 50) * 0.003
        
        # Zone position
        if not features.in_zone:
            prob -= 0.2
            prob -= min(0.3, features.distance_to_zone_edge / 5000 * 0.3)
        
        # Threats
        prob -= features.nearby_enemies * 0.05
        if features.is_fighting:
            prob -= 0.15
        
        # Resources
        prob += min(0.1, features.total_healing / 200 * 0.1)
        prob += min(0.05, features.smokes_available / 5 * 0.05)
        
        # Historical
        prob += features.team_top5_rate * 0.1
        
        return max(0.01, min(0.95, prob))
    
    def _estimate_placement(self, features: SurvivalFeatures) -> int:
        """Estimate final placement"""
        
        base = features.teams_alive // 2
        
        # Adjust based on strength
        if features.hp_percent < 30:
            base += 2
        if features.players_alive < 3:
            base += 2
        if features.is_fighting:
            base += 1
        
        return max(1, min(16, base))
    
    def _determine_risk(self, prob: float) -> SurvivalRisk:
        """Determine risk level"""
        if prob >= 0.8:
            return SurvivalRisk.VERY_LOW
        elif prob >= 0.6:
            return SurvivalRisk.LOW
        elif prob >= 0.4:
            return SurvivalRisk.MODERATE
        elif prob >= 0.2:
            return SurvivalRisk.HIGH
        elif prob >= 0.1:
            return SurvivalRisk.VERY_HIGH
        return SurvivalRisk.CRITICAL
    
    def _get_risk_factors(self, features: SurvivalFeatures) -> List[Dict[str, Any]]:
        """Get top risk factors"""
        factors = []
        
        if not features.in_zone:
            factors.append({"factor": "Outside safe zone", "severity": "high"})
        
        if features.is_fighting:
            factors.append({"factor": "Currently in combat", "severity": "high"})
        
        if features.nearby_enemies >= 3:
            factors.append({"factor": f"{features.nearby_enemies} nearby enemies", "severity": "high"})
        
        if features.players_alive <= 2:
            factors.append({"factor": "Low player count", "severity": "medium"})
        
        if features.hp_percent < 30:
            factors.append({"factor": "Critical HP level", "severity": "high"})
        
        return factors
    
    def _get_protective_factors(self, features: SurvivalFeatures) -> List[Dict[str, Any]]:
        """Get protective factors"""
        factors = []
        
        if features.in_compound:
            factors.append({"factor": "Holding compound", "benefit": "high"})
        
        if features.has_high_ground:
            factors.append({"factor": "High ground advantage", "benefit": "medium"})
        
        if features.smokes_available >= 4:
            factors.append({"factor": "Good smoke coverage", "benefit": "medium"})
        
        if features.total_healing >= 200:
            factors.append({"factor": "Strong healing capacity", "benefit": "medium"})
        
        if features.players_alive == 4:
            factors.append({"factor": "Full squad alive", "benefit": "high"})
        
        return factors
    
    def get_stats(self) -> Dict[str, Any]:
        """Get model statistics"""
        return {
            "total_predictions": self._total_predictions,
            "has_cox_model": self.cox_model is not None,
            "has_placement_model": self.placement_model is not None,
        }