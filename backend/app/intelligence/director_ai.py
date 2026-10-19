import time
import structlog
from typing import Dict, List, Optional
from enum import Enum
from dataclasses import dataclass

logger = structlog.get_logger("aegis.director_ai")

class EventPriority(Enum):
    CRITICAL = 4 # Final zone, 1v1 clutch, squad wipe
    HIGH = 3     # Active fights, grenade knocks
    MEDIUM = 2   # Rotations, healing
    LOW = 1      # Looting, passive movement

@dataclass
class BroadcastEvent:
    id: str
    team_id: str
    type: str # "fight", "clutch", "rotation", "wipe"
    priority: EventPriority
    confidence: float # 0.0 to 1.0
    emotion_score: float # 0 to 100
    timestamp: float
    description: str

class DirectorAIEngine:
    """
    AEGIS AI - Central Director Engine.
    Implements: Priority Engine, Switch Cooldowns, Production Safety, 
    Emotion/Cinematic Engine, and AI Explainability.
    """
    def __init__(self):
        # Configuration rules per PRD
        self.MINIMUM_SCENE_TIME = 10.0 # seconds
        self.MAX_SWITCHES_PER_MIN = 8
        self.CONFIDENCE_THRESHOLD = 0.85
        
        # State
        self.current_pov_team: Optional[str] = None
        self.last_switch_time: float = 0.0
        self.switch_timestamps: List[float] = [] # For max_switches_per_min tracking
        self.is_clutch_active: bool = False
        self.current_zone_phase: int = 1
        
        # Explainability log
        self.decision_log: List[Dict] = []

    def _clean_switch_history(self, current_time: float):
        """Removes switch timestamps older than 60 seconds."""
        self.switch_timestamps = [ts for ts in self.switch_timestamps if current_time - ts <= 60.0]

    def evaluate_events(self, events: List[BroadcastEvent]) -> Optional[BroadcastEvent]:
        """
        1. REALTIME PRIORITY ENGINE
        3. CONFIDENCE FUSION SYSTEM
        Filters low confidence, prioritizes highest value event.
        """
        valid_events = [e for e in events if e.confidence >= self.CONFIDENCE_THRESHOLD]
        if not valid_events:
            return None
            
        # Sort by Priority -> Emotion Score -> Confidence
        sorted_events = sorted(
            valid_events, 
            key=lambda e: (e.priority.value, e.emotion_score, e.confidence), 
            reverse=True
        )
        return sorted_events[0]

    def process_tick(self, incoming_events: List[BroadcastEvent]) -> Optional[Dict]:
        """
        Main decision loop called by the Orchestrator/Event Bus.
        Returns a decision payload if a camera switch is required.
        """
        current_time = time.time()
        self._clean_switch_history(current_time)
        
        best_event = self.evaluate_events(incoming_events)
        if not best_event:
            return None # Hold current shot
            
        # If we are already watching this team, do nothing
        if self.current_pov_team == best_event.team_id:
            return None

        # 2. SWITCH COOLDOWN SYSTEM & 7. PRODUCTION SAFETY SYSTEM
        time_since_last_switch = current_time - self.last_switch_time
        
        # Safety Check 1: Stay on clutch until finished
        if self.is_clutch_active and best_event.priority != EventPriority.CRITICAL:
            self._log_decision(best_event, "REJECTED", "Stay on clutch until finished.")
            return None
            
        # Safety Check 2: Minimum scene time
        if time_since_last_switch < self.MINIMUM_SCENE_TIME:
            if best_event.priority.value <= EventPriority.HIGH.value:
                self._log_decision(best_event, "REJECTED", f"Cooldown active ({round(time_since_last_switch, 1)}s < {self.MINIMUM_SCENE_TIME}s).")
                return None
            else:
                # CRITICAL events bypass the 10-second rule, but still checked against max switches
                pass

        # Safety Check 3: Max switches per minute
        if len(self.switch_timestamps) >= self.MAX_SWITCHES_PER_MIN:
            self._log_decision(best_event, "REJECTED", "Max switches per minute (8) exceeded. Preventing chaotic production.")
            return None
            
        # --- EXECUTE SWITCH ---
        self.current_pov_team = best_event.team_id
        self.last_switch_time = current_time
        self.switch_timestamps.append(current_time)
        
        # 14. EMOTION + CINEMATIC ENGINE State Update
        if best_event.type == "clutch":
            self.is_clutch_active = True
        elif best_event.priority == EventPriority.CRITICAL:
            self.is_clutch_active = False # Reset on new critical override
            
        # 9. AI EXPLAINABILITY SYSTEM
        decision = {
            "action": "SWITCH_POV",
            "target_team": best_event.team_id,
            "reason": {
                "event_type": best_event.type,
                "priority_level": best_event.priority.name,
                "emotion_score": best_event.emotion_score,
                "confidence": best_event.confidence,
                "description": best_event.description
            },
            "timestamp": current_time
        }
        
        self._log_decision(best_event, "APPROVED", "Highest priority cinematic event.")
        logger.info("Director AI Switch Executed", target=best_event.team_id, reason=best_event.type)
        return decision
        
    def _log_decision(self, event: BroadcastEvent, outcome: str, explanation: str):
        """Records decisions for the AI Explainability System and Debugging."""
        log_entry = {
            "time": time.time(),
            "evaluated_team": event.team_id,
            "event_priority": event.priority.name,
            "outcome": outcome,
            "explanation": explanation
        }
        self.decision_log.append(log_entry)
        # Keep log size manageable
        if len(self.decision_log) > 100:
            self.decision_log.pop(0)

# ── Usage Example ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Dev-only demo (never executed in production — guarded by __name__).
    # Team IDs come from the designated simulator dataset
    # (app/api/v1/teams/demo_data.py), the single sanctioned source of
    # illustrative squad data. The engine itself is name-agnostic: in live
    # operation process_tick() receives team_id as an opaque string from the
    # orchestrator / event bus, never from a literal.
    from app.api.v1.teams.demo_data import DEMO_TEAMS

    demo_ids = [t["id"] for t in DEMO_TEAMS[:3]]
    director = DirectorAIEngine()

    events = [
        BroadcastEvent(id="e1", team_id=demo_ids[0], type="looting", priority=EventPriority.LOW, confidence=0.99, emotion_score=10, timestamp=time.time(), description="Looting Pochinki"),
        BroadcastEvent(id="e2", team_id=demo_ids[1], type="fight", priority=EventPriority.HIGH, confidence=0.92, emotion_score=75, timestamp=time.time(), description="Active 3v3 fight at Rozhok"),
        BroadcastEvent(id="e3", team_id=demo_ids[2], type="wipe", priority=EventPriority.CRITICAL, confidence=0.88, emotion_score=95, timestamp=time.time(), description="Squad wipe in progress")
    ]

    # 1. demo_ids[2] has critical priority wipe, Director should switch
    decision1 = director.process_tick(events)
    print("Tick 1:", decision1)

    # 2. Immediate tick again with demo_ids[1] fight. Should reject due to cooldown.
    decision2 = director.process_tick([events[1]])
    print("Tick 2 (Cooldown Test):", decision2)
    print("Explainability Log:", director.decision_log[-1])
