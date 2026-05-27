# backend/app/analytics/trend_analyzer.py
"""
AEGIS AI - Trend Analyzer
Analyzes performance trends over time for teams and players.
Detects patterns, streaks, improvements, and declines.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict, deque
import structlog
import numpy as np

logger = structlog.get_logger("aegis.analytics.trends")


class TrendType(str, Enum):
    """Types of trends"""
    IMPROVING = "improving"
    DECLINING = "declining"
    STABLE = "stable"
    PEAKING = "peaking"
    INCONSISTENT = "inconsistent"
    BREAKOUT = "breakout"  # Sudden improvement
    SLUMPING = "slumping"  # Sudden decline


@dataclass
class Trend:
    """A detected trend"""
    trend_id: str
    trend_type: TrendType
    subject_type: str  # "team" or "player"
    subject_id: str
    subject_name: str
    
    # Metric
    metric: str = "kills"  # kills, damage, placement, survival
    current_value: float = 0.0
    average_value: float = 0.0
    peak_value: float = 0.0
    lowest_value: float = 0.0
    
    # Change
    change_percent: float = 0.0
    confidence: float = 0.5
    
    # Data points
    data_points: List[Dict[str, Any]] = field(default_factory=list)
    matches_analyzed: int = 0
    
    # Timeline
    detected_at: float = field(default_factory=time.time)
    duration_matches: int = 0


class TrendAnalyzer:
    """
    Analyzes performance trends for teams and players.
    
    Features:
    - Trend detection (improving, declining, stable, etc.)
    - Streak analysis
    - Performance projections
    - Anomaly detection
    - Moving averages
    """
    
    def __init__(self):
        self.trends: Dict[str, List[Trend]] = defaultdict(list)
        
        # Performance history
        self.performance_history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=100)
        )
        
        # Moving average windows
        self.ma_windows = [3, 5, 10]
        
        self._log = logger.bind(component="trend_analyzer")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DATA RECORDING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def record_performance(
        self,
        subject_id: str,
        subject_name: str,
        subject_type: str,
        match_id: str,
        stats: Dict[str, Any],
    ) -> None:
        """Record a performance data point"""
        
        data_point = {
            "match_id": match_id,
            "timestamp": time.time(),
            "kills": stats.get("kills", 0),
            "damage": stats.get("damage", 0),
            "placement": stats.get("placement", 16),
            "survival_time": stats.get("survival_time", 0),
            "points": stats.get("points", 0),
            "mvp_score": stats.get("mvp_score", 0),
        }
        
        self.performance_history[subject_id].append(data_point)
    
    def record_batch(
        self,
        performances: List[Dict[str, Any]],
    ) -> None:
        """Record multiple performances"""
        for perf in performances:
            self.record_performance(
                subject_id=perf.get("subject_id", ""),
                subject_name=perf.get("subject_name", ""),
                subject_type=perf.get("subject_type", "player"),
                match_id=perf.get("match_id", ""),
                stats=perf.get("stats", {}),
            )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TREND ANALYSIS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def analyze(
        self,
        subject_id: str,
        metric: str = "kills",
        window: int = 5,
    ) -> Optional[Trend]:
        """
        Analyze trends for a subject.
        
        Args:
            subject_id: Team or player ID
            metric: Metric to analyze
            window: Number of recent matches to analyze
            
        Returns:
            Detected Trend or None
        """
        
        history = list(self.performance_history.get(subject_id, []))
        
        if len(history) < 5:
            return None
        
        # Get metric values
        values = [h.get(metric, 0) for h in history]
        recent = values[-window:]
        previous = values[-window*2:-window] if len(values) >= window * 2 else values[:window]
        
        recent_avg = sum(recent) / len(recent)
        previous_avg = sum(previous) / len(previous)
        
        all_avg = sum(values) / len(values)
        all_peak = max(values)
        all_low = min(values)
        
        # Calculate change
        if previous_avg > 0:
            change_pct = ((recent_avg - previous_avg) / previous_avg) * 100
        else:
            change_pct = 100 if recent_avg > 0 else 0
        
        # Determine trend type
        trend_type = self._determine_trend_type(values, recent_avg, change_pct)
        
        # Get subject info from first data point
        first = history[0] if history else {}
        
        trend = Trend(
            trend_id=f"trend_{subject_id}_{metric}_{int(time.time())}",
            trend_type=trend_type,
            subject_type="player" if "player" in subject_id else "team",
            subject_id=subject_id,
            subject_name=first.get("subject_name", ""),
            metric=metric,
            current_value=round(recent_avg, 1),
            average_value=round(all_avg, 1),
            peak_value=round(all_peak, 1),
            lowest_value=round(all_low, 1),
            change_percent=round(change_pct, 1),
            confidence=min(0.9, len(history) / 20),
            matches_analyzed=len(history),
            duration_matches=window,
        )
        
        # Store trend
        self.trends[subject_id].append(trend)
        
        return trend
    
    def _determine_trend_type(
        self,
        values: List[float],
        recent_avg: float,
        change_pct: float,
    ) -> TrendType:
        """Determine the trend type"""
        
        # Check consistency
        variance = np.var(values) if len(values) > 1 else 0
        
        if variance > (recent_avg * 2) and recent_avg > 0:
            return TrendType.INCONSISTENT
        
        if change_pct > 30:
            return TrendType.BREAKOUT
        elif change_pct > 10:
            return TrendType.IMPROVING
        elif change_pct < -30:
            return TrendType.SLUMPING
        elif change_pct < -10:
            return TrendType.DECLINING
        elif change_pct > 5 and recent_avg > sum(values) / len(values) * 1.1:
            return TrendType.PEAKING
        
        return TrendType.STABLE
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MOVING AVERAGES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_moving_averages(
        self,
        subject_id: str,
        metric: str = "kills",
        windows: Optional[List[int]] = None,
    ) -> Dict[int, List[float]]:
        """Calculate moving averages for different windows"""
        
        windows = windows or self.ma_windows
        values = [h.get(metric, 0) for h in self.performance_history.get(subject_id, [])]
        
        results = {}
        
        for window in windows:
            if len(values) >= window:
                ma = []
                for i in range(len(values) - window + 1):
                    ma.append(sum(values[i:i+window]) / window)
                results[window] = [round(v, 1) for v in ma]
            else:
                results[window] = []
        
        return results
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PROJECTIONS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def project_next_performance(
        self,
        subject_id: str,
        metric: str = "kills",
    ) -> Optional[Dict[str, Any]]:
        """Project next match performance"""
        
        values = [h.get(metric, 0) for h in self.performance_history.get(subject_id, [])]
        
        if len(values) < 3:
            return None
        
        # Simple linear regression
        x = np.arange(len(values))
        y = np.array(values)
        
        z = np.polyfit(x, y, 1)
        slope = z[0]
        
        # Project next value
        next_x = len(values)
        projected = slope * next_x + z[1]
        
        # Confidence based on R-squared
        y_pred = slope * x + z[1]
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
        
        return {
            "projected_value": round(max(0, projected), 1),
            "trend_direction": "up" if slope > 0.1 else "down" if slope < -0.1 else "stable",
            "confidence": round(max(0, min(1, r_squared)), 2),
            "based_on_matches": len(values),
        }
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_trends(self, subject_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Get trends for a subject"""
        trends = self.trends.get(subject_id, [])[-limit:]
        
        return [
            {
                "trend_type": t.trend_type.value,
                "metric": t.metric,
                "current": t.current_value,
                "average": t.average_value,
                "change_pct": t.change_percent,
                "confidence": round(t.confidence, 2),
                "matches": t.matches_analyzed,
            }
            for t in trends
        ]
    
    def get_hot_players(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Get players with improving trends"""
        
        improving = []
        
        for subject_id, trends in self.trends.items():
            if trends and trends[-1].trend_type in [TrendType.IMPROVING, TrendType.BREAKOUT]:
                t = trends[-1]
                improving.append({
                    "name": t.subject_name,
                    "metric": t.metric,
                    "change": t.change_percent,
                    "current": t.current_value,
                })
        
        return sorted(improving, key=lambda x: x["change"], reverse=True)[:limit]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get trend analyzer statistics"""
        return {
            "subjects_tracked": len(self.performance_history),
            "total_trends": sum(len(v) for v in self.trends.values()),
            "hot_players": self.get_hot_players(5),
        }
    
    def reset(self) -> None:
        """Reset analyzer"""
        self.trends.clear()
        self.performance_history.clear()
        self._log.info("Trend analyzer reset")