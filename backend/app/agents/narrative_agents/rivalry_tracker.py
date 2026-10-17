# backend/app/agents/narrative_agents/rivalry_tracker.py
"""
AEGIS AI - Rivalry Tracker
Tracks team and player rivalries over time,
calculates rivalry intensity, and provides rivalry context.
"""

import time
from typing import Dict, List, Optional, Any, Set
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.rivalry")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class RivalryLevel(str, Enum):
    """Rivalry intensity levels"""
    HISTORIC = "historic"         # Legendary rivalry (10+ encounters)
    INTENSE = "intense"          # Strong rivalry (5+ encounters)
    DEVELOPING = "developing"    # Growing rivalry (3+ encounters)
    EMERGING = "emerging"        # New rivalry (2 encounters)
    DORMANT = "dormant"         # Historic but inactive

class RivalryType(str, Enum):
    """Types of rivalries"""
    TEAM_VS_TEAM = "team_vs_team"
    PLAYER_VS_PLAYER = "player_vs_player"
    PLAYER_VS_TEAM = "player_vs_team"  # Player against former team
    REGIONAL = "regional"              # Same region rivalry
    ORGANIZATION = "organization"       # Same org rivalry

@dataclass
class RivalryEncounter:
    """A single encounter in a rivalry"""
    encounter_id: str
    timestamp: float = field(default_factory=time.time)
    match_id: str = ""
    tournament_name: str = ""
    
    # Participants
    team_a: str = ""
    team_b: str = ""
    
    # Outcome
    winner: str = ""
    team_a_kills: int = 0
    team_b_kills: int = 0
    team_a_damage: int = 0
    team_b_damage: int = 0
    
    # Context
    was_elimination: bool = False  # Did one eliminate the other
    was_final_circle: bool = False  # Was it in final circle
    zone_phase: int = 0
    
    # Hype
    excitement_score: float = 0.0
    clutch_moment: bool = False

@dataclass
class Rivalry:
    """Complete rivalry record"""
    rivalry_id: str
    rivalry_type: RivalryType = RivalryType.TEAM_VS_TEAM
    level: RivalryLevel = RivalryLevel.EMERGING
    
    # Participants
    team_a: str = ""
    team_b: str = ""
    player_a: str = ""
    player_b: str = ""
    
    # Names
    team_a_name: str = ""
    team_b_name: str = ""
    player_a_name: str = ""
    player_b_name: str = ""
    
    # Stats
    encounters: List[RivalryEncounter] = field(default_factory=list)
    total_encounters: int = 0
    team_a_wins: int = 0
    team_b_wins: int = 0
    
    # Intensity
    intensity_score: float = 0.0  # 0-100
    rivalry_name: str = ""  # e.g., "The Classic", "El Clasico"
    
    # Recent
    last_encounter: float = 0.0
    last_winner: str = ""
    streak: int = 0  # Positive = team_a, negative = team_b
    is_active: bool = True
    
    # Narrative
    backstory: str = ""
    key_moments: List[str] = field(default_factory=list)
    
    # Broadcast
    broadcast_priority: int = 0
    mention_count: int = 0


class RivalryTracker(BaseAgent):
    """
    Tracks rivalries between teams and players.
    
    Features:
    - Auto-detection of emerging rivalries
    - Rivalry intensity scoring
    - Head-to-head statistics
    - Rivalry naming
    - Streak tracking
    - Broadcast priority for rivalry matchups
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        # Active rivalries
        self.rivalries: Dict[str, Rivalry] = {}
        
        # Team and player indices
        self.team_rivalries: Dict[str, Set[str]] = defaultdict(set)
        self.player_rivalries: Dict[str, Set[str]] = defaultdict(set)
        
        # Rivalry names for special matchups
        self.rivalry_names = [
            "The Classic", "El Clasico", "The Derby",
            "The Showdown", "The Grudge Match", "The Rematch",
            "Battle of the Titans", "Civil War",
        ]
        
        self._log = logger.bind(component="rivalry_tracker")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # RIVALRY MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def record_encounter(
        self,
        team_a: str,
        team_b: str,
        team_a_name: str,
        team_b_name: str,
        winner: str,
        team_a_kills: int = 0,
        team_b_kills: int = 0,
        team_a_damage: int = 0,
        team_b_damage: int = 0,
        match_id: str = "",
        tournament_name: str = "",
        was_elimination: bool = False,
        was_final_circle: bool = False,
        excitement_score: float = 0.0,
    ) -> Rivalry:
        """Record an encounter between two teams"""
        
        # Get or create rivalry
        rivalry_key = self._get_rivalry_key(team_a, team_b)
        
        if rivalry_key not in self.rivalries:
            rivalry = self._create_rivalry(
                team_a, team_b,
                team_a_name, team_b_name,
            )
            self.rivalries[rivalry_key] = rivalry
        
        rivalry = self.rivalries[rivalry_key]
        
        # Create encounter
        encounter = RivalryEncounter(
            encounter_id=f"enc_{int(time.time())}_{team_a[:4]}_{team_b[:4]}",
            match_id=match_id,
            tournament_name=tournament_name,
            team_a=team_a,
            team_b=team_b,
            winner=winner,
            team_a_kills=team_a_kills,
            team_b_kills=team_b_kills,
            team_a_damage=team_a_damage,
            team_b_damage=team_b_damage,
            was_elimination=was_elimination,
            was_final_circle=was_final_circle,
            excitement_score=excitement_score,
        )
        
        rivalry.encounters.append(encounter)
        rivalry.total_encounters += 1
        rivalry.last_encounter = time.time()
        rivalry.last_winner = winner
        
        # Update wins
        if winner == team_a:
            rivalry.team_a_wins += 1
        elif winner == team_b:
            rivalry.team_b_wins += 1
        
        # Update streak
        if rivalry.streak > 0:
            rivalry.streak = rivalry.streak + 1 if winner == team_a else -1
        elif rivalry.streak < 0:
            rivalry.streak = rivalry.streak - 1 if winner == team_b else 1
        else:
            rivalry.streak = 1 if winner == team_a else -1
        
        # Update intensity
        self._update_intensity(rivalry)
        
        # Update indices
        self.team_rivalries[team_a].add(rivalry_key)
        self.team_rivalries[team_b].add(rivalry_key)
        
        self._log.info(
            f"Rivalry encounter: {team_a_name} vs {team_b_name}",
            winner=winner,
            total_encounters=rivalry.total_encounters,
        )
        
        return rivalry
    
    def _create_rivalry(
        self,
        team_a: str,
        team_b: str,
        team_a_name: str,
        team_b_name: str,
    ) -> Rivalry:
        """Create a new rivalry"""
        
        rivalry_id = f"rivalry_{team_a[:8]}_{team_b[:8]}"
        
        # Assign a rivalry name
        import random
        rivalry_name = random.choice(self.rivalry_names)
        
        return Rivalry(
            rivalry_id=rivalry_id,
            team_a=team_a,
            team_b=team_b,
            team_a_name=team_a_name,
            team_b_name=team_b_name,
            rivalry_name=rivalry_name,
            level=RivalryLevel.EMERGING,
        )
    
    def _get_rivalry_key(self, team_a: str, team_b: str) -> str:
        """Get consistent rivalry key (order independent)"""
        teams = sorted([team_a, team_b])
        return f"{teams[0]}_{teams[1]}"
    
    def _update_intensity(self, rivalry: Rivalry) -> None:
        """Update rivalry intensity score"""
        
        score = 0
        
        # Number of encounters
        score += min(40, rivalry.total_encounters * 5)
        
        # Close win/loss ratio = more intense
        total = rivalry.team_a_wins + rivalry.team_b_wins
        if total > 0:
            win_diff = abs(rivalry.team_a_wins - rivalry.team_b_wins)
            closeness = 1 - (win_diff / total)
            score += closeness * 30
        
        # Recent encounters
        recent = [
            e for e in rivalry.encounters[-3:]
            if time.time() - e.timestamp < 86400 * 30  # Within 30 days
        ]
        score += len(recent) * 10
        
        # Exciting encounters
        exciting = [e for e in rivalry.encounters if e.excitement_score > 70]
        score += len(exciting) * 5
        
        # Elimination encounters are more intense
        eliminations = [e for e in rivalry.encounters if e.was_elimination]
        score += len(eliminations) * 8
        
        rivalry.intensity_score = min(100, score)
        
        # Update level
        if rivalry.total_encounters >= 10:
            rivalry.level = RivalryLevel.HISTORIC
        elif rivalry.total_encounters >= 5:
            rivalry.level = RivalryLevel.INTENSE
        elif rivalry.total_encounters >= 3:
            rivalry.level = RivalryLevel.DEVELOPING
        elif rivalry.total_encounters >= 2:
            rivalry.level = RivalryLevel.EMERGING
        else:
            rivalry.level = RivalryLevel.DORMANT
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_rivalry(
        self,
        team_a: str,
        team_b: str,
    ) -> Optional[Rivalry]:
        """Get rivalry between two teams"""
        key = self._get_rivalry_key(team_a, team_b)
        return self.rivalries.get(key)
    
    def get_team_rivalries(self, team_id: str) -> List[Dict[str, Any]]:
        """Get all rivalries for a team"""
        
        rivalry_keys = self.team_rivalries.get(team_id, set())
        rivalries = []
        
        for key in rivalry_keys:
            r = self.rivalries.get(key)
            if r:
                other_team = r.team_b if r.team_a == team_id else r.team_a
                other_name = r.team_b_name if r.team_a == team_id else r.team_a_name
                
                rivalries.append({
                    "rivalry_id": r.rivalry_id,
                    "rivalry_name": r.rivalry_name,
                    "opponent": other_name,
                    "level": r.level.value,
                    "intensity": round(r.intensity_score, 1),
                    "encounters": r.total_encounters,
                    "our_wins": r.team_a_wins if r.team_a == team_id else r.team_b_wins,
                    "their_wins": r.team_b_wins if r.team_a == team_id else r.team_a_wins,
                    "last_winner": r.last_winner,
                    "streak": "Us" if (r.streak > 0 and r.team_a == team_id) or (r.streak < 0 and r.team_b == team_id) else "Them",
                })
        
        return sorted(rivalries, key=lambda r: r["intensity"], reverse=True)
    
    def get_top_rivalries(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top rivalries by intensity"""
        
        sorted_rivalries = sorted(
            self.rivalries.values(),
            key=lambda r: r.intensity_score,
            reverse=True,
        )[:limit]
        
        return [
            {
                "name": r.rivalry_name,
                "teams": f"{r.team_a_name} vs {r.team_b_name}",
                "level": r.level.value,
                "intensity": round(r.intensity_score, 1),
                "encounters": r.total_encounters,
                "record": f"{r.team_a_wins}-{r.team_b_wins}",
                "last_winner": r.last_winner,
            }
            for r in sorted_rivalries
        ]
    
    def is_rivalry_match(self, team_a: str, team_b: str) -> bool:
        """Check if this is a rivalry match"""
        rivalry = self.get_rivalry(team_a, team_b)
        return rivalry is not None and rivalry.level in [
            RivalryLevel.HISTORIC,
            RivalryLevel.INTENSE,
        ]
    
    def get_rivalry_context(
        self,
        team_a: str,
        team_b: str,
    ) -> Optional[Dict[str, Any]]:
        """Get rivalry context for commentary"""
        
        rivalry = self.get_rivalry(team_a, team_b)
        if not rivalry:
            return None
        
        return {
            "is_rivalry": True,
            "rivalry_name": rivalry.rivalry_name,
            "level": rivalry.level.value,
            "encounters": rivalry.total_encounters,
            "record": f"{rivalry.team_a_wins}-{rivalry.team_b_wins}",
            "last_winner": rivalry.last_winner,
            "streak_info": f"{abs(rivalry.streak)} match streak",
            "backstory": rivalry.backstory if rivalry.backstory else f"A {rivalry.level.value} rivalry with {rivalry.total_encounters} encounters.",
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get rivalry tracker statistics"""
        return {
            "total_rivalries": len(self.rivalries),
            "historic": len([r for r in self.rivalries.values() if r.level == RivalryLevel.HISTORIC]),
            "intense": len([r for r in self.rivalries.values() if r.level == RivalryLevel.INTENSE]),
            "developing": len([r for r in self.rivalries.values() if r.level == RivalryLevel.DEVELOPING]),
            "top_rivalries": self.get_top_rivalries(5),
        }
    
    def reset(self) -> None:
        """Reset rivalry tracker"""
        self.rivalries.clear()
        self.team_rivalries.clear()
        self.player_rivalries.clear()
        self._log.info("Rivalry tracker reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("kill_confirmed")
        self.subscribe_to_event("fight_detected")

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
