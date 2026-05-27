# backend/app/analytics/points_calculator.py
"""
AEGIS AI - Points Calculator
Real-time tournament points calculation with placement points,
kill points, bonus points, and tiebreaker logic.
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.analytics.points")


class TiebreakerMethod(str, Enum):
    """Tiebreaker methods"""
    TOTAL_KILLS = "total_kills"
    TOTAL_DAMAGE = "total_damage"
    HIGHEST_PLACEMENT = "highest_placement"
    HEAD_TO_HEAD = "head_to_head"
    LAST_MATCH_PERFORMANCE = "last_match"


@dataclass
class PlacementPoints:
    """Points awarded for each placement"""
    first: int = 15
    second: int = 12
    third: int = 10
    fourth: int = 8
    fifth: int = 6
    sixth: int = 5
    seventh: int = 4
    eighth: int = 3
    ninth: int = 2
    tenth: int = 2
    eleventh: int = 1
    twelfth: int = 1
    thirteenth: int = 1
    fourteenth: int = 1
    fifteenth: int = 1
    sixteenth: int = 1
    
    def get_points(self, placement: int) -> int:
        """Get points for a placement"""
        mapping = {
            1: self.first, 2: self.second, 3: self.third, 4: self.fourth,
            5: self.fifth, 6: self.sixth, 7: self.seventh, 8: self.eighth,
            9: self.ninth, 10: self.tenth, 11: self.eleventh, 12: self.twelfth,
            13: self.thirteenth, 14: self.fourteenth, 15: self.fifteenth, 16: self.sixteenth,
        }
        return mapping.get(placement, 0)


@dataclass
class TournamentPoints:
    """Complete tournament points for a team"""
    team_id: str = ""
    team_name: str = ""
    
    # Match points
    match_points: Dict[str, Dict[str, int]] = field(default_factory=dict)
    
    # Totals
    total_placement_points: int = 0
    total_kill_points: int = 0
    total_bonus_points: int = 0
    grand_total: int = 0
    
    # Stats
    total_kills: int = 0
    total_damage: int = 0
    matches_played: int = 0
    wwcd_count: int = 0  # Winner Winner Chicken Dinner
    top3_count: int = 0
    top5_count: int = 0
    
    # Rankings
    current_rank: int = 0
    previous_rank: int = 0
    rank_change: int = 0
    
    # Tiebreakers
    tiebreaker_kills: int = 0
    tiebreaker_damage: int = 0


class PointsCalculator:
    """
    Real-time tournament points calculator.
    
    Features:
    - Configurable placement points
    - Per-kill points calculation
    - Bonus point rules
    - Multiple tiebreaker methods
    - Live rank tracking
    - Points breakdown per match
    """
    
    def __init__(self):
        # Points configuration
        self.points_per_kill: int = 1
        self.placement_points = PlacementPoints()
        
        # Bonus points rules
        self.bonus_rules: Dict[str, Dict[str, Any]] = {
            "first_blood": {"points": 2, "description": "First kill of the match"},
            "most_kills": {"points": 3, "description": "Most kills in a match"},
            "squad_wipe": {"points": 2, "description": "Eliminate entire enemy squad"},
            "flawless_victory": {"points": 5, "description": "Win without losing any player"},
        }
        
        # Tiebreaker configuration
        self.tiebreaker_method: TiebreakerMethod = TiebreakerMethod.TOTAL_KILLS
        self.tiebreaker_chain: List[TiebreakerMethod] = [
            TiebreakerMethod.TOTAL_KILLS,
            TiebreakerMethod.TOTAL_DAMAGE,
            TiebreakerMethod.HIGHEST_PLACEMENT,
        ]
        
        # Team points storage
        self.team_points: Dict[str, TournamentPoints] = {}
        
        # Match results storage
        self.match_results: Dict[str, Dict[str, Any]] = {}
        
        self._log = logger.bind(component="points_calculator")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # POINTS CALCULATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def calculate_match_points(
        self,
        match_id: str,
        team_results: List[Dict[str, Any]],
    ) -> Dict[str, Dict[str, int]]:
        """
        Calculate points for a match.
        
        Args:
            match_id: Match identifier
            team_results: List of team results with placement, kills, etc.
            
        Returns:
            Dict of team_id -> points breakdown
        """
        
        match_points = {}
        most_kills = 0
        most_kills_team = ""
        
        for team in team_results:
            team_id = team.get("team_id", "")
            team_name = team.get("team_name", "")
            placement = team.get("placement", 16)
            kills = team.get("kills", 0)
            damage = team.get("damage", 0)
            is_winner = placement == 1
            flawless = is_winner and team.get("players_lost", 0) == 0
            squad_wipes = team.get("squad_wipes", 0)
            first_blood = team.get("first_blood", False)
            
            # Placement points
            place_pts = self.placement_points.get_points(placement)
            
            # Kill points
            kill_pts = kills * self.points_per_kill
            
            # Bonus points
            bonus_pts = 0
            
            if first_blood:
                bonus_pts += self.bonus_rules.get("first_blood", {}).get("points", 0)
            
            if squad_wipes > 0:
                bonus_pts += squad_wipes * self.bonus_rules.get("squad_wipe", {}).get("points", 0)
            
            if flawless:
                bonus_pts += self.bonus_rules.get("flawless_victory", {}).get("points", 0)
            
            total = place_pts + kill_pts + bonus_pts
            
            match_points[team_id] = {
                "placement_points": place_pts,
                "kill_points": kill_pts,
                "bonus_points": bonus_pts,
                "total": total,
                "placement": placement,
                "kills": kills,
                "damage": damage,
            }
            
            # Track most kills
            if kills > most_kills:
                most_kills = kills
                most_kills_team = team_id
            
            # Update team totals
            self._update_team_totals(
                team_id, team_name, placement, kills, damage,
                place_pts, kill_pts, bonus_pts, is_winner,
            )
        
        # Award most kills bonus
        if most_kills_team and most_kills > 0:
            bonus = self.bonus_rules.get("most_kills", {}).get("points", 0)
            if most_kills_team in match_points:
                match_points[most_kills_team]["bonus_points"] += bonus
                match_points[most_kills_team]["total"] += bonus
        
        # Store match results
        self.match_results[match_id] = match_points
        
        return match_points
    
    def _update_team_totals(
        self,
        team_id: str,
        team_name: str,
        placement: int,
        kills: int,
        damage: int,
        place_pts: int,
        kill_pts: int,
        bonus_pts: int,
        is_winner: bool,
    ) -> None:
        """Update team total points"""
        
        if team_id not in self.team_points:
            self.team_points[team_id] = TournamentPoints(
                team_id=team_id,
                team_name=team_name,
            )
        
        tp = self.team_points[team_id]
        tp.previous_rank = tp.current_rank
        
        tp.total_placement_points += place_pts
        tp.total_kill_points += kill_pts
        tp.total_bonus_points += bonus_pts
        tp.grand_total = tp.total_placement_points + tp.total_kill_points + tp.total_bonus_points
        
        tp.total_kills += kills
        tp.total_damage += damage
        tp.matches_played += 1
        
        if is_winner:
            tp.wwcd_count += 1
        
        if placement <= 3:
            tp.top3_count += 1
        
        if placement <= 5:
            tp.top5_count += 1
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # RANKINGS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_rankings(self) -> List[TournamentPoints]:
        """
        Get current tournament rankings with tiebreakers applied.
        
        Returns:
            Sorted list of TournamentPoints
        """
        
        teams = list(self.team_points.values())
        
        # Sort by grand total (descending)
        teams.sort(key=lambda t: t.grand_total, reverse=True)
        
        # Apply tiebreakers
        self._apply_tiebreakers(teams)
        
        # Assign ranks
        for i, team in enumerate(teams):
            team.current_rank = i + 1
            team.rank_change = team.previous_rank - team.current_rank
        
        return teams
    
    def _apply_tiebreakers(self, teams: List[TournamentPoints]) -> None:
        """Apply tiebreaker rules to teams with equal points"""
        
        i = 0
        while i < len(teams) - 1:
            # Find group with same points
            j = i + 1
            while j < len(teams) and teams[j].grand_total == teams[i].grand_total:
                j += 1
            
            if j - i > 1:
                # Tiebreaker needed
                tied_group = teams[i:j]
                
                for method in self.tiebreaker_chain:
                    if len(set(self._get_tiebreaker_value(t, method) for t in tied_group)) > 1:
                        tied_group.sort(
                            key=lambda t: self._get_tiebreaker_value(t, method),
                            reverse=True,
                        )
                        break
                
                teams[i:j] = tied_group
            
            i = j
    
    def _get_tiebreaker_value(self, team: TournamentPoints, method: TiebreakerMethod) -> int:
        """Get tiebreaker value for a team"""
        if method == TiebreakerMethod.TOTAL_KILLS:
            return team.total_kills
        elif method == TiebreakerMethod.TOTAL_DAMAGE:
            return team.total_damage
        elif method == TiebreakerMethod.HIGHEST_PLACEMENT:
            return team.wwcd_count * 100 + team.top3_count * 10 + team.top5_count
        return 0
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_team_points(self, team_id: str) -> Optional[TournamentPoints]:
        """Get points for a specific team"""
        return self.team_points.get(team_id)
    
    def get_match_points(self, match_id: str) -> Optional[Dict[str, Any]]:
        """Get points breakdown for a match"""
        return self.match_results.get(match_id)
    
    def get_top_teams(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Get top teams summary"""
        rankings = self.get_rankings()[:limit]
        
        return [
            {
                "rank": team.current_rank,
                "team_name": team.team_name,
                "total_points": team.grand_total,
                "placement_points": team.total_placement_points,
                "kill_points": team.total_kill_points,
                "total_kills": team.total_kills,
                "matches_played": team.matches_played,
                "wwcd": team.wwcd_count,
                "rank_change": team.rank_change,
            }
            for team in rankings
        ]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get points calculator statistics"""
        return {
            "teams_tracked": len(self.team_points),
            "matches_calculated": len(self.match_results),
            "points_per_kill": self.points_per_kill,
            "placement_points": {
                str(p): pts for p, pts in [
                    (1, self.placement_points.first),
                    (2, self.placement_points.second),
                    (3, self.placement_points.third),
                    (5, self.placement_points.fifth),
                    (10, self.placement_points.tenth),
                ]
            },
            "top_teams": self.get_top_teams(5),
        }
    
    def reset(self) -> None:
        """Reset calculator"""
        self.team_points.clear()
        self.match_results.clear()
        self._log.info("Points calculator reset")