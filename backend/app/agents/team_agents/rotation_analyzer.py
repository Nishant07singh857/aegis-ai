# backend/app/agents/team_agents/rotation_analyzer.py
"""
AEGIS AI - Rotation Analyzer
Analyzes team movement patterns, rotation paths,
predicts future positions, and detects rotation strategies.
"""

import math
import time
from typing import Dict, List, Optional, Tuple, Any, Deque
from dataclasses import dataclass, field
from collections import deque
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord
import numpy as np

logger = structlog.get_logger("aegis.agents.rotation_analyzer")

@dataclass
class RotationPoint:
    """A point in a rotation path"""
    x: float
    y: float
    timestamp: float
    speed: float = 0.0
    is_stop: bool = False
    zone_phase: int = 0

@dataclass
class RotationPath:
    """Complete rotation path"""
    team_id: str
    points: Deque[RotationPoint] = field(default_factory=lambda: deque(maxlen=500))
    start_time: float = field(default_factory=time.time)
    total_distance: float = 0.0
    average_speed: float = 0.0
    max_speed: float = 0.0
    stops_count: int = 0
    path_type: str = "unknown"  # edge, center, zone_chasing, compound_holding
    
    def add_point(self, x: float, y: float, timestamp: float, zone_phase: int = 0) -> None:
        """Add a point to the rotation path"""
        if self.points:
            last = self.points[-1]
            distance = math.sqrt((x - last.x) ** 2 + (y - last.y) ** 2)
            dt = timestamp - last.timestamp
            
            speed = distance / dt if dt > 0 else 0
            is_stop = speed < 1.0 and dt > 2.0  # Stopped for >2 seconds
            
            if is_stop:
                self.stops_count += 1
            
            self.total_distance += distance
            self.max_speed = max(self.max_speed, speed)
            
            # Update average speed
            if len(self.points) > 1:
                total_time = timestamp - self.points[0].timestamp
                self.average_speed = self.total_distance / total_time if total_time > 0 else 0
        
        self.points.append(RotationPoint(
            x=x, y=y, timestamp=timestamp,
            speed=self.average_speed if self.points else 0,
            is_stop=is_stop if self.points else False,
            zone_phase=zone_phase,
        ))
    
    def get_recent_points(self, seconds: float = 30.0) -> List[RotationPoint]:
        """Get points from the last N seconds"""
        if not self.points:
            return []
        
        cutoff = time.time() - seconds
        return [p for p in self.points if p.timestamp >= cutoff]

@dataclass
class ZoneAnalysis:
    """Zone-related rotation analysis"""
    current_phase: int = 0
    zone_center_x: float = 0.0
    zone_center_y: float = 0.0
    zone_radius: float = 0.0
    distance_to_center: float = 0.0
    distance_to_edge: float = 0.0
    in_zone: bool = True
    time_to_reach_zone: float = 0.0  # Estimated seconds
    zone_danger_level: float = 0.0  # 0-100


class RotationAnalyzer(BaseAgent):
    """
    Analyzes team rotations and movement patterns.
    
    Tracks:
    - Complete rotation path
    - Movement speed and direction
    - Stop points (looting, camping)
    - Zone-relative positioning
    - Rotation strategy classification
    - Future position prediction
    - Vehicle usage
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus, team_id: str):
        super().__init__(config, event_bus)
        self.team_id = team_id
        
        # Current rotation
        self.current_path = RotationPath(team_id=team_id)
        
        # Historical rotations (last 10)
        self.rotation_history: Deque[RotationPath] = deque(maxlen=10)
        
        # Movement analysis
        self.movement_history: Deque[Dict[str, Any]] = deque(maxlen=100)
        self.direction_changes: int = 0
        self.last_direction: float = 0.0
        
        # Zone analysis
        self.zone_analysis = ZoneAnalysis()
        
        # Prediction
        self.predicted_next_position: Optional[Tuple[float, float]] = None
        self.prediction_confidence: float = 0.0
        
        # Strategy classification
        self.rotation_strategy: str = "unknown"
        self.strategy_confidence: float = 0.0
        
        # Vehicle tracking
        self.using_vehicle: bool = False
        self.vehicle_type: Optional[str] = None
        self.vehicle_path: Deque[Tuple[float, float, float]] = deque(maxlen=100)
        
        self._log = logger.bind(team_id=team_id)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MOVEMENT TRACKING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def add_position(
        self,
        x: float,
        y: float,
        timestamp: float,
        is_vehicle: bool = False,
        zone_phase: int = 0,
    ) -> None:
        """Add a new position to the rotation path"""
        self.current_path.add_point(x, y, timestamp, zone_phase)
        
        # Track vehicle usage
        if is_vehicle:
            self.vehicle_path.append((x, y, timestamp))
        
        # Track direction changes
        if len(self.current_path.points) >= 2:
            last_points = list(self.current_path.points)[-2:]
            dx = x - last_points[0].x
            dy = y - last_points[0].y
            current_direction = math.degrees(math.atan2(dy, dx))
            
            if self.last_direction != 0:
                angle_diff = abs(current_direction - self.last_direction)
                if angle_diff > 180:
                    angle_diff = 360 - angle_diff
                if angle_diff > 45:  # Significant direction change
                    self.direction_changes += 1
            
            self.last_direction = current_direction
        
        # Store movement analysis
        self.movement_history.append({
            "timestamp": timestamp,
            "position": (x, y),
            "speed": self.current_path.average_speed if self.current_path.points else 0,
            "in_vehicle": is_vehicle,
            "zone_phase": zone_phase,
        })
        
        # Predict next position
        self._predict_next_position()
        
        # Classify rotation strategy
        if len(self.current_path.points) > 10:
            self._classify_strategy()
    
    def add_stop_point(self, x: float, y: float, duration: float, activity: str = "unknown") -> None:
        """Record a stop point (looting, camping, healing)"""
        self._log.debug(
            f"Stop point: ({x:.0f}, {y:.0f}) for {duration:.1f}s | {activity}"
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ZONE ANALYSIS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def update_zone(
        self,
        center_x: float,
        center_y: float,
        radius: float,
        phase: int,
        team_x: float,
        team_y: float,
    ) -> None:
        """Update zone information and analyze position"""
        self.zone_analysis.current_phase = phase
        self.zone_analysis.zone_center_x = center_x
        self.zone_analysis.zone_center_y = center_y
        self.zone_analysis.zone_radius = radius
        
        # Calculate distances
        distance_to_center = math.sqrt(
            (team_x - center_x) ** 2 + (team_y - center_y) ** 2
        )
        self.zone_analysis.distance_to_center = distance_to_center
        self.zone_analysis.distance_to_edge = max(0, distance_to_center - radius)
        self.zone_analysis.in_zone = distance_to_center <= radius
        
        # Estimate time to reach zone
        if not self.zone_analysis.in_zone and self.current_path.average_speed > 0:
            self.zone_analysis.time_to_reach_zone = (
                self.zone_analysis.distance_to_edge / self.current_path.average_speed
            )
        else:
            self.zone_analysis.time_to_reach_zone = 0.0
        
        # Calculate zone danger
        self._calculate_zone_danger()
    
    def _calculate_zone_danger(self) -> None:
        """Calculate how dangerous the zone situation is"""
        danger = 0.0
        
        # Distance from zone edge
        if not self.zone_analysis.in_zone:
            distance = self.zone_analysis.distance_to_edge
            if distance > 500:
                danger += 40
            elif distance > 200:
                danger += 25
            elif distance > 100:
                danger += 15
            elif distance > 50:
                danger += 10
            else:
                danger += 5
        
        # Zone phase (later phases = more dangerous)
        phase_danger = {
            1: 0, 2: 5, 3: 10, 4: 20,
            5: 30, 6: 40, 7: 50, 8: 60,
        }
        danger += phase_danger.get(self.zone_analysis.current_phase, 30)
        
        # Time to reach zone
        if self.zone_analysis.time_to_reach_zone > 60:
            danger += 20
        elif self.zone_analysis.time_to_reach_zone > 30:
            danger += 10
        
        self.zone_analysis.zone_danger_level = min(100, danger)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PREDICTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _predict_next_position(self) -> None:
        """Predict team's next position based on movement history"""
        points = list(self.current_path.points)
        if len(points) < 5:
            self.prediction_confidence = 0.0
            return
        
        # Use last 5 points for prediction
        recent = points[-5:]
        
        # Calculate average velocity
        velocities = []
        for i in range(1, len(recent)):
            dt = recent[i].timestamp - recent[i-1].timestamp
            if dt > 0:
                vx = (recent[i].x - recent[i-1].x) / dt
                vy = (recent[i].y - recent[i-1].y) / dt
                velocities.append((vx, vy))
        
        if not velocities:
            return
        
        avg_vx = sum(v[0] for v in velocities) / len(velocities)
        avg_vy = sum(v[1] for v in velocities) / len(velocities)
        
        # Predict position in 5 seconds
        last_point = recent[-1]
        pred_x = last_point.x + avg_vx * 5
        pred_y = last_point.y + avg_vy * 5
        
        self.predicted_next_position = (pred_x, pred_y)
        
        # Calculate confidence based on consistency of movement
        vx_variance = sum((v[0] - avg_vx) ** 2 for v in velocities) / len(velocities)
        vy_variance = sum((v[1] - avg_vy) ** 2 for v in velocities) / len(velocities)
        
        consistency = 1.0 / (1.0 + vx_variance + vy_variance)
        self.prediction_confidence = min(0.95, consistency * 2)
    
    def predict_future_position(self, seconds_ahead: float = 10.0) -> Optional[Dict[str, Any]]:
        """Predict where the team will be in N seconds"""
        if not self.predicted_next_position or self.prediction_confidence < 0.3:
            return None
        
        # Scale prediction based on time
        points = list(self.current_path.points)
        if len(points) < 5:
            return None
        
        recent = points[-5:]
        avg_speed = self.current_path.average_speed
        
        # Get direction
        dx = recent[-1].x - recent[0].x
        dy = recent[-1].y - recent[0].y
        direction = math.degrees(math.atan2(dy, dx))
        
        distance = avg_speed * seconds_ahead
        pred_x = recent[-1].x + math.cos(math.radians(direction)) * distance
        pred_y = recent[-1].y + math.sin(math.radians(direction)) * distance
        
        return {
            "position": (pred_x, pred_y),
            "direction": direction,
            "confidence": self.prediction_confidence,
            "estimated_time": seconds_ahead,
        }
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STRATEGY CLASSIFICATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _classify_strategy(self) -> None:
        """Classify the team's rotation strategy"""
        points = list(self.current_path.points)
        if len(points) < 20:
            return
        
        # Analyze movement patterns
        stops = sum(1 for p in points if p.is_stop)
        total_time = points[-1].timestamp - points[0].timestamp
        stops_ratio = stops / len(points) if points else 0
        
        # Distance from map center (assuming 8000x8000 map)
        recent_x = np.mean([p.x for p in points[-10:]])
        recent_y = np.mean([p.y for p in points[-10:]])
        distance_from_center = math.sqrt(
            (recent_x - 4000) ** 2 + (recent_y - 4000) ** 2
        )
        
        # Direction changes
        direction_change_rate = self.direction_changes / len(points) if points else 0
        
        # Zone relationship
        zone_changes = 0
        for i in range(1, len(points)):
            if points[i].zone_phase != points[i-1].zone_phase:
                zone_changes += 1
        
        # Classify
        if distance_from_center > 3000:
            strategy = "edge_rotation"
            confidence = 0.7
        elif distance_from_center < 1000:
            strategy = "center_control"
            confidence = 0.8
        elif stops_ratio > 0.3:
            strategy = "compound_holding"
            confidence = 0.6
        elif direction_change_rate > 0.3:
            strategy = "zone_chasing"
            confidence = 0.5
        elif self.current_path.average_speed > 15:
            strategy = "aggressive_rotation"
            confidence = 0.6
        else:
            strategy = "balanced_rotation"
            confidence = 0.4
        
        self.rotation_strategy = strategy
        self.strategy_confidence = confidence
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # VEHICLE TRACKING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def vehicle_entered(self, vehicle_type: str) -> None:
        """Record vehicle entry"""
        self.using_vehicle = True
        self.vehicle_type = vehicle_type
        self._log.debug(f"Vehicle entered: {vehicle_type}")
    
    def vehicle_exited(self) -> None:
        """Record vehicle exit"""
        self.using_vehicle = False
        self.vehicle_type = None
        self._log.debug("Vehicle exited")
    
    def get_vehicle_path_summary(self) -> Dict[str, Any]:
        """Get vehicle usage summary"""
        if not self.vehicle_path:
            return {"used": False}
        
        points = list(self.vehicle_path)
        total_distance = 0
        for i in range(1, len(points)):
            dx = points[i][0] - points[i-1][0]
            dy = points[i][1] - points[i-1][1]
            total_distance += math.sqrt(dx**2 + dy**2)
        
        return {
            "used": True,
            "vehicle_type": self.vehicle_type,
            "total_distance": total_distance,
            "point_count": len(points),
            "start_time": points[0][2] if points else 0,
            "end_time": points[-1][2] if points else 0,
        }
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ANALYSIS QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_movement_summary(self) -> Dict[str, Any]:
        """Get movement analysis summary"""
        return {
            "total_distance": round(self.current_path.total_distance, 1),
            "average_speed": round(self.current_path.average_speed, 1),
            "max_speed": round(self.current_path.max_speed, 1),
            "stops_count": self.current_path.stops_count,
            "direction_changes": self.direction_changes,
            "path_type": self.current_path.path_type,
        }
    
    def get_rotation_analysis(self) -> Dict[str, Any]:
        """Get complete rotation analysis"""
        return {
            "strategy": self.rotation_strategy,
            "strategy_confidence": round(self.strategy_confidence, 2),
            "movement": self.get_movement_summary(),
            "zone": {
                "phase": self.zone_analysis.current_phase,
                "in_zone": self.zone_analysis.in_zone,
                "distance_to_center": round(self.zone_analysis.distance_to_center, 1),
                "distance_to_edge": round(self.zone_analysis.distance_to_edge, 1),
                "danger_level": round(self.zone_analysis.zone_danger_level, 1),
                "time_to_reach": round(self.zone_analysis.time_to_reach_zone, 1),
            },
            "prediction": {
                "next_position": self.predicted_next_position,
                "confidence": round(self.prediction_confidence, 2),
            } if self.predicted_next_position else None,
            "vehicle": self.get_vehicle_path_summary(),
        }
    
    def save_current_rotation(self) -> None:
        """Save current rotation to history and start new"""
        if len(list(self.current_path.points)) > 10:
            self.rotation_history.append(self.current_path)
        
        self.current_path = RotationPath(team_id=self.team_id)
        self.direction_changes = 0
        self.last_direction = 0.0
    
    def get_historical_rotations(self) -> List[Dict[str, Any]]:
        """Get historical rotation summaries"""
        return [
            {
                "total_distance": round(path.total_distance, 1),
                "average_speed": round(path.average_speed, 1),
                "stops_count": path.stops_count,
                "start_time": path.start_time,
                "path_type": path.path_type,
            }
            for path in self.rotation_history
        ]

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("zone_change")
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
