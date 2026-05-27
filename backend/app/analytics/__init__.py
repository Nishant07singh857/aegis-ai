# backend/app/analytics/__init__.py
"""
AEGIS AI - Analytics Engine
Tournament scoring, live leaderboards, tactical heatmaps,
post-match reports, trend analysis, and comparative analytics.
"""

from .points_calculator import PointsCalculator, TournamentPoints, PlacementPoints
from .leaderboard_generator import LeaderboardGenerator, Leaderboard, LeaderboardEntry
from .heatmap_generator import HeatmapGenerator, Heatmap, HeatmapType
from .report_generator import ReportGenerator, MatchReport, PlayerReport
from .trend_analyzer import TrendAnalyzer, Trend, TrendType
from .comparative_analysis import ComparativeAnalyzer, Comparison, ComparisonMetric

__all__ = [
    "PointsCalculator", "TournamentPoints", "PlacementPoints",
    "LeaderboardGenerator", "Leaderboard", "LeaderboardEntry",
    "HeatmapGenerator", "Heatmap", "HeatmapType",
    "ReportGenerator", "MatchReport", "PlayerReport",
    "TrendAnalyzer", "Trend", "TrendType",
    "ComparativeAnalyzer", "Comparison", "ComparisonMetric",
]