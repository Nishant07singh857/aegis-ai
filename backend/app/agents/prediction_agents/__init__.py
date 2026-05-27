# backend/app/agents/prediction_agents/__init__.py
"""
AEGIS AI - Prediction Agents
Fight outcome prediction, survival analysis,
zone prediction, and match winner prediction.
"""

from .fight_predictor import FightPredictor, FightPrediction
from .survival_predictor import SurvivalPredictor, SurvivalPrediction
from .zone_predictor import ZonePredictor, ZonePrediction
from .winner_predictor import WinnerPredictor, WinnerPrediction

__all__ = [
    "FightPredictor",
    "FightPrediction",
    "SurvivalPredictor",
    "SurvivalPrediction",
    "ZonePredictor",
    "ZonePrediction",
    "WinnerPredictor",
    "WinnerPrediction",
]