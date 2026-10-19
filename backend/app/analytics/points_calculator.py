# backend/app/analytics/points_calculator.py
"""
AEGIS AI - Points Calculator
Real-time tournament points calculation with placement points,
kill points, bonus points, and tiebreaker logic.
"""

from typing import Dict, List, Mapping, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.analytics.points")

# ── tournament-config wiring (Phase 13 / W1) ──────────────────────────
# Documented defaults: the Krafton BMSD format — chicken dinner = 10
# placement points, no other placement points, 1 point per finish.
# Used whenever no active tournament exists in the database.
DEFAULT_POINTS_PER_KILL: int = 1
DEFAULT_CHICKEN_POINTS: int = 10

# The ``tournaments.placement_points`` JSONB schema this loader reads:
# string keys "1".."16" mapping to integer points, e.g. {"1": 10}.
# Missing placements score 0.
PLACEMENT_FIELD_BY_RANK = {
    1: "first", 2: "second", 3: "third", 4: "fourth",
    5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth",
    9: "ninth", 10: "tenth", 11: "eleventh", 12: "twelfth",
    13: "thirteenth", 14: "fourteenth", 15: "fifteenth", 16: "sixteenth",
}


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

    @classmethod
    def from_placement_map(cls, mapping: Optional[Mapping[Any, Any]]) -> "PlacementPoints":
        """Build a placement table from the ``tournaments.placement_points``
        JSONB schema (``{"1": 10, ...}``). Missing placements score 0 — the
        map is authoritative, not merged with the dataclass defaults."""
        pp = cls()
        source = mapping or {}
        for rank in range(1, 17):
            field_name = PLACEMENT_FIELD_BY_RANK[rank]
            raw = source.get(str(rank), source.get(rank, 0))
            try:
                setattr(pp, field_name, max(0, int(raw)))
            except (TypeError, ValueError):
                setattr(pp, field_name, 0)
        return pp


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
        # Points configuration — starts on the documented Krafton BMSD
        # defaults (chicken=10, 1 point per finish); configure_from_tournament
        # / configure_from_active_tournament replace them when a tournament
        # config exists.
        self.points_per_kill: int = DEFAULT_POINTS_PER_KILL
        self.placement_points = PlacementPoints.from_placement_map(
            {"1": DEFAULT_CHICKEN_POINTS}
        )
        # Where the current config came from: "defaults" or "tournament:<id>".
        self.config_source: str = "defaults (Krafton BMSD)"
        
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
    # TOURNAMENT-CONFIG WIRING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def configure_from_tournament(self, tournament: Any) -> None:
        """Apply points config from a tournament.

        Accepts the ``Tournament`` SQLAlchemy model, a plain dict with the
        same fields, or ``None``. When ``None`` (no active tournament) the
        documented Krafton BMSD defaults are applied (chicken=10,
        per-finish=1) and a warning is logged. Never raises.
        """
        try:
            if tournament is None:
                self._apply_defaults("no active tournament found")
                return
            data = (
                tournament
                if isinstance(tournament, dict)
                else {c.name: getattr(tournament, c.name, None) for c in tournament.__table__.columns}
            )
            ppk = data.get("points_per_kill")
            self.points_per_kill = int(ppk) if ppk is not None else DEFAULT_POINTS_PER_KILL
            self.placement_points = PlacementPoints.from_placement_map(
                data.get("placement_points") or {}
            )
            self._apply_bonus_overrides(data.get("rules") or {})
            tid = data.get("id") or data.get("slug") or "?"
            self.config_source = f"tournament:{tid}"
            self._log.info(
                "points config loaded from tournament",
                tournament=tid,
                points_per_kill=self.points_per_kill,
            )
        except Exception as exc:
            self._log.warning("failed to apply tournament points config", error=str(exc))
            self._apply_defaults("config parse failed")

    def _apply_defaults(self, reason: str) -> None:
        """Fall back to the documented Krafton BMSD defaults."""
        self.points_per_kill = DEFAULT_POINTS_PER_KILL
        self.placement_points = PlacementPoints.from_placement_map(
            {"1": DEFAULT_CHICKEN_POINTS}
        )
        self.config_source = "defaults (Krafton BMSD)"
        self._log.warning(
            "no active tournament — using Krafton BMSD defaults "
            "(chicken=10 placement points, 1 point per finish)",
            reason=reason,
        )

    def _apply_bonus_overrides(self, rules: Mapping[Any, Any]) -> None:
        """Apply ``rules.scoring.bonus_rules`` point overrides, if present."""
        if not isinstance(rules, Mapping):
            return
        overrides = (rules.get("scoring") or {})
        if not isinstance(overrides, Mapping):
            return
        bonus = overrides.get("bonus_rules") or {}
        if not isinstance(bonus, Mapping):
            return
        for name, rule in bonus.items():
            if name in self.bonus_rules and isinstance(rule, Mapping) and "points" in rule:
                try:
                    self.bonus_rules[name]["points"] = max(0, int(rule["points"]))
                except (TypeError, ValueError):
                    continue

    async def configure_from_active_tournament(self, session: Any) -> bool:
        """Load the active tournament from the DB and apply its points config.

        Returns True when a tournament was found and applied, False when the
        defaults were used instead. Never raises — a dead or missing database
        degrades to the documented defaults with a logged warning.
        """
        try:
            # Local import: keeps analytics importable without the API layer.
            from app.api.v1.tournament.service import TournamentService

            svc = TournamentService(session)
            active = await svc.get_active_tournament()
            self.configure_from_tournament(
                active.model_dump() if active is not None else None
            )
            return active is not None
        except Exception as exc:
            self._log.warning(
                "active-tournament lookup failed — using defaults", error=str(exc)
            )
            self._apply_defaults("active-tournament lookup failed")
            return False
    
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
            "config_source": self.config_source,
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