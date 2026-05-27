# backend/app/agents/referee_agents/cheating_detector.py
"""
AEGIS AI - Cheating Detector
Detects suspicious behavior patterns that may indicate cheating.
Analyzes aim patterns, movement anomalies, and statistical outliers.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque, defaultdict
import structlog

logger = structlog.get_logger("aegis.agents.cheating")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class CheatType(str, Enum):
    """Types of potential cheating"""
    AIMBOT = "aimbot"                    # Auto-aim
    WALLHACK = "wallhack"              # Seeing through walls
    ESP = "esp"                        # Extra sensory perception
    SPEED_HACK = "speed_hack"          # Movement speed manipulation
    RECOIL_SCRIPT = "recoil_script"    # No recoil scripts
    RADAR_HACK = "radar_hack"         # Mini-map hacks
    DAMAGE_MOD = "damage_mod"         # Damage modification
    TELEPORT = "teleport"             # Position manipulation
    AUTO_HEAL = "auto_heal"           # Automatic healing
    AUTO_LOOT = "auto_loot"           # Automatic looting
    MAGIC_BULLET = "magic_bullet"     # Impossible shots
    GHOST_MODE = "ghost_mode"         # Invisibility exploits

class DetectionConfidence(str, Enum):
    """Confidence in cheating detection"""
    CERTAIN = "certain"           # 95%+ sure
    HIGH = "high"                # 80-95%
    MODERATE = "moderate"        # 60-80%
    LOW = "low"                 # 40-60%
    SUSPICIOUS = "suspicious"   # 20-40%
    INCONCLUSIVE = "inconclusive"  # <20%

@dataclass
class SuspiciousActivity:
    """A detected suspicious activity"""
    activity_id: str
    cheat_type: CheatType
    confidence: DetectionConfidence = DetectionConfidence.SUSPICIOUS
    confidence_score: float = 0.0  # 0-100
    
    # Who
    player_id: str = ""
    player_name: str = ""
    team_id: str = ""
    team_name: str = ""
    
    # Evidence
    description: str = ""
    evidence_points: List[Dict[str, Any]] = field(default_factory=list)
    
    # Statistical analysis
    headshot_percentage: float = 0.0
    accuracy_percentage: float = 0.0
    reaction_time_ms: float = 0.0
    tracking_smoothness: float = 0.0
    movement_anomaly_score: float = 0.0
    
    # Comparison to normal
    deviation_from_average: float = 0.0  # Standard deviations from mean
    is_statistical_outlier: bool = False
    
    # Timing
    detected_at: float = field(default_factory=time.time)
    match_id: str = ""
    
    # Status
    is_reviewed: bool = False
    review_result: str = ""
    is_confirmed_cheating: Optional[bool] = None


class CheatingDetector:
    """
    Detects suspicious behavior that may indicate cheating.
    
    Analyzes:
    - Aim patterns (snapping, tracking, headshot rates)
    - Movement anomalies (speed, teleportation)
    - Statistical outliers (accuracy, damage, kills)
    - Reaction times (inhuman speeds)
    - Behavior patterns (wall tracking, pre-firing)
    
    IMPORTANT: This system flags SUSPICIOUS behavior for human review.
    It does NOT automatically ban players. All detections require
    human verification before action is taken.
    """
    
    def __init__(self):
        # Suspicious activities
        self.active_detections: Dict[str, SuspiciousActivity] = {}
        self.detection_history: deque = deque(maxlen=200)
        
        # Player statistics for baseline comparison
        self.player_aim_stats: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        self.player_movement_stats: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        
        # Baseline statistics (what "normal" looks like)
        self.baselines = {
            "avg_headshot_pct": 15.0,       # Average headshot percentage
            "avg_accuracy": 25.0,           # Average accuracy
            "avg_reaction_ms": 250.0,       # Average reaction time
            "max_legit_headshot_pct": 45.0,  # Max legitimate headshot %
            "max_legit_accuracy": 55.0,      # Max legitimate accuracy
            "min_human_reaction_ms": 150.0,  # Fastest human reaction
            "max_legit_kpm": 8.0,           # Max legitimate kills per minute
        }
        
        # Thresholds for flagging
        self.thresholds = {
            "headshot_outlier_std": 3.0,      # Standard deviations
            "accuracy_outlier_std": 3.0,
            "reaction_time_min_ms": 100.0,    # Below this = suspicious
            "snap_angle_threshold": 30.0,      # Degrees - instant aim snap
            "tracking_perfect_score": 95.0,    # Too perfect tracking
            "movement_anomaly_score": 70.0,    # Movement anomaly threshold
        }
        
        self._log = logger.bind(component="cheating_detector")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CHEATING DETECTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def analyze_player_behavior(
        self,
        player_id: str,
        player_name: str,
        team_id: str,
        team_name: str,
        aim_data: Dict[str, Any],
        movement_data: Dict[str, Any],
        match_context: Dict[str, Any],
    ) -> List[SuspiciousActivity]:
        """Analyze player behavior for suspicious patterns"""
        
        activities = []
        
        # Check aim patterns
        aim_activity = self._check_aim_patterns(
            player_id, player_name, team_id, team_name,
            aim_data, match_context,
        )
        if aim_activity:
            activities.append(aim_activity)
        
        # Check movement anomalies
        movement_activity = self._check_movement_anomalies(
            player_id, player_name, team_id, team_name,
            movement_data, match_context,
        )
        if movement_activity:
            activities.append(movement_activity)
        
        # Check statistical outliers
        stats_activity = self._check_statistical_outliers(
            player_id, player_name, team_id, team_name,
            aim_data, match_context,
        )
        if stats_activity:
            activities.append(stats_activity)
        
        # Check reaction times
        reaction_activity = self._check_reaction_times(
            player_id, player_name, team_id, team_name,
            aim_data, match_context,
        )
        if reaction_activity:
            activities.append(reaction_activity)
        
        # Store activities
        for activity in activities:
            self.active_detections[activity.activity_id] = activity
        
        return activities
    
    def _check_aim_patterns(
        self,
        player_id: str,
        player_name: str,
        team_id: str,
        team_name: str,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[SuspiciousActivity]:
        """Check for aimbot-like patterns"""
        
        headshot_pct = data.get("headshot_percentage", 0)
        accuracy = data.get("accuracy", 0)
        snap_count = data.get("snap_events", 0)
        tracking_score = data.get("tracking_smoothness", 50)
        
        concerns = []
        confidence = 0.0
        
        # Extremely high headshot percentage
        if headshot_pct > self.baselines["max_legit_headshot_pct"]:
            concerns.append(f"Headshot rate {headshot_pct:.1f}% exceeds maximum legitimate rate")
            confidence += 25
        
        # Very high accuracy
        if accuracy > self.baselines["max_legit_accuracy"]:
            concerns.append(f"Accuracy {accuracy:.1f}% exceeds maximum legitimate rate")
            confidence += 20
        
        # Multiple snap events (instant aim lock)
        if snap_count >= 3:
            concerns.append(f"{snap_count} aim snap events detected")
            confidence += 30
        
        # Too perfect tracking
        if tracking_score > self.thresholds["tracking_perfect_score"]:
            concerns.append(f"Tracking smoothness {tracking_score:.1f} too perfect")
            confidence += 25
        
        if not concerns:
            return None
        
        confidence = min(95, confidence + 10)  # Base confidence
        
        return SuspiciousActivity(
            activity_id=f"aim_{int(time.time())}_{player_id[:8]}",
            cheat_type=CheatType.AIMBOT,
            confidence=self._determine_confidence(confidence),
            confidence_score=confidence,
            player_id=player_id,
            player_name=player_name,
            team_id=team_id,
            team_name=team_name,
            description="; ".join(concerns),
            headshot_percentage=headshot_pct,
            accuracy_percentage=accuracy,
            tracking_smoothness=tracking_score,
            match_id=context.get("match_id", ""),
        )
    
    def _check_movement_anomalies(
        self,
        player_id: str,
        player_name: str,
        team_id: str,
        team_name: str,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[SuspiciousActivity]:
        """Check for movement anomalies (speed hack, teleport)"""
        
        speed_events = data.get("speed_anomalies", 0)
        teleport_events = data.get("teleport_events", 0)
        movement_score = data.get("movement_anomaly_score", 0)
        
        concerns = []
        confidence = 0.0
        
        if speed_events > 0:
            concerns.append(f"{speed_events} speed anomaly events")
            confidence += 30
        
        if teleport_events > 0:
            concerns.append(f"{teleport_events} teleport events detected")
            confidence += 40
            return SuspiciousActivity(
                activity_id=f"teleport_{int(time.time())}_{player_id[:8]}",
                cheat_type=CheatType.TELEPORT,
                confidence=DetectionConfidence.HIGH,
                confidence_score=85,
                player_id=player_id,
                player_name=player_name,
                team_id=team_id,
                team_name=team_name,
                description="; ".join(concerns),
                movement_anomaly_score=movement_score,
                match_id=context.get("match_id", ""),
            )
        
        if movement_score > self.thresholds["movement_anomaly_score"]:
            concerns.append(f"Movement anomaly score: {movement_score:.1f}")
            confidence += 25
        
        if not concerns:
            return None
        
        return SuspiciousActivity(
            activity_id=f"move_{int(time.time())}_{player_id[:8]}",
            cheat_type=CheatType.SPEED_HACK if speed_events > 0 else CheatType.ESP,
            confidence=self._determine_confidence(confidence + 10),
            confidence_score=confidence + 10,
            player_id=player_id,
            player_name=player_name,
            team_id=team_id,
            team_name=team_name,
            description="; ".join(concerns),
            movement_anomaly_score=movement_score,
            match_id=context.get("match_id", ""),
        )
    
    def _check_statistical_outliers(
        self,
        player_id: str,
        player_name: str,
        team_id: str,
        team_name: str,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[SuspiciousActivity]:
        """Check for statistical outliers"""
        
        kills_per_minute = data.get("kills_per_minute", 0)
        damage_per_minute = data.get("damage_per_minute", 0)
        
        concerns = []
        confidence = 0.0
        
        if kills_per_minute > self.baselines["max_legit_kpm"]:
            concerns.append(f"Kill rate {kills_per_minute:.1f}/min exceeds maximum")
            confidence += 30
        
        if damage_per_minute > 2000:
            concerns.append(f"Damage rate {damage_per_minute:.0f}/min unusually high")
            confidence += 20
        
        if not concerns:
            return None
        
        # Calculate deviation from average
        deviation = kills_per_minute / max(1, self.baselines["max_legit_kpm"])
        
        return SuspiciousActivity(
            activity_id=f"stats_{int(time.time())}_{player_id[:8]}",
            cheat_type=CheatType.DAMAGE_MOD,
            confidence=self._determine_confidence(confidence + 10),
            confidence_score=confidence + 10,
            player_id=player_id,
            player_name=player_name,
            team_id=team_id,
            team_name=team_name,
            description="; ".join(concerns),
            deviation_from_average=deviation,
            is_statistical_outlier=(deviation > 2.0),
            match_id=context.get("match_id", ""),
        )
    
    def _check_reaction_times(
        self,
        player_id: str,
        player_name: str,
        team_id: str,
        team_name: str,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[SuspiciousActivity]:
        """Check for inhuman reaction times"""
        
        avg_reaction = data.get("average_reaction_ms", 250)
        fastest_reaction = data.get("fastest_reaction_ms", 150)
        
        concerns = []
        confidence = 0.0
        
        if fastest_reaction < self.thresholds["reaction_time_min_ms"]:
            concerns.append(f"Fastest reaction {fastest_reaction:.0f}ms below human minimum")
            confidence += 35
        
        if avg_reaction < self.baselines["min_human_reaction_ms"]:
            concerns.append(f"Average reaction {avg_reaction:.0f}ms below human range")
            confidence += 30
        
        if not concerns:
            return None
        
        return SuspiciousActivity(
            activity_id=f"react_{int(time.time())}_{player_id[:8]}",
            cheat_type=CheatType.AIMBOT,
            confidence=self._determine_confidence(confidence + 15),
            confidence_score=confidence + 15,
            player_id=player_id,
            player_name=player_name,
            team_id=team_id,
            team_name=team_name,
            description="; ".join(concerns),
            reaction_time_ms=avg_reaction,
            match_id=context.get("match_id", ""),
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # UTILITIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _determine_confidence(self, score: float) -> DetectionConfidence:
        """Determine confidence level from score"""
        if score >= 90:
            return DetectionConfidence.CERTAIN
        elif score >= 70:
            return DetectionConfidence.HIGH
        elif score >= 50:
            return DetectionConfidence.MODERATE
        elif score >= 30:
            return DetectionConfidence.LOW
        elif score >= 15:
            return DetectionConfidence.SUSPICIOUS
        return DetectionConfidence.INCONCLUSIVE
    
    def review_detection(
        self,
        activity_id: str,
        is_cheating: bool,
        reviewer: str = "human_reviewer",
    ) -> None:
        """Record human review of a detection"""
        
        activity = self.active_detections.get(activity_id)
        if not activity:
            return
        
        activity.is_reviewed = True
        activity.is_confirmed_cheating = is_cheating
        activity.review_result = f"Reviewed by {reviewer}: {'Confirmed' if is_cheating else 'Cleared'}"
        
        if is_cheating:
            self._log.warning(
                f"Cheating CONFIRMED: {activity.player_name}",
                cheat_type=activity.cheat_type.value,
            )
        else:
            self._log.info(f"Player cleared: {activity.player_name}")
        
        # Move to history
        self.detection_history.append(activity)
        del self.active_detections[activity_id]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_active_detections(self) -> List[Dict[str, Any]]:
        """Get active suspicious activities"""
        return [
            {
                "activity_id": a.activity_id,
                "cheat_type": a.cheat_type.value,
                "player_name": a.player_name,
                "team_name": a.team_name,
                "confidence": a.confidence.value,
                "confidence_score": round(a.confidence_score, 1),
                "description": a.description,
            }
            for a in self.active_detections.values()
        ]
    
    def get_player_history(self, player_id: str) -> List[Dict[str, Any]]:
        """Get detection history for a player"""
        return [
            {
                "cheat_type": a.cheat_type.value,
                "confidence": a.confidence.value,
                "is_confirmed": a.is_confirmed_cheating,
                "detected_at": a.detected_at,
            }
            for a in self.detection_history
            if a.player_id == player_id
        ] + [
            {
                "cheat_type": a.cheat_type.value,
                "confidence": a.confidence.value,
                "is_confirmed": None,
                "detected_at": a.detected_at,
            }
            for a in self.active_detections.values()
            if a.player_id == player_id
        ]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cheating detector statistics"""
        return {
            "active_detections": len(self.active_detections),
            "total_detections": len(self.detection_history) + len(self.active_detections),
            "by_confidence": {
                c.value: len([
                    a for a in self.active_detections.values()
                    if a.confidence == c
                ])
                for c in DetectionConfidence
            },
            "by_type": {
                t.value: len([
                    a for a in self.active_detections.values()
                    if a.cheat_type == t
                ])
                for t in CheatType
            },
        }
    
    def reset(self) -> None:
        """Reset cheating detector"""
        self.active_detections.clear()
        self.detection_history.clear()
        self._log.info("Cheating detector reset")