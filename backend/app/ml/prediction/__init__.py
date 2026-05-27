# backend/app/ml/prediction/__init__.py
"""
AEGIS AI - Prediction ML Module
Fight outcome prediction, survival analysis,
feature engineering, and model ensemble.
"""

from .fight_outcome_model import FightOutcomeModel, FightFeatures, OutcomePrediction
from .survival_model import SurvivalModel, SurvivalFeatures, SurvivalPrediction
from .feature_engineering import FeatureEngineer, FeatureSet, FeatureConfig
from .ensemble import ModelEnsemble, EnsembleConfig, EnsemblePrediction

__all__ = [
    "FightOutcomeModel",
    "FightFeatures", 
    "OutcomePrediction",
    "SurvivalModel",
    "SurvivalFeatures",
    "SurvivalPrediction",
    "FeatureEngineer",
    "FeatureSet",
    "FeatureConfig",
    "ModelEnsemble",
    "EnsembleConfig",
    "EnsemblePrediction",
]