# backend/app/agents/analytics_agents/team_chemistry.py
"""
AEGIS AI - Team Chemistry Analyzer
Analyzes team synergy, coordination patterns,
and overall team chemistry for performance optimization.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog

logger = structlog.get_logger("aegis.agents.team_chemistry")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ChemistryLevel(str, Enum):
    """Team chemistry assessment levels"""
    EXCEPTIONAL = "exceptional"     # Perfect synergy
    EXCELLENT = "excellent"         # Very strong
    GOOD = "good"                   # Solid coordination
    AVERAGE = "average"             # Normal team play
    DEVELOPING = "developing"       # Still building
    POOR = "poor"                   # Lacking coordination
    TOXIC = "toxic"                # Negative synergy

class SynergyType(str, Enum):
    """Types of team synergy"""
    COMBAT = "combat"               # Fighting together
    ROTATION = "rotation"           # Movement coordination
    UTILITY = "utility"             # Combined utility usage
    REVIVE = "revive"              # Revival coordination
    COMMUNICATION = "communication" # Implied from behavior
    POSITIONING = "positioning"     # Spatial awareness
    TARGET_FOCUS = "target_focus"   # Focusing same enemies

@dataclass
class PlayerPairSynergy:
    """Synergy between two specific players"""
    player_a_id: str
    player_b_id: str
    player_a_name: str = ""
    player_b_name: str = ""
    
    # Synergy scores (0-100)
    overall_synergy: float = 0.0
    combat_synergy: float = 0.0
    rotation_synergy: float = 0.0
    revive_synergy: float = 0.0
    
    # Shared stats
    shared_kills: int = 0          # Kills where both damaged enemy
    shared_assists: int = 0
    revives_between: int = 0       # Revives of each other
    time_spent_together: float = 0.0
    avg_distance: float = 9999.0   # Average distance apart
    
    # Patterns
    trade_kill_rate: float = 0.0   # Rate of trading kills
    cover_fire_events: int = 0     # Covering fire provided
    
    matches_analyzed: int = 0

@dataclass
class ChemistryScore:
    """Complete team chemistry analysis"""
    team_id: str
    team_name: str
    
    # Overall
    overall_score: float = 0.0       # 0-100
    chemistry_level: ChemistryLevel = ChemistryLevel.AVERAGE
    
    # Category scores (0-100)
    combat_synergy: float = 0.0
    rotation_synergy: float = 0.0
    utility_synergy: float = 0.0
    revive_synergy: float = 0.0
    positioning_synergy: float = 0.0
    target_focus_synergy: float = 0.0
    
    # Player pairs
    player_pairs: Dict[str, PlayerPairSynergy] = field(default_factory=dict)
    best_pair: Optional[str] = None
    weakest_pair: Optional[str] = None
    
    # Team patterns
    avg_spacing: float = 0.0
    formation_changes: int = 0
    coordinated_pushes: int = 0
    successful_pushes: int = 0
    
    # Communication indicators
    quick_revive_rate: float = 0.0   # Revives within 5 seconds
    simultaneous_actions: int = 0     # Acting at same time
    target_switching_speed: float = 0.0  # How fast team switches targets
    
    # Improvement
    chemistry_trend: deque = field(default_factory=lambda: deque(maxlen=20))
    matches_analyzed: int = 0
    last_updated: float = field(default_factory=time.time)


class TeamChemistryAnalyzer:
    """
    Analyzes team chemistry and synergy patterns.
    
    Measures:
    - Combat synergy (fighting together effectively)
    - Rotation coordination (moving as a unit)
    - Utility synergy (combined smoke/grenade usage)
    - Revival coordination (quick revives)
    - Positioning awareness (spacing, formations)
    - Target focus (shooting same enemies)
    - Player pair synergies
    
    Identifies:
    - Strongest player duos
    - Weak links in team coordination
    - Chemistry trends over time
    - Areas for improvement
    """
    
    def __init__(self):
        # Team chemistry scores
        self.team_scores: Dict[str, ChemistryScore] = {}
        
        # Player pair tracking
        self.pair_tracking: Dict[str, Dict[str, PlayerPairSynergy]] = {}
        
        # Recent events for analysis
        self.recent_events: deque = deque(maxlen=500)
        
        # Configuration weights
        self.weights = {
            SynergyType.COMBAT: 0.25,
            SynergyType.ROTATION: 0.20,
            SynergyType.UTILITY: 0.15,
            SynergyType.REVIVE: 0.20,
            SynergyType.POSITIONING: 0.10,
            SynergyType.TARGET_FOCUS: 0.10,
        }
        
        self._log = logger.bind(component="team_chemistry")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CHEMISTRY CALCULATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def analyze_team_chemistry(
        self,
        team_id: str,
        team_name: str,
        players: List[Dict[str, Any]],
        match_events: List[Dict[str, Any]],
        match_stats: Dict[str, Any],
    ) -> ChemistryScore:
        """
        Analyze team chemistry for a match.
        
        Args:
            team_id: Team identifier
            team_name: Team name
            players: List of player data
            match_events: Events from the match
            match_stats: Match statistics
            
        Returns:
            ChemistryScore with detailed analysis
        """
        
        # Get or create score
        if team_id not in self.team_scores:
            self.team_scores[team_id] = ChemistryScore(
                team_id=team_id,
                team_name=team_name,
            )
        
        score = self.team_scores[team_id]
        score.team_name = team_name
        
        # Calculate category scores
        score.combat_synergy = self._calculate_combat_synergy(players, match_events)
        score.rotation_synergy = self._calculate_rotation_synergy(players, match_events)
        score.utility_synergy = self._calculate_utility_synergy(players, match_events)
        score.revive_synergy = self._calculate_revive_synergy(players, match_events)
        score.positioning_synergy = self._calculate_positioning_synergy(players, match_stats)
        score.target_focus_synergy = self._calculate_target_focus_synergy(match_events)
        
        # Calculate weighted overall
        score.overall_score = (
            score.combat_synergy * self.weights[SynergyType.COMBAT] +
            score.rotation_synergy * self.weights[SynergyType.ROTATION] +
            score.utility_synergy * self.weights[SynergyType.UTILITY] +
            score.revive_synergy * self.weights[SynergyType.REVIVE] +
            score.positioning_synergy * self.weights[SynergyType.POSITIONING] +
            score.target_focus_synergy * self.weights[SynergyType.TARGET_FOCUS]
        )
        
        # Determine chemistry level
        score.chemistry_level = self._determine_level(score.overall_score)
        
        # Analyze player pairs
        self._analyze_player_pairs(team_id, players, match_events, score)
        
        # Update trend
        score.chemistry_trend.append({
            "score": score.overall_score,
            "timestamp": time.time(),
        })
        
        score.matches_analyzed += 1
        score.last_updated = time.time()
        
        self._log.info(
            f"Team chemistry: {team_name}",
            score=round(score.overall_score, 1),
            level=score.chemistry_level.value,
        )
        
        return score
    
    def _calculate_combat_synergy(
        self,
        players: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
    ) -> float:
        """Calculate combat synergy (fighting together)"""
        score = 0.0
        
        # Trade kills (avenging teammate deaths)
        trade_kills = sum(1 for e in events if e.get("type") == "trade_kill")
        teammate_deaths = sum(1 for e in events if e.get("type") == "player_death")
        if teammate_deaths > 0:
            trade_rate = trade_kills / teammate_deaths
            score += trade_rate * 40
        
        # Combined damage on same targets
        combined_damage_events = sum(
            1 for e in events 
            if e.get("type") == "damage" and e.get("team_damage_count", 0) >= 2
        )
        score += min(30, combined_damage_events * 3)
        
        # Simultaneous kills (kills within 2 seconds of each other)
        kill_times = [
            e.get("timestamp", 0) for e in events 
            if e.get("type") == "kill"
        ]
        kill_times.sort()
        simultaneous = 0
        for i in range(1, len(kill_times)):
            if kill_times[i] - kill_times[i-1] < 2.0:
                simultaneous += 1
        score += min(30, simultaneous * 5)
        
        return min(100, score)
    
    def _calculate_rotation_synergy(
        self,
        players: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
    ) -> float:
        """Calculate rotation coordination"""
        score = 50.0  # Base
        
        # Average team spacing
        avg_spacing = events[0].get("team_spacing", 30) if events else 30
        
        # Optimal spacing is 10-30 meters
        if 10 <= avg_spacing <= 30:
            score += 25
        elif avg_spacing < 10:
            score += 10  # Too close
        elif avg_spacing < 50:
            score += 5   # A bit spread
        else:
            score -= 15  # Too spread
        
        # Formation consistency
        formation_changes = sum(1 for e in events if e.get("type") == "formation_change")
        if formation_changes < 5:
            score += 15  # Stable formations
        elif formation_changes < 10:
            score += 5
        
        # Movement synchronization
        sync_movements = sum(1 for e in events if e.get("type") == "sync_movement")
        score += min(10, sync_movements * 2)
        
        return min(100, max(0, score))
    
    def _calculate_utility_synergy(
        self,
        players: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
    ) -> float:
        """Calculate utility coordination"""
        score = 0.0
        
        # Coordinated smoke usage
        smoke_events = [e for e in events if e.get("type") == "smoke_used"]
        coordinated_smokes = sum(
            1 for e in smoke_events 
            if e.get("coordinated", False)
        )
        if smoke_events:
            score += (coordinated_smokes / len(smoke_events)) * 35
        
        # Grenade + push combinations
        grenade_push = sum(1 for e in events if e.get("type") == "grenade_push")
        score += min(25, grenade_push * 5)
        
        # Combined utility (smoke + grenade same target)
        combined_utility = sum(
            1 for e in events 
            if e.get("type") == "combined_utility"
        )
        score += min(25, combined_utility * 5)
        
        # Flash + push coordination
        flash_push = sum(1 for e in events if e.get("type") == "flash_push")
        score += min(15, flash_push * 3)
        
        return min(100, score)
    
    def _calculate_revive_synergy(
        self,
        players: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
    ) -> float:
        """Calculate revival coordination"""
        score = 0.0
        
        # Quick revives (within 5 seconds of knock)
        revive_events = [e for e in events if e.get("type") == "revive"]
        quick_revives = sum(
            1 for e in revive_events 
            if e.get("time_to_revive", 10) < 5
        )
        
        if revive_events:
            quick_rate = quick_revives / len(revive_events)
            score += quick_rate * 40
        
        # Cover during revive
        covered_revives = sum(
            1 for e in revive_events 
            if e.get("covered", False)
        )
        if revive_events:
            score += (covered_revives / len(revive_events)) * 30
        
        # Successful revive rate
        total_knocks = sum(1 for e in events if e.get("type") == "knock")
        successful_revives = len(revive_events)
        if total_knocks > 0:
            success_rate = successful_revives / total_knocks
            score += success_rate * 30
        
        return min(100, score)
    
    def _calculate_positioning_synergy(
        self,
        players: List[Dict[str, Any]],
        stats: Dict[str, Any],
    ) -> float:
        """Calculate positioning awareness"""
        score = 50.0
        
        # Optimal spacing
        avg_spacing = stats.get("avg_team_spacing", 30)
        if 15 <= avg_spacing <= 35:
            score += 25
        elif avg_spacing < 15:
            score += 10
        elif avg_spacing < 60:
            score += 5
        else:
            score -= 15
        
        # Cover utilization
        cover_usage = stats.get("cover_usage_rate", 0.5)
        score += cover_usage * 25
        
        return min(100, max(0, score))
    
    def _calculate_target_focus_synergy(
        self,
        events: List[Dict[str, Any]],
    ) -> float:
        """Calculate how well team focuses same targets"""
        score = 0.0
        
        # Multiple players damaging same enemy
        focused_damage = sum(
            1 for e in events 
            if e.get("type") == "damage" and e.get("team_hits", 0) >= 2
        )
        score += min(40, focused_damage * 4)
        
        # Quick target switching
        target_switches = [e for e in events if e.get("type") == "target_switch"]
        fast_switches = sum(
            1 for e in target_switches 
            if e.get("switch_time", 5) < 2
        )
        if target_switches:
            score += (fast_switches / len(target_switches)) * 30
        
        # Assist rate
        kills = sum(1 for e in events if e.get("type") == "kill")
        assists = sum(1 for e in events if e.get("type") == "assist")
        if kills > 0:
            assist_rate = assists / kills
            score += min(30, assist_rate * 30)
        
        return min(100, score)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PLAYER PAIR ANALYSIS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _analyze_player_pairs(
        self,
        team_id: str,
        players: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
        score: ChemistryScore,
    ) -> None:
        """Analyze synergy between each player pair"""
        
        player_ids = [p.get("player_id") for p in players if p.get("player_id")]
        
        # Initialize pair tracking
        if team_id not in self.pair_tracking:
            self.pair_tracking[team_id] = {}
        
        for i, pid_a in enumerate(player_ids):
            for pid_b in player_ids[i+1:]:
                pair_key = f"{pid_a}_{pid_b}"
                
                if pair_key not in self.pair_tracking[team_id]:
                    self.pair_tracking[team_id][pair_key] = PlayerPairSynergy(
                        player_a_id=pid_a,
                        player_b_id=pid_b,
                    )
                
                pair = self.pair_tracking[team_id][pair_key]
                
                # Update pair stats
                pair.shared_kills += sum(
                    1 for e in events 
                    if e.get("type") == "kill" 
                    and pid_a in e.get("damagers", [])
                    and pid_b in e.get("damagers", [])
                )
                
                pair.revives_between += sum(
                    1 for e in events
                    if e.get("type") == "revive"
                    and ((e.get("reviver") == pid_a and e.get("revived") == pid_b)
                    or (e.get("reviver") == pid_b and e.get("revived") == pid_a))
                )
                
                # Calculate pair synergy
                pair.overall_synergy = (
                    pair.shared_kills * 15 +
                    pair.revives_between * 25 +
                    pair.shared_assists * 10
                )
                pair.overall_synergy = min(100, pair.overall_synergy)
                
                pair.matches_analyzed += 1
                
                # Store in score
                score.player_pairs[pair_key] = pair
        
        # Find best and weakest pairs
        if score.player_pairs:
            best = max(score.player_pairs.items(), key=lambda x: x[1].overall_synergy)
            weakest = min(score.player_pairs.items(), key=lambda x: x[1].overall_synergy)
            score.best_pair = best[0]
            score.weakest_pair = weakest[0]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # UTILITIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _determine_level(self, score: float) -> ChemistryLevel:
        """Determine chemistry level from score"""
        if score >= 90:
            return ChemistryLevel.EXCEPTIONAL
        elif score >= 75:
            return ChemistryLevel.EXCELLENT
        elif score >= 60:
            return ChemistryLevel.GOOD
        elif score >= 40:
            return ChemistryLevel.AVERAGE
        elif score >= 25:
            return ChemistryLevel.DEVELOPING
        elif score >= 10:
            return ChemistryLevel.POOR
        return ChemistryLevel.TOXIC
    
    def get_chemistry_trend(self, team_id: str) -> List[Dict[str, Any]]:
        """Get chemistry trend over time"""
        score = self.team_scores.get(team_id)
        if not score:
            return []
        return list(score.chemistry_trend)
    
    def get_best_pairs(self, team_id: str, limit: int = 3) -> List[Dict[str, Any]]:
        """Get best player pairs"""
        score = self.team_scores.get(team_id)
        if not score or not score.player_pairs:
            return []
        
        sorted_pairs = sorted(
            score.player_pairs.items(),
            key=lambda x: x[1].overall_synergy,
            reverse=True,
        )[:limit]
        
        return [
            {
                "players": [pair.player_a_name, pair.player_b_name],
                "synergy": round(pair.overall_synergy, 1),
                "shared_kills": pair.shared_kills,
                "revives_between": pair.revives_between,
            }
            for _, pair in sorted_pairs
        ]
    
    def get_chemistry_summary(self, team_id: str) -> Optional[Dict[str, Any]]:
        """Get chemistry summary for API"""
        score = self.team_scores.get(team_id)
        if not score:
            return None
        
        return {
            "team_name": score.team_name,
            "overall_score": round(score.overall_score, 1),
            "level": score.chemistry_level.value,
            "categories": {
                "combat": round(score.combat_synergy, 1),
                "rotation": round(score.rotation_synergy, 1),
                "utility": round(score.utility_synergy, 1),
                "revive": round(score.revive_synergy, 1),
                "positioning": round(score.positioning_synergy, 1),
                "target_focus": round(score.target_focus_synergy, 1),
            },
            "best_pairs": self.get_best_pairs(team_id),
            "matches_analyzed": score.matches_analyzed,
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get analyzer statistics"""
        return {
            "teams_analyzed": len(self.team_scores),
            "teams": {
                tid: {
                    "name": s.team_name,
                    "score": round(s.overall_score, 1),
                    "level": s.chemistry_level.value,
                }
                for tid, s in self.team_scores.items()
            },
        }
    
    def reset(self) -> None:
        """Reset analyzer"""
        self.team_scores.clear()
        self.pair_tracking.clear()
        self.recent_events.clear()
        self._log.info("Team chemistry analyzer reset")