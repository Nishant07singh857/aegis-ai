# backend/app/agents/prediction_agents/__init__.py
"""
AEGIS AI - Prediction Agents
Fight outcome prediction, survival analysis,
zone prediction, and match winner prediction.
"""

from .fight_predictor import FightPredictor, FightPrediction
from .survival_predictor import SurvivalPredictor, SurvivalPrediction
from .zone_predictor import (
    ZonePredictor,
    ZonePrediction,
    ZonePredictionUnavailable,
)
from .winner_predictor import WinnerPredictor, WinnerPrediction
from .win_probability import (
    attach_deltas,
    form_index,
    h2h_rate_matrix,
    score_live_win_probability,
    snapshot_map,
)

__all__ = [
    "FightPredictor",
    "FightPrediction",
    "SurvivalPredictor",
    "SurvivalPrediction",
    "ZonePredictor",
    "ZonePrediction",
    "ZonePredictionUnavailable",
    "WinnerPredictor",
    "WinnerPrediction",
    "attach_deltas",
    "form_index",
    "h2h_rate_matrix",
    "score_live_win_probability",
    "snapshot_map",
]