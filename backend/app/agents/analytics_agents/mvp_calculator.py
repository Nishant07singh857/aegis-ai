# backend/app/agents/analytics_agents/mvp_calculator.py
"""
AEGIS AI - MVP Calculator — SINGLE canonical MVP producer (Phase 21, W2).

Canonical PRD formula (the ONLY MVP definition in the codebase):

    mvp_score = kills*5 + damage*0.05 + revives*3
              + survival_seconds*0.02 + clutches*10 + headshots*2

* :func:`compute_mvp_score` / :func:`compute_mvp_score_from_stats` are the
  single implementation. ``MatchService.record_player_stats`` persists
  their output to ``match_player_stats.mvp_score`` and the player-stats
  API serves it (``rating`` / ``mvp_score``); the standings MVP board
  sorts by it. No other module may define an MVP score.
* Honesty rules: a stat the feed has not delivered contributes 0 and is
  never estimated. In particular damage is taken exactly as recorded
  (agent HP-drop accounting, see ``team_tracker.update_player_hp`` /
  ``record_kill``); a missing/``None`` damage contributes 0 to the score
  while the API still reports the damage itself as ``null`` ("—").
* ``clutches`` means clutches WON. Sources: an explicit ``clutches`` key,
  else ``clutch_wins``. ``clutch_attempts`` alone is NOT a clutch and is
  never counted. Headshots come from ``match_player_stats.headshots``
  (kill-feed / feed payloads); when absent they are 0.
* Category awards (Best Fragger / Support / Clutch / …) are labels only:
  they never feed back into ``mvp_score``.
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.mvp")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CANONICAL MVP FORMULA (PRD) — the single producer
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MVP_WEIGHT_KILLS = 5.0
MVP_WEIGHT_DAMAGE = 0.05
MVP_WEIGHT_REVIVES = 3.0
MVP_WEIGHT_SURVIVAL_PER_SECOND = 0.02
MVP_WEIGHT_CLUTCHES = 10.0
MVP_WEIGHT_HEADSHOTS = 2.0


def _num(value: Any) -> float:
    """Coerce a possibly-missing stat to a number; missing/None → 0."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def compute_mvp_score(
    *,
    kills: Any = 0,
    damage: Any = None,
    revives: Any = 0,
    survival_seconds: Any = 0,
    clutches: Any = 0,
    headshots: Any = 0,
) -> float:
    """Canonical PRD MVP score — the ONLY MVP formula in the codebase.

    mvp_score = kills*5 + damage*0.05 + revives*3
              + survival_seconds*0.02 + clutches*10 + headshots*2

    Any argument may be ``None`` (feed has not delivered it) and then
    contributes 0 — values are never estimated or invented.
    """
    return (
        _num(kills) * MVP_WEIGHT_KILLS
        + _num(damage) * MVP_WEIGHT_DAMAGE
        + _num(revives) * MVP_WEIGHT_REVIVES
        + _num(survival_seconds) * MVP_WEIGHT_SURVIVAL_PER_SECOND
        + _num(clutches) * MVP_WEIGHT_CLUTCHES
        + _num(headshots) * MVP_WEIGHT_HEADSHOTS
    )


def extract_clutches(stats: Dict[str, Any]) -> int:
    """Clutches WON from a stat dict: ``clutches`` else ``clutch_wins`` else 0.

    ``clutch_attempts`` is deliberately NOT used: an attempt is not a
    clutch. When no won-clutch count exists the honest value is 0.
    """
    if stats.get("clutches") is not None:
        return int(_num(stats.get("clutches")))
    if stats.get("clutch_wins") is not None:
        return int(_num(stats.get("clutch_wins")))
    return 0


def compute_mvp_score_from_stats(stats: Dict[str, Any]) -> float:
    """Canonical score from a loose stat dict (feed / DB / agent shapes).

    Accepted aliases: kills|finishes, damage|damage_dealt,
    revives|revives_performed, survival_seconds|survived_time_seconds|
    survival_time, clutches|clutch_wins, headshots.
    """
    damage = stats.get("damage", stats.get("damage_dealt"))
    revives = stats.get("revives", stats.get("revives_performed"))
    survival = stats.get(
        "survival_seconds",
        stats.get("survived_time_seconds", stats.get("survival_time")),
    )
    kills = stats.get("kills", stats.get("finishes"))
    return compute_mvp_score(
        kills=kills,
        damage=damage,
        revives=revives,
        survival_seconds=survival,
        clutches=extract_clutches(stats),
        headshots=stats.get("headshots"),
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MVPCategory(str, Enum):
    """MVP award categories"""
    OVERALL_MVP = "overall_mvp"           # Best overall player
    BEST_FRAGGER = "best_fragger"         # Most kills/damage
    BEST_SUPPORT = "best_support"         # Healing, revives, assists
    BEST_IGL = "best_igl"                # Best strategic play
    BEST_SNIPER = "best_sniper"          # Long-range specialist
    BEST_CLUTCH = "best_clutch"          # Clutch performance
    MOST_IMPROVED = "most_improved"       # Biggest improvement
    FAN_FAVORITE = "fan_favorite"        # Fan-voted
    RISING_STAR = "rising_star"          # New talent
    BEST_UTILITY = "best_utility"        # Best grenade/smoke usage

class PerformanceWeight(str, Enum):
    """Weight categories for MVP calculation"""
    KILLS = "kills"
    DAMAGE = "damage"
    SURVIVAL = "survival"
    UTILITY = "utility"
    CLUTCH = "clutch"
    TEAMWORK = "teamwork"
    CONSISTENCY = "consistency"
    IMPACT = "impact"

@dataclass
class MVPResult:
    """Complete MVP calculation result.

    ``overall_score`` IS the canonical PRD ``mvp_score`` (see
    :func:`compute_mvp_score`) — an unbounded points total, NOT a
    0–100 normalisation. The ``*_score`` breakdown fields hold the
    canonical per-term contributions so they always sum to
    ``overall_score`` (utility/impact/consistency carry no PRD term
    and stay 0).
    """
    player_id: str
    player_name: str
    team_id: str
    team_name: str

    # Canonical score + per-term contributions (sum == overall_score)
    overall_score: float = 0.0
    combat_score: float = 0.0
    survival_score: float = 0.0
    utility_score: float = 0.0
    teamwork_score: float = 0.0
    clutch_score: float = 0.0
    impact_score: float = 0.0

    # Raw stats (damage is None when the feed never delivered it)
    kills: int = 0
    knocks: int = 0
    assists: int = 0
    damage: Optional[int] = None
    headshots: int = 0
    revives: int = 0
    healing: int = 0
    survival_time: float = 0.0
    distance_traveled: float = 0.0
    smokes_used: int = 0
    grenades_used: int = 0
    clutch_attempts: int = 0
    clutch_wins: int = 0
    clutches: int = 0

    @property
    def mvp_score(self) -> float:
        """Alias for ``overall_score`` — the canonical MVP score."""
        return self.overall_score
    
    # Rankings
    rank: int = 0
    category_ranks: Dict[MVPCategory, int] = field(default_factory=dict)
    
    # Awards
    awards: List[MVPCategory] = field(default_factory=list)
    
    # Match context
    match_id: str = ""
    match_placement: int = 16
    zone_phase_at_death: int = 0
    
    # Historical
    is_personal_best: bool = False
    compared_to_average: float = 0.0  # % above/below average


class MVPCalculator(BaseAgent):
    """
    Canonical MVP scoring engine (single producer).

    The score is EXACTLY the PRD formula implemented by
    :func:`compute_mvp_score` — no weights, no placement multipliers,
    no 0–100 normalisation. Category awards below are descriptive
    labels derived from raw stats; they never alter the score.
    """

    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)

        # Historical data
        self.player_averages: Dict[str, Dict[str, float]] = {}
        self.tournament_mvps: List[MVPResult] = []

        self._log = logger.bind(component="mvp_calculator")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MVP CALCULATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def calculate_mvp(
        self,
        players: List[Dict[str, Any]],
        match_context: Dict[str, Any],
    ) -> List[MVPResult]:
        """
        Calculate MVP scores for all players in a match.
        
        Args:
            players: List of player stats dictionaries
            match_context: Match-level context
            
        Returns:
            List of MVPResult sorted by overall_score descending
        """
        
        results = []
        
        for player in players:
            result = self._calculate_player_score(player, match_context)
            results.append(result)
        
        # Sort by overall score
        results.sort(key=lambda r: r.overall_score, reverse=True)
        
        # Assign ranks
        for i, result in enumerate(results):
            result.rank = i + 1
        
        # Determine category winners
        self._assign_category_awards(results)
        
        # Store top performers
        if results:
            self.tournament_mvps.append(results[0])
        
        self._log.info(
            f"MVP Calculated: {results[0].player_name if results else 'N/A'} "
            f"({results[0].overall_score:.1f})" if results else "No players"
        )
        
        return results
    
    def _calculate_player_score(
        self,
        player: Dict[str, Any],
        context: Dict[str, Any],
    ) -> MVPResult:
        """Calculate the canonical MVP score for a single player.

        Delegates to :func:`compute_mvp_score_from_stats` — the one
        formula. Missing stats contribute 0 (never estimated); in
        particular ``damage`` stays ``None`` on the result when the
        feed never delivered it, so callers can render "—".
        """

        # Extract stats with the canonical alias handling.
        kills = int(_num(player.get("kills", player.get("finishes"))))
        knocks = int(_num(player.get("knocks")))
        assists = int(_num(player.get("assists")))
        raw_damage = player.get("damage", player.get("damage_dealt"))
        damage: Optional[int] = (
            int(_num(raw_damage)) if raw_damage is not None else None
        )
        headshots = int(_num(player.get("headshots")))
        revives = int(
            _num(player.get("revives", player.get("revives_performed")))
        )
        healing = int(_num(player.get("healing")))
        survival_time = _num(
            player.get(
                "survival_seconds",
                player.get(
                    "survived_time_seconds", player.get("survival_time")
                ),
            )
        )
        smokes = int(_num(player.get("smokes", player.get("smokes_used"))))
        grenades = int(
            _num(player.get("grenades", player.get("grenades_used")))
        )
        clutch_attempts = int(_num(player.get("clutch_attempts")))
        clutch_wins = int(_num(player.get("clutch_wins")))
        clutches = extract_clutches(player)

        placement = context.get("team_placement", 16)

        # Canonical per-term contributions (they sum to the score).
        combat_score = (
            kills * MVP_WEIGHT_KILLS
            + _num(damage) * MVP_WEIGHT_DAMAGE
            + headshots * MVP_WEIGHT_HEADSHOTS
        )
        survival_score = survival_time * MVP_WEIGHT_SURVIVAL_PER_SECOND
        teamwork_score = revives * MVP_WEIGHT_REVIVES
        clutch_score = clutches * MVP_WEIGHT_CLUTCHES
        overall_score = compute_mvp_score(
            kills=kills,
            damage=damage,
            revives=revives,
            survival_seconds=survival_time,
            clutches=clutches,
            headshots=headshots,
        )

        result = MVPResult(
            player_id=player.get("player_id", ""),
            player_name=player.get("player_name", "Unknown"),
            team_id=player.get("team_id", ""),
            team_name=player.get("team_name", "Unknown"),
            overall_score=round(overall_score, 2),
            combat_score=round(combat_score, 2),
            survival_score=round(survival_score, 2),
            utility_score=0.0,
            teamwork_score=round(teamwork_score, 2),
            clutch_score=round(clutch_score, 2),
            impact_score=0.0,
            kills=kills,
            knocks=knocks,
            assists=assists,
            damage=damage,
            headshots=headshots,
            revives=revives,
            healing=healing,
            survival_time=survival_time,
            smokes_used=smokes,
            grenades_used=grenades,
            clutch_attempts=clutch_attempts,
            clutch_wins=clutch_wins,
            clutches=clutches,
            match_placement=placement,
            match_id=context.get("match_id", ""),
        )

        return result
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CATEGORY AWARDS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _assign_category_awards(self, results: List[MVPResult]) -> None:
        """Assign descriptive category awards from RAW stats only.

        Awards never influence ``overall_score`` (the canonical MVP
        score). They are labels the broadcast layer may display.
        """

        if not results:
            return

        # Best Fragger (most kills, then most damage)
        best_fragger = max(
            results, key=lambda r: (r.kills, r.damage or 0)
        )
        best_fragger.awards.append(MVPCategory.BEST_FRAGGER)
        best_fragger.category_ranks[MVPCategory.BEST_FRAGGER] = 1

        # Best Support (most revives)
        best_support = max(results, key=lambda r: r.revives)
        if best_support.revives > 0:
            best_support.awards.append(MVPCategory.BEST_SUPPORT)
            best_support.category_ranks[MVPCategory.BEST_SUPPORT] = 1

        # Best Clutch (most clutches won)
        best_clutch = max(results, key=lambda r: r.clutches)
        if best_clutch.clutches > 0:
            best_clutch.awards.append(MVPCategory.BEST_CLUTCH)
            best_clutch.category_ranks[MVPCategory.BEST_CLUTCH] = 1

        # Best Utility (most utility thrown) — informational only
        best_utility = max(
            results, key=lambda r: r.smokes_used + r.grenades_used
        )
        if best_utility.smokes_used + best_utility.grenades_used > 0:
            best_utility.awards.append(MVPCategory.BEST_UTILITY)

        # Overall MVP (highest canonical score)
        if results:
            results[0].awards.append(MVPCategory.OVERALL_MVP)
            results[0].category_ranks[MVPCategory.OVERALL_MVP] = 1
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HISTORICAL TRACKING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def update_player_averages(self, player_id: str, stats: Dict[str, float]) -> None:
        """Update player historical averages"""
        if player_id not in self.player_averages:
            self.player_averages[player_id] = {}
        
        for key, value in stats.items():
            if key in self.player_averages[player_id]:
                # Exponential moving average
                self.player_averages[player_id][key] = (
                    self.player_averages[player_id][key] * 0.7 + value * 0.3
                )
            else:
                self.player_averages[player_id][key] = value
    
    def get_player_average(self, player_id: str) -> Dict[str, float]:
        """Get player historical averages"""
        return self.player_averages.get(player_id, {})
    
    def is_personal_best(self, player_id: str, score: float) -> bool:
        """Check if this is a personal best score"""
        avg = self.player_averages.get(player_id, {})
        avg_score = avg.get("avg_mvp_score", 0)
        return score > avg_score * 1.3  # 30% above average
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_top_players(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top MVP performers"""
        sorted_mvps = sorted(
            self.tournament_mvps,
            key=lambda m: m.overall_score,
            reverse=True,
        )
        
        return [
            {
                "rank": i + 1,
                "player_name": m.player_name,
                "team_name": m.team_name,
                "score": m.overall_score,
                "mvp_score": m.overall_score,
                "kills": m.kills,
                "damage": m.damage,
                "awards": [a.value for a in m.awards],
            }
            for i, m in enumerate(sorted_mvps[:limit])
        ]

    def get_match_mvp(self, match_id: str) -> Optional[Dict[str, Any]]:
        """Get MVP for a specific match"""
        for mvp in self.tournament_mvps:
            if mvp.match_id == match_id:
                return {
                    "player_name": mvp.player_name,
                    "team_name": mvp.team_name,
                    "score": mvp.overall_score,
                    "mvp_score": mvp.overall_score,
                    "kills": mvp.kills,
                    "damage": mvp.damage,
                    "awards": [a.value for a in mvp.awards],
                }
        return None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get MVP calculator statistics"""
        return {
            "total_mvps_calculated": len(self.tournament_mvps),
            "players_tracked": len(self.player_averages),
            "top_players": self.get_top_players(5),
        }
    
    def reset(self) -> None:
        """Reset calculator"""
        self.tournament_mvps.clear()
        self._log.info("MVP calculator reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("kill_confirmed")
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
