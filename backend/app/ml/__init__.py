# backend/app/ml/__init__.py
"""
AEGIS AI - Machine Learning Models
Training pipelines, model architectures, datasets,
augmentations, evaluation, and model export utilities.
"""

from .fight_detection import (
    FightDetectionModel,
    FightDataset,
    FightAugmentations,
    FightTrainer,
    FightEvaluator,
    ModelExporter,
)

__all__ = [
    "FightDetectionModel",
    "FightDataset",
    "FightAugmentations",
    "FightTrainer",
    "FightEvaluator",
    "ModelExporter",
]
