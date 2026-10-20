# backend/app/analytics/report_generator.py
"""
AEGIS AI - Report Generator
Generates post-match reports with match summaries,
player performance, team analysis, and recommendations.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict
import structlog

logger = structlog.get_logger("aegis.analytics.reports")


class ReportType(str, Enum):
    """Types of reports"""
    MATCH_SUMMARY = "match_summary"
    PLAYER_PERFORMANCE = "player_performance"
    TEAM_ANALYSIS = "team_analysis"
    TOURNAMENT_RECAP = "tournament_recap"
    SCOUTING = "scouting"
    COACHING = "coaching"


@dataclass
class MatchReport:
    """Complete match report"""
    report_id: str
    match_id: str
    report_type: ReportType = ReportType.MATCH_SUMMARY
    title: str = "Match Report"
    generated_at: float = field(default_factory=time.time)
    
    # Match overview
    match_name: str = ""
    map_name: str = ""
    duration: float = 0.0
    winner_team: str = ""
    mvp_player: str = ""
    
    # Statistics
    total_kills: int = 0
    total_damage: int = 0
    total_knocks: int = 0
    total_revives: int = 0
    total_utility_used: int = 0
    
    # Team standings
    team_results: List[Dict[str, Any]] = field(default_factory=list)
    
    # Key moments
    key_moments: List[Dict[str, Any]] = field(default_factory=list)
    highlight_clips: List[str] = field(default_factory=list)
    
    # Analysis
    match_analysis: str = ""
    turning_points: List[str] = field(default_factory=list)
    
    # Player highlights
    top_fraggers: List[Dict[str, Any]] = field(default_factory=list)
    top_support: List[Dict[str, Any]] = field(default_factory=list)
    
    # AI insights
    ai_insights: List[str] = field(default_factory=list)


@dataclass
class PlayerReport:
    """Individual player performance report"""
    report_id: str
    player_id: str
    player_name: str
    team_name: str
    generated_at: float = field(default_factory=time.time)
    
    # Match stats
    match_id: str = ""
    kills: int = 0
    knocks: int = 0
    assists: int = 0
    damage: int = 0
    headshots: int = 0
    revives: int = 0
    healing: int = 0
    survival_time: float = 0.0
    placement: int = 16
    
    # Performance rating
    performance_score: float = 0.0
    mvp_score: float = 0.0
    rating: str = "Average"  # Excellent, Good, Average, Below Average, Poor
    
    # Weapons
    favorite_weapon: str = ""
    weapon_accuracy: Dict[str, float] = field(default_factory=dict)
    
    # Comparison
    vs_average: Dict[str, float] = field(default_factory=dict)
    personal_bests: Dict[str, Any] = field(default_factory=dict)
    
    # Insights
    strengths: List[str] = field(default_factory=list)
    improvements: List[str] = field(default_factory=list)


class ReportGenerator:
    """
    Generates post-match reports and analysis.
    
    Features:
    - Match summary reports
    - Player performance reports
    - Team analysis reports
    - Tournament recaps
    - Coaching insights
    - AI-generated analysis
    """
    
    def __init__(self, event_bus: Any = None):
        self.match_reports: Dict[str, MatchReport] = {}
        self.player_reports: Dict[str, List[PlayerReport]] = defaultdict(list)
        # Phase 22 (W3, M12): optional bus for insight.generated —
        # set via constructor or set_event_bus(); emission best-effort.
        self._event_bus = event_bus
        self._log = logger.bind(component="report_generator")

    def set_event_bus(self, bus: Any) -> None:
        """Attach an event bus so generated insights emit insight.generated."""
        self._event_bus = bus
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MATCH REPORT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def generate_match_report(
        self,
        match_id: str,
        match_data: Dict[str, Any],
        team_results: List[Dict[str, Any]],
        player_stats: List[Dict[str, Any]],
        key_events: List[Dict[str, Any]],
    ) -> MatchReport:
        """Generate a comprehensive match report"""
        
        report = MatchReport(
            report_id=f"report_{match_id}",
            match_id=match_id,
            title=f"Match Report - {match_data.get('match_name', '')}",
            match_name=match_data.get("match_name", ""),
            map_name=match_data.get("map_name", ""),
            duration=match_data.get("duration", 0),
            winner_team=match_data.get("winner_team", ""),
            mvp_player=match_data.get("mvp", ""),
        )
        
        # Calculate totals
        report.total_kills = sum(t.get("kills", 0) for t in team_results)
        report.total_damage = sum(t.get("damage", 0) for t in team_results)
        report.total_knocks = sum(p.get("knocks", 0) for p in player_stats)
        report.total_revives = sum(p.get("revives", 0) for p in player_stats)
        
        # Team results (sorted by placement)
        report.team_results = sorted(
            team_results,
            key=lambda t: t.get("placement", 16),
        )
        
        # Key moments
        report.key_moments = sorted(
            [e for e in key_events if e.get("importance", 0) > 0.7],
            key=lambda e: e.get("importance", 0),
            reverse=True,
        )[:5]
        
        # Top fraggers
        report.top_fraggers = sorted(
            player_stats,
            key=lambda p: p.get("kills", 0),
            reverse=True,
        )[:3]
        
        # Top support
        report.top_support = sorted(
            player_stats,
            key=lambda p: p.get("revives", 0) + p.get("assists", 0),
            reverse=True,
        )[:3]
        
        # AI Insights
        report.ai_insights = self._generate_match_insights(report)

        # Phase 22 (W3, M12): publish each insight as insight.generated
        # (best-effort; sync context schedules on a running loop or
        # runs inline — never breaks report generation).
        if self._event_bus is not None:
            try:
                import asyncio

                from app.core.event_names import emit_insight_generated

                for text in report.ai_insights:
                    coro = emit_insight_generated(
                        self._event_bus,
                        insight={"text": text, "kind": "match_report"},
                        match_id=match_id,
                    )
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        asyncio.run(coro)
                    else:
                        loop.create_task(coro)
            except Exception as exc:  # pragma: no cover - defensive
                self._log.warning("insight_emit_failed", error=str(exc)[:200])
        
        # Turning points
        report.turning_points = self._identify_turning_points(key_events)
        
        # Match analysis
        report.match_analysis = self._generate_match_analysis(report)
        
        self.match_reports[report.report_id] = report
        
        return report
    
    def generate_player_report(
        self,
        player_id: str,
        player_name: str,
        team_name: str,
        match_id: str,
        player_stats: Dict[str, Any],
        historical_stats: Optional[Dict[str, Any]] = None,
    ) -> PlayerReport:
        """Generate individual player report"""
        
        report = PlayerReport(
            report_id=f"player_report_{player_id}_{match_id}",
            player_id=player_id,
            player_name=player_name,
            team_name=team_name,
            match_id=match_id,
            kills=player_stats.get("kills", 0),
            knocks=player_stats.get("knocks", 0),
            assists=player_stats.get("assists", 0),
            damage=player_stats.get("damage", 0),
            headshots=player_stats.get("headshots", 0),
            revives=player_stats.get("revives", 0),
            healing=player_stats.get("healing", 0),
            survival_time=player_stats.get("survival_time", 0),
            placement=player_stats.get("placement", 16),
            favorite_weapon=player_stats.get("favorite_weapon", ""),
            weapon_accuracy=player_stats.get("weapon_accuracy", {}),
        )
        
        # Calculate performance score (0-100)
        kills_score = min(30, report.kills * 6)
        damage_score = min(25, report.damage / 40)
        survival_score = min(15, report.survival_time / 120)
        team_score = min(15, (report.revives * 4 + report.assists * 2))
        placement_score = min(15, (17 - report.placement) * 1.5)
        
        report.performance_score = kills_score + damage_score + survival_score + team_score + placement_score
        
        # Rating
        if report.performance_score >= 80:
            report.rating = "Excellent"
        elif report.performance_score >= 60:
            report.rating = "Good"
        elif report.performance_score >= 40:
            report.rating = "Average"
        elif report.performance_score >= 20:
            report.rating = "Below Average"
        else:
            report.rating = "Poor"
        
        # Compare to historical
        if historical_stats:
            report.vs_average = {
                "kills": round(report.kills - historical_stats.get("avg_kills", 0), 1),
                "damage": round(report.damage - historical_stats.get("avg_damage", 0), 0),
                "survival": round(report.survival_time - historical_stats.get("avg_survival", 0), 0),
            }
        
        # Strengths and improvements
        if report.kills >= 3:
            report.strengths.append("Strong fragging ability")
        if report.revives >= 2:
            report.strengths.append("Excellent team support")
        if report.headshots >= report.kills * 0.5 and report.kills > 0:
            report.strengths.append("Deadly headshot accuracy")
        
        if report.kills == 0 and report.damage < 100:
            report.improvements.append("Need to increase combat engagement")
        if report.survival_time < 300:
            report.improvements.append("Improve early-game survival")
        
        # Store report
        self.player_reports[player_id].append(report)
        
        return report
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ANALYSIS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _generate_match_insights(self, report: MatchReport) -> List[str]:
        """Generate AI match insights"""
        
        insights = []
        
        if report.winner_team:
            insights.append(f"{report.winner_team} secured victory with dominant performance")
        
        if report.total_kills > 20:
            insights.append(f"High-action match with {report.total_kills} total eliminations")
        
        if report.mvp_player:
            mvp = next((p for p in report.top_fraggers if p.get("player_name") == report.mvp_player), None)
            if mvp:
                insights.append(f"{report.mvp_player} earned MVP with {mvp.get('kills', 0)} kills")
        
        # Zone analysis
        insights.append("Key rotations through center map proved decisive")
        
        return insights
    
    def _identify_turning_points(self, events: List[Dict[str, Any]]) -> List[str]:
        """Identify match turning points"""
        
        turning_points = []
        
        for event in events:
            if event.get("type") == "team_wipe" and event.get("importance", 0) > 0.8:
                turning_points.append(
                    f"Team wipe at {event.get('location', 'unknown')} shifted momentum"
                )
            elif event.get("type") == "clutch_win":
                turning_points.append(
                    f"{event.get('player', 'Player')}'s clutch play changed the game"
                )
        
        return turning_points[:3]
    
    def _generate_match_analysis(self, report: MatchReport) -> str:
        """Generate match analysis text"""
        
        parts = []
        
        parts.append(f"The match on {report.map_name} lasted {report.duration:.0f} seconds.")
        parts.append(f"Total of {report.total_kills} eliminations were recorded.")
        
        if report.winner_team:
            winner = next(
                (t for t in report.team_results if t.get("team_name") == report.winner_team),
                None,
            )
            if winner:
                parts.append(
                    f"{report.winner_team} claimed victory with {winner.get('kills', 0)} kills "
                    f"and {winner.get('points', 0)} points."
                )
        
        if report.mvp_player:
            parts.append(f"The MVP was awarded to {report.mvp_player}.")
        
        return " ".join(parts)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_match_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        """Get match report"""
        report = self.match_reports.get(report_id)
        if not report:
            return None
        
        return {
            "title": report.title,
            "match_name": report.match_name,
            "map": report.map_name,
            "duration": round(report.duration, 0),
            "winner": report.winner_team,
            "mvp": report.mvp_player,
            "total_kills": report.total_kills,
            "total_damage": report.total_damage,
            "team_results": report.team_results[:5],
            "top_fraggers": report.top_fraggers,
            "key_moments": report.key_moments,
            "ai_insights": report.ai_insights,
            "match_analysis": report.match_analysis,
        }
    
    def get_player_reports(self, player_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get player reports"""
        reports = self.player_reports.get(player_id, [])[-limit:]
        
        return [
            {
                "match_id": r.match_id,
                "kills": r.kills,
                "damage": r.damage,
                "performance_score": round(r.performance_score, 1),
                "rating": r.rating,
                "strengths": r.strengths,
                "improvements": r.improvements,
            }
            for r in reports
        ]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get report generator statistics"""
        return {
            "match_reports": len(self.match_reports),
            "player_reports": sum(len(v) for v in self.player_reports.values()),
            "players_tracked": len(self.player_reports),
        }
    
    def reset(self) -> None:
        """Reset generator"""
        self.match_reports.clear()
        self.player_reports.clear()
        self._log.info("Report generator reset")