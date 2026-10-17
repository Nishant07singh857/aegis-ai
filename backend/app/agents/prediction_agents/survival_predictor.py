# backend/app/agents/prediction_agents/survival_predictor.py
"""
AEGIS AI - Survival Predictor
Predicts team survival probability and time-to-elimination.
Analyzes zone position, team strength, and match context.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.survival_predictor")

class SurvivalRisk(str, Enum):
    """Survival risk levels"""
    SAFE = "safe"
    LOW_RISK = "low_risk"
    MODERATE = "moderate"
    HIGH_RISK = "high_risk"
    CRITICAL = "critical"
    IMMINENT = "imminent"

@dataclass
class SurvivalPrediction:
    """Team survival prediction"""
    team_id: str
    team_name: str
    timestamp: float = field(default_factory=time.time)
    
    # Survival probability
    survival_probability: float = 0.0  # 0-100
    risk_level: SurvivalRisk = SurvivalRisk.MODERATE
    
    # Time estimates
    estimated_survival_seconds: float = 0.0
    estimated_placement: int = 16
    
    # Top placement probabilities
    top1_probability: float = 0.0
    top3_probability: float = 0.0
    top5_probability: float = 0.0
    top10_probability: float = 0.0
    
    # Risk factors
    risk_factors: List[str] = field(default_factory=list)
    positive_factors: List[str] = field(default_factory=list)
    
    # Context
    zone_phase: int = 0
    teams_alive: int = 16
    alive_players: int = 4
    in_zone: bool = True
    is_fighting: bool = False


class SurvivalPredictor(BaseAgent):
    """
    Predicts team survival probability.
    
    Factors:
    1. Zone position and distance
    2. Team strength (HP, players alive)
    3. Nearby threats
    4. Historical survival performance
    5. Current engagements
    6. Utility/resources available
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        self.active_predictions: Dict[str, SurvivalPrediction] = {}
        self.zone_history: deque = deque(maxlen=10)
        self._log = logger.bind(component="survival_predictor")
    
    def predict_survival(
        self,
        team_id: str,
        team_name: str,
        team_state: Dict[str, Any],
        match_context: Dict[str, Any],
    ) -> SurvivalPrediction:
        """Predict team survival probability"""
        
        pred = SurvivalPrediction(
            team_id=team_id,
            team_name=team_name,
            zone_phase=match_context.get("zone_phase", 0),
            teams_alive=match_context.get("teams_alive", 16),
            alive_players=team_state.get("alive_count", 4),
            in_zone=team_state.get("in_zone", True),
            is_fighting=team_state.get("is_fighting", False),
        )
        
        # Base survival from teams alive
        base_survival = 1.0 / max(1, pred.teams_alive)
        
        # Zone factor
        zone_factor = self._calculate_zone_factor(team_state, match_context)
        
        # HP factor
        hp_factor = self._calculate_hp_factor(team_state)
        
        # Threat factor
        threat_factor = self._calculate_threat_factor(team_state, match_context)
        
        # Resource factor
        resource_factor = self._calculate_resource_factor(team_state)
        
        # Calculate survival probability
        survival = base_survival * zone_factor * hp_factor * threat_factor * resource_factor
        pred.survival_probability = round(min(95, max(1, survival * 100)), 1)
        
        # Estimate placement
        pred.estimated_placement = max(1, int(pred.teams_alive * (1 - survival) + 1))
        
        # Calculate top placement probabilities
        pred.top1_probability = round(survival ** 3 * 100, 1)
        pred.top3_probability = round(survival ** 2 * 100, 1)
        pred.top5_probability = round(survival * 100, 1)
        pred.top10_probability = round(min(95, survival * 150), 1)
        
        # Estimate survival time
        zone_duration = 180  # Average zone duration in seconds
        zones_remaining = 8 - pred.zone_phase
        pred.estimated_survival_seconds = survival * zones_remaining * zone_duration
        
        # Determine risk level
        pred.risk_level = self._determine_risk(pred)
        
        # Identify factors
        self._identify_factors(pred, team_state, match_context)
        
        self.active_predictions[team_id] = pred
        return pred
    
    def _calculate_zone_factor(
        self,
        team_state: Dict[str, Any],
        context: Dict[str, Any],
    ) -> float:
        """Calculate zone position factor"""
        if team_state.get("in_zone", True):
            distance_to_edge = team_state.get("distance_to_zone_edge", 0)
            zone_radius = context.get("zone_radius", 1000)
            
            if distance_to_edge < zone_radius * 0.3:
                return 1.3  # Center of zone - safe
            elif distance_to_edge < zone_radius * 0.7:
                return 1.1  # Mid zone
            else:
                return 0.9  # Edge of zone
        else:
            distance_outside = team_state.get("distance_to_zone", 100)
            if distance_outside < 100:
                return 0.7
            elif distance_outside < 300:
                return 0.4
            else:
                return 0.2  # Far from zone - dangerous
    
    def _calculate_hp_factor(self, team_state: Dict[str, Any]) -> float:
        """Calculate HP health factor"""
        hp_percent = team_state.get("hp_percent", 100) / 100
        
        if hp_percent > 0.8:
            return 1.2
        elif hp_percent > 0.5:
            return 1.0
        elif hp_percent > 0.25:
            return 0.7
        else:
            return 0.4
    
    def _calculate_threat_factor(
        self,
        team_state: Dict[str, Any],
        context: Dict[str, Any],
    ) -> float:
        """Calculate threat level factor"""
        factor = 1.0
        
        nearby_enemies = team_state.get("nearby_enemies", 0)
        nearest_enemy = team_state.get("nearest_enemy_distance", 9999)
        
        if nearby_enemies >= 2 and nearest_enemy < 50:
            factor -= 0.3
        elif nearby_enemies >= 1:
            factor -= 0.15
        
        if team_state.get("is_fighting", False):
            factor -= 0.2
        
        if team_state.get("flank_risk", 0) > 50:
            factor -= 0.15
        
        return max(0.2, factor)
    
    def _calculate_resource_factor(self, team_state: Dict[str, Any]) -> float:
        """Calculate resource availability factor"""
        factor = 1.0
        
        utility = team_state.get("utility", {})
        
        # Healing capacity
        heals = utility.get("first_aids", 0) + utility.get("bandages", 0) * 0.5
        if heals >= 4:
            factor += 0.15
        elif heals >= 2:
            factor += 0.05
        elif heals == 0:
            factor -= 0.2
        
        # Boost capacity
        boosts = utility.get("boosts", 0)
        if boosts >= 4:
            factor += 0.1
        elif boosts == 0:
            factor -= 0.1
        
        return max(0.3, factor)
    
    def _determine_risk(self, pred: SurvivalPrediction) -> SurvivalRisk:
        """Determine survival risk level"""
        prob = pred.survival_probability
        
        if prob >= 80:
            return SurvivalRisk.SAFE
        elif prob >= 60:
            return SurvivalRisk.LOW_RISK
        elif prob >= 40:
            return SurvivalRisk.MODERATE
        elif prob >= 20:
            return SurvivalRisk.HIGH_RISK
        elif prob >= 10:
            return SurvivalRisk.CRITICAL
        else:
            return SurvivalRisk.IMMINENT
    
    def _identify_factors(
        self,
        pred: SurvivalPrediction,
        team_state: Dict[str, Any],
        context: Dict[str, Any],
    ) -> None:
        """Identify risk and positive factors"""
        
        # Risk factors
        if not team_state.get("in_zone", True):
            pred.risk_factors.append("Outside safe zone")
        if team_state.get("is_fighting", False):
            pred.risk_factors.append("Currently engaged in combat")
        if team_state.get("alive_count", 4) <= 2:
            pred.risk_factors.append("Low player count")
        if team_state.get("nearby_enemies", 0) >= 2:
            pred.risk_factors.append("Multiple nearby threats")
        if team_state.get("hp_percent", 100) < 40:
            pred.risk_factors.append("Low team health")
        
        # Positive factors
        if team_state.get("in_compound", False):
            pred.positive_factors.append("Holding compound")
        if team_state.get("has_high_ground", False):
            pred.positive_factors.append("High ground advantage")
        if team_state.get("alive_count", 4) == 4:
            pred.positive_factors.append("Full squad alive")
        if team_state.get("hp_percent", 100) > 80:
            pred.positive_factors.append("Strong team health")
        if team_state.get("utility", {}).get("smokes", 0) >= 4:
            pred.positive_factors.append("Good smoke coverage")
    
    def get_survival_prediction(self, team_id: str) -> Optional[Dict[str, Any]]:
        """Get survival prediction for a team"""
        pred = self.active_predictions.get(team_id)
        if not pred:
            return None
        
        return {
            "team_name": pred.team_name,
            "survival_probability": pred.survival_probability,
            "risk_level": pred.risk_level.value,
            "estimated_placement": pred.estimated_placement,
            "estimated_survival_minutes": round(pred.estimated_survival_seconds / 60, 1),
            "top_placements": {
                "win": round(pred.top1_probability, 1),
                "top3": round(pred.top3_probability, 1),
                "top5": round(pred.top5_probability, 1),
            },
            "risk_factors": pred.risk_factors,
            "positive_factors": pred.positive_factors,
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get predictor statistics"""
        return {
            "active_predictions": len(self.active_predictions),
        }
    
    def reset(self) -> None:
        """Reset predictor"""
        self.active_predictions.clear()
        self.zone_history.clear()
        self._log.info("Survival predictor reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("zone_change")
        self.subscribe_to_event("player_knocked")

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
