# backend/app/agents/prediction_agents/zone_predictor.py
"""
AEGIS AI - Zone Predictor
Predicts next safe zone location based on current zone,
historical patterns, and game mechanics.
"""

import time
import math
import random
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from collections import deque
import structlog

logger = structlog.get_logger("aegis.agents.zone_predictor")

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


class ZonePredictor:
    """
    Predicts next safe zone location.
    
    Uses:
    - Current zone position and size
    - Historical zone patterns per map
    - Zone shift mechanics (zone always inside previous)
    - Distance-based probability distribution
    - Common zone ending areas
    """
    
    def __init__(self):
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
        """Predict the next safe zone"""
        
        pred = ZonePrediction(
            prediction_id=f"zone_pred_{int(time.time())}",
            current_center_x=current_center_x,
            current_center_y=current_center_y,
            current_radius=current_radius,
            current_phase=current_phase,
        )
        
        # Get shrink ratio
        shrink_ratio = self.zone_shrink_ratios.get(
            current_phase + 1,
            0.5 - current_phase * 0.05,
        )
        
        # Predicted radius
        pred.predicted_radius = current_radius * shrink_ratio
        
        # Maximum shift distance (new center must be within current zone)
        max_shift = current_radius * (1 - shrink_ratio)
        
        # Strategy 1: Zone tends to shift away from center (toward edge)
        # Calculate direction bias based on current center position
        map_center_x, map_center_y = self._get_map_center(map_name)
        
        dx = current_center_x - map_center_x
        dy = current_center_y - map_center_y
        
        # Bias toward map edges (away from center)
        direction_magnitude = math.sqrt(dx**2 + dy**2)
        if direction_magnitude > 0:
            bias_x = (dx / direction_magnitude) * max_shift * 0.4
            bias_y = (dy / direction_magnitude) * max_shift * 0.4
        else:
            bias_x = 0
            bias_y = 0
        
        # Add some randomness
        random_angle = random.uniform(0, 2 * math.pi)
        random_distance = random.uniform(0, max_shift * 0.6)
        
        random_x = math.cos(random_angle) * random_distance
        random_y = math.sin(random_angle) * random_distance
        
        # Predicted center
        pred.predicted_center_x = current_center_x + bias_x + random_x
        pred.predicted_center_y = current_center_y + bias_y + random_y
        
        # Ensure within map bounds
        map_size = self._get_map_size(map_name)
        pred.predicted_center_x = max(pred.predicted_radius, min(map_size - pred.predicted_radius, pred.predicted_center_x))
        pred.predicted_center_y = max(pred.predicted_radius, min(map_size - pred.predicted_radius, pred.predicted_center_y))
        
        # Confidence based on phase (earlier = less confident)
        pred.confidence = min(0.9, 0.3 + current_phase * 0.08)
        
        # Generate alternative predictions
        for i in range(3):
            alt_angle = random.uniform(0, 2 * math.pi)
            alt_dist = random.uniform(0, max_shift)
            alt_x = current_center_x + math.cos(alt_angle) * alt_dist
            alt_y = current_center_y + math.sin(alt_angle) * alt_dist
            alt_conf = 0.1 + random.uniform(0, 0.15)
            pred.alternative_centers.append((alt_x, alt_y, alt_conf))
        
        # Check common final zones
        if current_phase >= 5:
            common_zones = self.common_final_zones.get(map_name, [])
            for cz_x, cz_y in common_zones:
                dist = math.sqrt(
                    (current_center_x - cz_x)**2 + (current_center_y - cz_y)**2
                )
                if dist < current_radius * 0.8:
                    # Boost probability near common zones
                    pred.alternative_centers.append((cz_x, cz_y, 0.25))
        
        self.predictions.append(pred)
        
        return pred
    
    def get_zone_probability(
        self,
        x: float,
        y: float,
        prediction: ZonePrediction,
    ) -> float:
        """Get probability that a given point is in next zone"""
        
        # Distance from predicted center
        dist = math.sqrt(
            (x - prediction.predicted_center_x)**2 +
            (y - prediction.predicted_center_y)**2
        )
        
        # Base probability from main prediction
        if dist <= prediction.predicted_radius:
            base_prob = 1.0 - (dist / prediction.predicted_radius) * 0.3
        else:
            base_prob = max(0, 1.0 - (dist - prediction.predicted_radius) / 200)
        
        # Check alternatives
        alt_prob = 0
        for alt_x, alt_y, alt_conf in prediction.alternative_centers:
            alt_dist = math.sqrt((x - alt_x)**2 + (y - alt_y)**2)
            if alt_dist <= prediction.predicted_radius:
                alt_prob = max(alt_prob, alt_conf)
        
        return max(base_prob * prediction.confidence, alt_prob)
    
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