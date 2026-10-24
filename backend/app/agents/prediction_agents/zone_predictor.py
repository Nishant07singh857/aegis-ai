# backend/app/agents/prediction_agents/zone_predictor.py
"""
AEGIS AI - Zone Predictor

DORMANT — this agent is NOT live in production and has no trained
zone-prediction model behind it.

The previous implementation of ``predict_next_zone`` fabricated its
output: it offset the current centre by ``random.uniform`` angle /
distance and invented alternative-prediction confidences. Those were
random numbers presented as predictions (FULL_PROJECT_INSPECTION H13).

Honest behaviour now: ``predict_next_zone`` (and
``get_zone_probability``, which depended on a fabricated prediction)
raise :class:`ZonePredictionUnavailable` with an explicit reason.
No random prediction can be produced from this module. A real zone
predictor would need a model trained on measured zone data; none
exists in this repo today.
"""

import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from collections import deque
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.zone_predictor")


class ZonePredictionUnavailable(RuntimeError):
    """Raised when a zone prediction is requested from this dormant agent.

    There is no trained zone model and no measured prediction method
    behind this class, so any numeric prediction would be fabricated.
    """


ZONE_PREDICTION_UNAVAILABLE_REASON = (
    "ZonePredictor is dormant: no trained zone-prediction model exists, "
    "and the previous random-offset implementation was fabrication "
    "(inspection H13). Zone prediction is unavailable."
)

@dataclass
class ZonePrediction:
    """Zone prediction result"""
    prediction_id: str
    
    # Current zone
    current_center_x: float = 0.0
    current_center_y: float = 0.0
    current_radius: float = 0.0
    current_phase: int = 0
    
    # Predicted next zone
    predicted_center_x: float = 0.0
    predicted_center_y: float = 0.0   
    predicted_radius: float = 0.0
    confidence: float = 0.0
    
    # Alternative predictions
    alternative_centers: List[Tuple[float, float, float]] = field(default_factory=list)
    
    # Probability distribution (sampled points)
    probability_map: List[Dict[str, float]] = field(default_factory=list)
    
    timestamp: float = field(default_factory=time.time)


class ZonePredictor(BaseAgent):
    """
    DORMANT zone-predictor agent — does NOT predict zones.

    Historically this class claimed to predict the next safe zone from
    the current zone, historical patterns and zone-shift mechanics, but
    the implementation was a random offset with invented confidences.
    That fabrication was removed (inspection H13): prediction entry
    points now raise :class:`ZonePredictionUnavailable`. The class is
    kept only so dormant imports / the (also dormant) orchestrator
    registry shape do not silently change; nothing in production
    instantiates it.
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        self.predictions: deque = deque(maxlen=20)
        
        # Map knowledge (common final zones per map)
        self.common_final_zones = {
            "erangel": [
                (4000, 5000), (4500, 4500), (5000, 4000),
                (3500, 5500), (5500, 3500), (4000, 4000),
            ],
            "miramar": [
                (4000, 4000), (4500, 5000), (3500, 3000),
            ],
            "sanhok": [
                (2000, 2000), (2500, 1500), (1500, 2500),
            ],
        }
        
        # Zone shrink ratios per phase
        self.zone_shrink_ratios = {
            0: 1.0,    # Initial
            1: 0.65,   # First shrink
            2: 0.50,   # Second
            3: 0.40,   # Third
            4: 0.30,   # Fourth
            5: 0.22,   # Fifth
            6: 0.15,   # Sixth
            7: 0.08,   # Seventh
            8: 0.04,   # Final
        }
        
        self._log = logger.bind(component="zone_predictor")
    
    def predict_next_zone(
        self,
        current_center_x: float,
        current_center_y: float,
        current_radius: float,
        current_phase: int,
        map_name: str = "erangel",
    ) -> ZonePrediction:
        """Zone prediction is unavailable — always raises.

        The previous body fabricated a prediction (random centre offset
        + invented confidences, inspection H13). It was removed rather
        than re-tuned: there is no trained model or measured method
        behind this dormant agent, so the only honest result is an
        explicit unavailable error.

        Raises:
            ZonePredictionUnavailable: always, with the reason above.
        """
        self._log.warning(
            "zone prediction requested from dormant ZonePredictor; "
            "returning unavailable",
            reason=ZONE_PREDICTION_UNAVAILABLE_REASON,
        )
        raise ZonePredictionUnavailable(ZONE_PREDICTION_UNAVAILABLE_REASON)
    
    def get_zone_probability(
        self,
        x: float,
        y: float,
        prediction: ZonePrediction,
    ) -> float:
        """Zone probability is unavailable — always raises.

        This derived a "probability" from a fabricated prediction and
        its invented confidence, so it has the same defect as
        ``predict_next_zone`` (inspection H13) and was retired with it.

        Raises:
            ZonePredictionUnavailable: always.
        """
        raise ZonePredictionUnavailable(ZONE_PREDICTION_UNAVAILABLE_REASON)
    
    def _get_map_center(self, map_name: str) -> Tuple[float, float]:
        """Get map center coordinates"""
        map_sizes = {
            "erangel": (8000, 8000),
            "miramar": (8000, 8000),
            "sanhok": (4000, 4000),
            "vikendi": (6000, 6000),
            "livik": (2000, 2000),
            "karakin": (2000, 2000),
        }
        size = map_sizes.get(map_name, (8000, 8000))
        return (size[0] / 2, size[1] / 2)
    
    def _get_map_size(self, map_name: str) -> float:
        """Get map size"""
        map_sizes = {
            "erangel": 8000, "miramar": 8000, "sanhok": 4000,
            "vikendi": 6000, "livik": 2000, "karakin": 2000,
        }
        return map_sizes.get(map_name, 8000)
    
    def get_prediction(self) -> Optional[Dict[str, Any]]:
        """Get latest zone prediction"""
        if not self.predictions:
            return None
        
        pred = self.predictions[-1]
        return {
            "current_zone": {
                "center": (round(pred.current_center_x, 1), round(pred.current_center_y, 1)),
                "radius": round(pred.current_radius, 1),
                "phase": pred.current_phase,
            },
            "predicted_zone": {
                "center": (round(pred.predicted_center_x, 1), round(pred.predicted_center_y, 1)),
                "radius": round(pred.predicted_radius, 1),
            },
            "confidence": round(pred.confidence * 100, 1),
            "alternatives": [
                {
                    "center": (round(x, 1), round(y, 1)),
                    "confidence": round(c, 2),
                }
                for x, y, c in pred.alternative_centers[:3]
            ],
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get predictor statistics"""
        return {
            "predictions_made": len(self.predictions),
            "latest": self.get_prediction(),
        }
    
    def reset(self) -> None:
        """Reset predictor"""
        self.predictions.clear()
        self._log.info("Zone predictor reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("zone_change")

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
