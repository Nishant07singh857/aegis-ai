# backend/app/agents/analytics_agents/performance_tracker.py
"""
AEGIS AI - Performance Tracker
Tracks player and team performance over time,
identifies trends, streaks, and improvement areas.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque, defaultdict
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.performance")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PerformanceTrend(str, Enum):
    """Performance trend direction"""
    IMPROVING = "improving"
    STABLE = "stable"
    DECLINING = "declining"
    INCONSISTENT = "inconsistent"
    PEAKING = "peaking"
    SLUMPING = "slumping"

class StreakType(str, Enum):
    """Types of performance streaks"""
    KILL_STREAK = "kill_streak"
    WIN_STREAK = "win_streak"
    TOP3_STREAK = "top3_streak"
    SURVIVAL_STREAK = "survival_streak"
    DAMAGE_STREAK = "damage_streak"

@dataclass
class PerformanceRecord:
    """Single match performance record"""
    match_id: str
    timestamp: float = field(default_factory=time.time)
    
    # Player stats
    player_id: str = ""
    player_name: str = ""
    team_id: str = ""
    team_name: str = ""
    
    kills: int = 0
    knocks: int = 0
    assists: int = 0
    damage: int = 0
    headshots: int = 0
    revives: int = 0
    healing: int = 0
    survival_time: float = 0.0
    placement: int = 16
    
    # Derived
    mvp_score: float = 0.0
    combat_score: float = 0.0
    support_score: float = 0.0
    
    # Context
    zone_phase_at_death: int = 0
    weapons_used: List[str] = field(default_factory=list)
    opponents_faced: List[str] = field(default_factory=list)

@dataclass
class PerformanceSummary:
    """Aggregated performance summary"""
    player_id: str
    player_name: str
    
    # Totals
    total_matches: int = 0
    total_kills: int = 0
    total_damage: int = 0
    total_wins: int = 0
    
    # Averages
    avg_kills: float = 0.0
    avg_damage: float = 0.0
    avg_placement: float = 0.0
    avg_survival: float = 0.0
    
    # Best performances
    best_kills: int = 0
    best_damage: int = 0
    best_placement: int = 16
    best_mvp_score: float = 0.0
    
    # Trends
    trend: PerformanceTrend = PerformanceTrend.STABLE
    recent_form: float = 0.0  # 0-100
    
    # Streaks
    current_kill_streak: int = 0
    best_kill_streak: int = 0
    matches_with_kills: int = 0
    
    # Consistency
    consistency_score: float = 0.0  # How consistent (0-100)
    ceiling_score: float = 0.0      # Peak potential
    floor_score: float = 0.0        # Worst case
    
    # Improvement
    improvement_rate: float = 0.0    # Per match improvement
    
    # History
    recent_matches: deque = field(default_factory=lambda: deque(maxlen=20))
    all_time_records: Dict[str, Any] = field(default_factory=dict)


class PerformanceTracker(BaseAgent):
    """
    Tracks player and team performance over time.
    
    Features:
    - Match-by-match performance records
    - Rolling averages and trends
    - Streak detection (kill streaks, win streaks)
    - Consistency analysis
    - Improvement tracking
    - Best/worst performance identification
    - Performance predictions
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        # Player records
        self.player_records: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=50)
        )
        
        # Player summaries
        self.player_summaries: Dict[str, PerformanceSummary] = {}
        
        # Team records
        self.team_records: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=50)
        )
        
        # Active streaks
        self.active_streaks: Dict[str, Dict[StreakType, int]] = defaultdict(dict)
        
        # Configuration
        self.recent_window = 10  # Matches for "recent form"
        self.trend_window = 20   # Matches for trend analysis

        # Periodic trend refresh (30s cadence, in-memory, stdlib-only)
        self._last_trend_refresh = 0.0
        
        self._log = logger.bind(component="performance_tracker")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # RECORD MATCH PERFORMANCE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def record_match(
        self,
        player_id: str,
        player_name: str,
        team_id: str,
        team_name: str,
        stats: Dict[str, Any],
        match_id: str = "",
    ) -> PerformanceRecord:
        """Record a player's match performance"""
        
        # Create record
        record = PerformanceRecord(
            match_id=match_id or f"match_{int(time.time())}",
            player_id=player_id,
            player_name=player_name,
            team_id=team_id,
            team_name=team_name,
            kills=stats.get("kills", 0),
            knocks=stats.get("knocks", 0),
            assists=stats.get("assists", 0),
            damage=stats.get("damage", 0),
            headshots=stats.get("headshots", 0),
            revives=stats.get("revives", 0),
            healing=stats.get("healing", 0),
            survival_time=stats.get("survival_time", 0),
            placement=stats.get("placement", 16),
            mvp_score=stats.get("mvp_score", 0),
            zone_phase_at_death=stats.get("zone_phase", 0),
            weapons_used=stats.get("weapons", []),
        )
        
        # Store record
        self.player_records[player_id].append(record)
        
        # Update summary
        self._update_summary(player_id, player_name, record)
        
        # Update streaks
        self._update_streaks(player_id, record)
        
        self._log.debug(
            f"Performance recorded: {player_name}",
            kills=record.kills,
            damage=record.damage,
        )
        
        return record
    
    def _update_summary(
        self,
        player_id: str,
        player_name: str,
        record: PerformanceRecord,
    ) -> None:
        """Update player performance summary"""
        
        if player_id not in self.player_summaries:
            self.player_summaries[player_id] = PerformanceSummary(
                player_id=player_id,
                player_name=player_name,
            )
        
        summary = self.player_summaries[player_id]
        n = summary.total_matches
        
        # Update totals
        summary.total_matches += 1
        summary.total_kills += record.kills
        summary.total_damage += record.damage
        if record.placement == 1:
            summary.total_wins += 1
        
        # Update averages
        summary.avg_kills = (summary.avg_kills * n + record.kills) / (n + 1)
        summary.avg_damage = (summary.avg_damage * n + record.damage) / (n + 1)
        summary.avg_placement = (summary.avg_placement * n + record.placement) / (n + 1)
        summary.avg_survival = (summary.avg_survival * n + record.survival_time) / (n + 1)
        
        # Update bests
        summary.best_kills = max(summary.best_kills, record.kills)
        summary.best_damage = max(summary.best_damage, record.damage)
        summary.best_placement = min(summary.best_placement, record.placement)
        summary.best_mvp_score = max(summary.best_mvp_score, record.mvp_score)
        
        # Update recent matches
        summary.recent_matches.append({
            "kills": record.kills,
            "damage": record.damage,
            "placement": record.placement,
            "timestamp": record.timestamp,
        })
        
        # Calculate recent form (last 5 matches)
        recent = list(summary.recent_matches)[-5:]
        if recent:
            recent_avg = sum(
                (16 - r["placement"]) / 15 * r["kills"] * 10 + r["damage"] / 100
                for r in recent
            ) / len(recent)
            summary.recent_form = min(100, recent_avg * 2)
        
        # Calculate consistency
        if n >= 5:
            kills_values = [r.kills for r in list(self.player_records[player_id])[-10:]]
            if kills_values:
                mean_kills = sum(kills_values) / len(kills_values)
                variance = sum((k - mean_kills) ** 2 for k in kills_values) / len(kills_values)
                summary.consistency_score = max(0, 100 - variance * 20)
        
        # Calculate improvement rate
        if n >= 5:
            first_5 = list(self.player_records[player_id])[:5]
            last_5 = list(self.player_records[player_id])[-5:]
            
            first_avg = sum(r.kills for r in first_5) / len(first_5)
            last_avg = sum(r.kills for r in last_5) / len(last_5)
            
            matches_between = len(list(self.player_records[player_id])) - 5
            if matches_between > 0:
                summary.improvement_rate = (last_avg - first_avg) / matches_between
        
        # Determine trend
        summary.trend = self._determine_trend(player_id)
    
    def _update_streaks(self, player_id: str, record: PerformanceRecord) -> None:
        """Update active streaks"""
        
        if player_id not in self.active_streaks:
            self.active_streaks[player_id] = {}
        
        streaks = self.active_streaks[player_id]
        
        # Kill streak
        if record.kills >= 1:
            streaks[StreakType.KILL_STREAK] = streaks.get(StreakType.KILL_STREAK, 0) + 1
        else:
            streaks[StreakType.KILL_STREAK] = 0
        
        # Win streak
        if record.placement == 1:
            streaks[StreakType.WIN_STREAK] = streaks.get(StreakType.WIN_STREAK, 0) + 1
        else:
            streaks[StreakType.WIN_STREAK] = 0
        
        # Top 3 streak
        if record.placement <= 3:
            streaks[StreakType.TOP3_STREAK] = streaks.get(StreakType.TOP3_STREAK, 0) + 1
        else:
            streaks[StreakType.TOP3_STREAK] = 0
        
        # Update best streaks in summary
        summary = self.player_summaries.get(player_id)
        if summary:
            summary.current_kill_streak = streaks.get(StreakType.KILL_STREAK, 0)
            summary.best_kill_streak = max(
                summary.best_kill_streak,
                streaks.get(StreakType.KILL_STREAK, 0),
            )
            summary.matches_with_kills += 1 if record.kills > 0 else 0
    
    def _determine_trend(self, player_id: str) -> PerformanceTrend:
        """Determine performance trend"""
        
        records = list(self.player_records[player_id])
        if len(records) < 5:
            return PerformanceTrend.STABLE
        
        # Get recent performance
        recent = records[-5:]
        earlier = records[-10:-5] if len(records) >= 10 else records[:5]
        
        recent_avg = sum(r.kills * 10 + r.damage / 100 for r in recent) / len(recent)
        earlier_avg = sum(r.kills * 10 + r.damage / 100 for r in earlier) / len(earlier)
        
        change_pct = ((recent_avg - earlier_avg) / max(1, earlier_avg)) * 100
        
        if change_pct > 20:
            return PerformanceTrend.PEAKING
        elif change_pct > 5:
            return PerformanceTrend.IMPROVING
        elif change_pct < -20:
            return PerformanceTrend.SLUMPING
        elif change_pct < -5:
            return PerformanceTrend.DECLINING
        
        # Check consistency
        recent_values = [r.kills for r in recent]
        if recent_values:
            variance = sum((v - sum(recent_values)/len(recent_values))**2 for v in recent_values) / len(recent_values)
            if variance > 10:
                return PerformanceTrend.INCONSISTENT
        
        return PerformanceTrend.STABLE
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PREDICTIONS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def predict_next_performance(self, player_id: str) -> Optional[Dict[str, Any]]:
        """Predict next match performance"""
        
        summary = self.player_summaries.get(player_id)
        if not summary or summary.total_matches < 3:
            return None
        
        # Base prediction on averages
        predicted_kills = summary.avg_kills
        predicted_damage = summary.avg_damage
        
        # Adjust for trend
        if summary.trend == PerformanceTrend.IMPROVING:
            predicted_kills *= 1.1
            predicted_damage *= 1.1
        elif summary.trend == PerformanceTrend.DECLINING:
            predicted_kills *= 0.9
            predicted_damage *= 0.9
        elif summary.trend == PerformanceTrend.PEAKING:
            predicted_kills *= 1.2
        
        # Adjust for recent form
        form_factor = summary.recent_form / 50  # 1.0 = average
        predicted_kills *= form_factor
        predicted_damage *= form_factor
        
        confidence = min(0.9, summary.total_matches / 20)
        
        return {
            "predicted_kills": round(predicted_kills, 1),
            "predicted_damage": round(predicted_damage, 0),
            "confidence": round(confidence, 2),
            "trend": summary.trend.value,
            "recent_form": round(summary.recent_form, 1),
        }
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_player_summary(self, player_id: str) -> Optional[Dict[str, Any]]:
        """Get player performance summary"""
        summary = self.player_summaries.get(player_id)
        if not summary:
            return None
        
        return {
            "player_name": summary.player_name,
            "matches": summary.total_matches,
            "averages": {
                "kills": round(summary.avg_kills, 1),
                "damage": round(summary.avg_damage, 0),
                "placement": round(summary.avg_placement, 1),
                "survival_seconds": round(summary.avg_survival, 0),
            },
            "bests": {
                "kills": summary.best_kills,
                "damage": summary.best_damage,
                "placement": summary.best_placement,
                "mvp_score": round(summary.best_mvp_score, 1),
            },
            "trend": summary.trend.value,
            "recent_form": round(summary.recent_form, 1),
            "consistency": round(summary.consistency_score, 1),
            "streaks": {
                "current_kill_streak": summary.current_kill_streak,
                "best_kill_streak": summary.best_kill_streak,
            },
            "improvement": round(summary.improvement_rate, 3),
            "prediction": self.predict_next_performance(player_id),
        }
    
    def get_recent_matches(self, player_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent match performances"""
        records = list(self.player_records[player_id])[-limit:]
        
        return [
            {
                "match_id": r.match_id,
                "kills": r.kills,
                "damage": r.damage,
                "placement": r.placement,
                "survival": round(r.survival_time, 0),
                "mvp_score": round(r.mvp_score, 1),
                "timestamp": r.timestamp,
            }
            for r in records
        ]
    
    def get_top_performers(
        self,
        metric: str = "avg_kills",
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get top performing players by metric"""
        
        if metric == "avg_kills":
            sorted_players = sorted(
                self.player_summaries.values(),
                key=lambda s: s.avg_kills,
                reverse=True,
            )
        elif metric == "avg_damage":
            sorted_players = sorted(
                self.player_summaries.values(),
                key=lambda s: s.avg_damage,
                reverse=True,
            )
        elif metric == "recent_form":
            sorted_players = sorted(
                self.player_summaries.values(),
                key=lambda s: s.recent_form,
                reverse=True,
            )
        else:
            sorted_players = list(self.player_summaries.values())
        
        return [
            {
                "name": s.player_name,
                "matches": s.total_matches,
                "avg_kills": round(s.avg_kills, 1),
                "avg_damage": round(s.avg_damage, 0),
                "form": round(s.recent_form, 1),
                "trend": s.trend.value,
            }
            for s in sorted_players[:limit]
        ]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get tracker statistics"""
        return {
            "players_tracked": len(self.player_summaries),
            "total_records": sum(len(v) for v in self.player_records.values()),
            "top_performers": self.get_top_performers(limit=5),
        }
    
    def reset(self) -> None:
        """Reset tracker"""
        self.player_records.clear()
        self.player_summaries.clear()
        self.active_streaks.clear()
        self._log.info("Performance tracker reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("match_state_change")

    async def tick(self) -> None:
        """Periodic maintenance tick.

        Recomputes each tracked player's performance trend via the real
        _determine_trend() on a 30s cadence. Pure in-memory stdlib work:
        no network, no settings reads, smoke-safe.
        """
        now = time.time()
        if now - self._last_trend_refresh < 30.0:
            return
        self._last_trend_refresh = now

        for player_id, summary in list(self.player_summaries.items()):
            try:
                summary.trend = self._determine_trend(player_id)
            except Exception:
                continue

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
