# backend/app/analytics/leaderboard_generator.py
"""
AEGIS AI - Leaderboard Generator
Generates live leaderboards with rankings, points,
kills, damage, and performance indicators.
"""

import time
from typing import Dict, List, Any
from dataclasses import dataclass, field
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.analytics.leaderboard")


class LeaderboardType(str, Enum):
    """Types of leaderboards"""
    OVERALL = "overall"
    KILLS = "kills"
    DAMAGE = "damage"
    SURVIVAL = "survival"
    MVP = "mvp"
    PER_MATCH = "per_match"


@dataclass
class LeaderboardEntry:
    """A single leaderboard entry"""
    rank: int
    team_id: str
    team_name: str
    
    # Points
    total_points: int = 0
    placement_points: int = 0
    kill_points: int = 0
    
    # Stats
    total_kills: int = 0
    total_damage: int = 0
    matches_played: int = 0
    wwcd_count: int = 0
    
    # Movement
    rank_change: int = 0  # Positive = moved up
    previous_rank: int = 0
    
    # Form
    recent_form: str = ""  # "hot", "stable", "cold"
    streak: int = 0
    
    # Highlight
    is_highlighted: bool = False
    highlight_reason: str = ""


@dataclass
class Leaderboard:
    """Complete leaderboard"""
    leaderboard_id: str
    leaderboard_type: LeaderboardType = LeaderboardType.OVERALL
    title: str = "Tournament Standings"
    
    # Entries
    entries: List[LeaderboardEntry] = field(default_factory=list)
    
    # Metadata
    generated_at: float = field(default_factory=time.time)
    match_count: int = 0
    total_kills: int = 0
    total_damage: int = 0
    
    # Sorting
    sort_by: str = "total_points"
    sort_descending: bool = True


class LeaderboardGenerator:
    """
    Generates live leaderboards for broadcast and display.
    
    Features:
    - Multiple leaderboard types (overall, kills, damage, etc.)
    - Rank movement indicators
    - Form streaks
    - Highlight flags for notable changes
    - Custom sorting
    - Real-time updates
    """
    
    def __init__(self):
        self.leaderboards: Dict[str, Leaderboard] = {}
        self.previous_rankings: Dict[str, int] = {}
        self._log = logger.bind(component="leaderboard_generator")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # LEADERBOARD GENERATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def generate_overall(
        self,
        team_points: Dict[str, Any],
        sort_by: str = "total_points",
    ) -> Leaderboard:
        """Generate overall tournament leaderboard"""
        
        leaderboard = Leaderboard(
            leaderboard_id=f"overall_{int(time.time())}",
            leaderboard_type=LeaderboardType.OVERALL,
            title="Tournament Standings",
            sort_by=sort_by,
        )
        
        entries = []
        for team_id, tp in team_points.items():
            prev_rank = self.previous_rankings.get(team_id, 0)
            rank_change = prev_rank - 0 if prev_rank > 0 else 0  # Will be updated after sorting
            
            entry = LeaderboardEntry(
                rank=0,  # Will be set after sorting
                team_id=team_id,
                team_name=tp.get("team_name", ""),
                total_points=tp.get("grand_total", 0),
                placement_points=tp.get("total_placement_points", 0),
                kill_points=tp.get("total_kill_points", 0),
                total_kills=tp.get("total_kills", 0),
                total_damage=tp.get("total_damage", 0),
                matches_played=tp.get("matches_played", 0),
                wwcd_count=tp.get("wwcd_count", 0),
                previous_rank=prev_rank,
            )
            entries.append(entry)
        
        # Sort
        entries.sort(key=lambda e: getattr(e, sort_by, 0), reverse=True)
        
        # Assign ranks
        for i, entry in enumerate(entries):
            entry.rank = i + 1
            entry.rank_change = entry.previous_rank - entry.rank
        
        # Highlight notable changes
        for entry in entries:
            if entry.rank_change >= 3:
                entry.is_highlighted = True
                entry.highlight_reason = f"Moved up {entry.rank_change} positions!"
            elif entry.rank_change <= -3:
                entry.is_highlighted = True
                entry.highlight_reason = f"Dropped {abs(entry.rank_change)} positions"
        
        leaderboard.entries = entries
        leaderboard.match_count = entries[0].matches_played if entries else 0
        
        # Store previous rankings
        for entry in entries:
            self.previous_rankings[entry.team_id] = entry.rank
        
        self.leaderboards[leaderboard.leaderboard_id] = leaderboard
        
        return leaderboard
    
    def generate_kills_leaderboard(
        self,
        team_points: Dict[str, Any],
    ) -> Leaderboard:
        """Generate kills-focused leaderboard"""
        
        leaderboard = Leaderboard(
            leaderboard_id=f"kills_{int(time.time())}",
            leaderboard_type=LeaderboardType.KILLS,
            title="Kill Leaders",
            sort_by="total_kills",
        )
        
        entries = []
        for team_id, tp in team_points.items():
            entry = LeaderboardEntry(
                rank=0,
                team_id=team_id,
                team_name=tp.get("team_name", ""),
                total_kills=tp.get("total_kills", 0),
                total_damage=tp.get("total_damage", 0),
                total_points=tp.get("grand_total", 0),
                matches_played=tp.get("matches_played", 0),
            )
            entries.append(entry)
        
        entries.sort(key=lambda e: e.total_kills, reverse=True)
        
        for i, entry in enumerate(entries):
            entry.rank = i + 1
        
        leaderboard.entries = entries
        leaderboard.total_kills = sum(e.total_kills for e in entries)
        
        return leaderboard
    
    def generate_damage_leaderboard(
        self,
        team_points: Dict[str, Any],
    ) -> Leaderboard:
        """Generate damage-focused leaderboard"""
        
        leaderboard = Leaderboard(
            leaderboard_id=f"damage_{int(time.time())}",
            leaderboard_type=LeaderboardType.DAMAGE,
            title="Damage Leaders",
            sort_by="total_damage",
        )
        
        entries = []
        for team_id, tp in team_points.items():
            entry = LeaderboardEntry(
                rank=0,
                team_id=team_id,
                team_name=tp.get("team_name", ""),
                total_damage=tp.get("total_damage", 0),
                total_kills=tp.get("total_kills", 0),
                total_points=tp.get("grand_total", 0),
            )
            entries.append(entry)
        
        entries.sort(key=lambda e: e.total_damage, reverse=True)
        
        for i, entry in enumerate(entries):
            entry.rank = i + 1
        
        leaderboard.entries = entries
        leaderboard.total_damage = sum(e.total_damage for e in entries)
        
        return leaderboard
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FORMATTED OUTPUT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_top_teams_display(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get formatted top teams for display"""
        
        leaderboard = self.leaderboards.get(
            list(self.leaderboards.keys())[-1] if self.leaderboards else ""
        )
        
        if not leaderboard or not leaderboard.entries:
            return []
        
        return [
            {
                "rank": e.rank,
                "team": e.team_name,
                "points": e.total_points,
                "kills": e.total_kills,
                "damage": e.total_damage,
                "matches": e.matches_played,
                "wwcd": e.wwcd_count,
                "change": e.rank_change,
                "highlighted": e.is_highlighted,
                "highlight_reason": e.highlight_reason,
            }
            for e in leaderboard.entries[:limit]
        ]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get leaderboard generator statistics"""
        return {
            "leaderboards_generated": len(self.leaderboards),
            "latest": {
                "title": lb.title if (lb := list(self.leaderboards.values())[-1] if self.leaderboards else None) else "",
                "entries": len(lb.entries) if (lb := list(self.leaderboards.values())[-1] if self.leaderboards else None) else 0,
            } if self.leaderboards else None,
        }
    
    def reset(self) -> None:
        """Reset generator"""
        self.leaderboards.clear()
        self.previous_rankings.clear()
        self._log.info("Leaderboard generator reset")