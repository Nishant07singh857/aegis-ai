# backend/app/agents/narrative_agents/comeback_detector.py
"""
AEGIS AI - Comeback Detector
Detects comeback stories in real-time - teams overcoming
significant disadvantages to achieve victory.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque, defaultdict
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.comeback")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ComebackType(str, Enum):
    """Types of comebacks"""
    HP_COMEBACK = "hp_comeback"           # Won despite low HP
    NUMBERS_COMEBACK = "numbers_comeback"  # Won with fewer players
    PLACEMENT_COMEBACK = "placement_comeback"  # Bad early, great late
    TOURNAMENT_COMEBACK = "tournament_comeback"  # Across matches
    CLUTCH_COMEBACK = "clutch_comeback"    # Won 1vX
    ZONE_COMEBACK = "zone_comeback"       # Survived zone disadvantage

class ComebackStage(str, Enum):
    """Stages of a comeback"""
    ADVERSITY = "adversity"       # The low point
    TURNING_POINT = "turning_point"  # Things start changing
    MOMENTUM = "momentum"        # Building success
    TRIUMPH = "triumph"         # Comeback complete

@dataclass
class ComebackStory:
    """A detected comeback story"""
    comeback_id: str
    comeback_type: ComebackType
    stage: ComebackStage = ComebackStage.ADVERSITY
    
    # Who
    team_id: str = ""
    team_name: str = ""
    player_id: str = ""
    player_name: str = ""
    
    # The adversity
    initial_disadvantage: str = ""
    disadvantage_score: float = 0.0  # 0-100 (higher = worse)
    
    # The turning point
    turning_point: str = ""
    turning_point_time: float = 0.0
    
    # The triumph
    final_result: str = ""
    comeback_magnitude: float = 0.0  # How impressive (0-100)
    
    # Timing
    detected_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    
    # Entertainment
    entertainment_value: float = 0.0
    broadcast_priority: int = 0
    
    # Events
    key_events: List[Dict[str, Any]] = field(default_factory=list)


class ComebackDetector(BaseAgent):
    """
    Detects comeback stories in real-time.
    
    Monitors for:
    - Teams winning fights from HP disadvantage
    - Teams winning with fewer players
    - Players winning clutch situations
    - Teams surviving outside zone
    - Tournament comebacks across matches
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        self.active_comebacks: Dict[str, ComebackStory] = {}
        self.comeback_history: deque = deque(maxlen=50)
        
        # Team state tracking for comeback detection
        self.team_hp_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        self.team_placement_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=20))
        
        self._log = logger.bind(component="comeback_detector")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # COMEBACK DETECTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def detect_comeback(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        team_state: Dict[str, Any],
    ) -> Optional[ComebackStory]:
        """Detect if an event represents a comeback"""
        
        comeback = None
        
        if event_type == "fight_won_low_hp":
            comeback = self._detect_hp_comeback(event_data, team_state)
        elif event_type == "fight_won_disadvantage":
            comeback = self._detect_numbers_comeback(event_data, team_state)
        elif event_type == "clutch_win":
            comeback = self._detect_clutch_comeback(event_data)
        elif event_type == "survived_zone":
            comeback = self._detect_zone_comeback(event_data)
        elif event_type == "placement_improvement":
            comeback = self._detect_placement_comeback(event_data)
        
        if comeback:
            self.active_comebacks[comeback.comeback_id] = comeback
            self._log.info(
                f"Comeback detected: {comeback.team_name}",
                type=comeback.comeback_type.value,
                magnitude=round(comeback.comeback_magnitude, 1),
            )
        
        return comeback
    
    def _detect_hp_comeback(
        self,
        data: Dict[str, Any],
        state: Dict[str, Any],
    ) -> Optional[ComebackStory]:
        """Detect HP-based comeback"""
        
        team_hp = state.get("hp_percent", 100)
        enemy_hp = data.get("enemy_hp_percent", 100)
        
        hp_deficit = enemy_hp - team_hp
        
        if hp_deficit < 30:  # Need significant HP disadvantage
            return None
        
        comeback = ComebackStory(
            comeback_id=f"hp_comeback_{int(time.time())}",
            comeback_type=ComebackType.HP_COMEBACK,
            stage=ComebackStage.TRIUMPH,
            team_id=data.get("team_id", ""),
            team_name=data.get("team_name", ""),
            initial_disadvantage=f"Down {hp_deficit:.0f}% in total HP",
            disadvantage_score=min(100, hp_deficit * 2),
            turning_point=data.get("turning_point", "Won despite HP disadvantage"),
            final_result=f"Won fight with only {team_hp:.0f}% HP remaining",
            comeback_magnitude=min(100, hp_deficit * 1.5),
            entertainment_value=min(100, hp_deficit * 2),
        )
        
        comeback.stage = ComebackStage.TRIUMPH
        comeback.completed_at = time.time()
        
        return comeback
    
    def _detect_numbers_comeback(
        self,
        data: Dict[str, Any],
        state: Dict[str, Any],
    ) -> Optional[ComebackStory]:
        """Detect numbers-based comeback"""
        
        team_alive = state.get("alive_count", 4)
        enemy_alive = data.get("enemy_alive_count", 4)
        
        deficit = enemy_alive - team_alive
        
        if deficit < 1:
            return None
        
        comeback = ComebackStory(
            comeback_id=f"num_comeback_{int(time.time())}",
            comeback_type=ComebackType.NUMBERS_COMEBACK,
            stage=ComebackStage.TRIUMPH,
            team_id=data.get("team_id", ""),
            team_name=data.get("team_name", ""),
            initial_disadvantage=f"{team_alive}v{enemy_alive} disadvantage",
            disadvantage_score=deficit * 30,
            turning_point=data.get("turning_point", "Evened the odds"),
            final_result=f"Won {team_alive}v{enemy_alive} fight",
            comeback_magnitude=deficit * 25,
            entertainment_value=deficit * 30,
        )
        
        comeback.completed_at = time.time()
        
        return comeback
    
    def _detect_clutch_comeback(
        self,
        data: Dict[str, Any],
    ) -> Optional[ComebackStory]:
        """Detect clutch comeback"""
        
        enemies = data.get("enemy_count", 4)
        probability = data.get("win_probability", 0.1)
        
        if enemies < 2:
            return None
        
        comeback = ComebackStory(
            comeback_id=f"clutch_comeback_{int(time.time())}",
            comeback_type=ComebackType.CLUTCH_COMEBACK,
            stage=ComebackStage.TRIUMPH,
            player_id=data.get("player_id", ""),
            player_name=data.get("player_name", ""),
            team_id=data.get("team_id", ""),
            team_name=data.get("team_name", ""),
            initial_disadvantage=f"1v{enemies} with {probability:.0%} win chance",
            disadvantage_score=95,
            turning_point=f"First elimination in 1v{enemies}",
            final_result=f"Won 1v{enemies} clutch!",
            comeback_magnitude=95,
            entertainment_value=100,
        )
        
        comeback.completed_at = time.time()
        comeback.broadcast_priority = 100
        
        return comeback
    
    def _detect_zone_comeback(
        self,
        data: Dict[str, Any],
    ) -> Optional[ComebackStory]:
        """Detect zone survival comeback"""
        
        zone_damage = data.get("zone_damage_taken", 0)
        
        if zone_damage < 50:
            return None
        
        comeback = ComebackStory(
            comeback_id=f"zone_comeback_{int(time.time())}",
            comeback_type=ComebackType.ZONE_COMEBACK,
            team_id=data.get("team_id", ""),
            team_name=data.get("team_name", ""),
            initial_disadvantage=f"Took {zone_damage} zone damage",
            disadvantage_score=min(80, zone_damage),
            turning_point="Made it into zone",
            final_result="Survived zone and continued fighting",
            comeback_magnitude=min(80, zone_damage * 0.8),
            entertainment_value=60,
        )
        
        return comeback
    
    def _detect_placement_comeback(
        self,
        data: Dict[str, Any],
    ) -> Optional[ComebackStory]:
        """Detect placement comeback across matches"""
        
        previous_placement = data.get("previous_placement", 16)
        current_placement = data.get("current_placement", 1)
        
        improvement = previous_placement - current_placement
        
        if improvement < 5:
            return None
        
        comeback = ComebackStory(
            comeback_id=f"place_comeback_{int(time.time())}",
            comeback_type=ComebackType.PLACEMENT_COMEBACK,
            team_id=data.get("team_id", ""),
            team_name=data.get("team_name", ""),
            initial_disadvantage=f"Placed #{previous_placement} last match",
            disadvantage_score=previous_placement * 5,
            turning_point="Turned their tournament around",
            final_result=f"Finished #{current_placement} this match",
            comeback_magnitude=improvement * 8,
            entertainment_value=improvement * 7,
        )
        
        return comeback
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_active_comebacks(self) -> List[Dict[str, Any]]:
        """Get active comeback stories"""
        return [
            {
                "team_name": c.team_name,
                "player_name": c.player_name,
                "type": c.comeback_type.value,
                "stage": c.stage.value,
                "disadvantage": c.initial_disadvantage,
                "magnitude": round(c.comeback_magnitude, 1),
                "entertainment": round(c.entertainment_value, 1),
            }
            for c in self.active_comebacks.values()
        ]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get comeback detector statistics"""
        return {
            "active_comebacks": len(self.active_comebacks),
            "total_detected": len(self.comeback_history) + len(self.active_comebacks),
            "by_type": {
                t.value: len([c for c in self.active_comebacks.values() if c.comeback_type == t])
                for t in ComebackType
            },
        }
    
    def reset(self) -> None:
        """Reset comeback detector"""
        self.active_comebacks.clear()
        self.comeback_history.clear()
        self.team_hp_history.clear()
        self.team_placement_history.clear()
        self._log.info("Comeback detector reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("player_knocked")
        self.subscribe_to_event("kill_confirmed")
        self.subscribe_to_event("team_eliminated")

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
