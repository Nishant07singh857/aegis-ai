# backend/app/ml/fight_detection/__init__.py
"""
AEGIS AI - Fight Detection ML Module
Complete training pipeline for fight detection models.
"""

from .model import FightDetectionModel, ModelConfig
from .dataset import FightDataset, DatasetConfig
from .augmentations import FightAugmentations, AugmentationConfig
from .trainer import FightTrainer, TrainingConfig
from .evaluator import FightEvaluator, EvalConfig
from .export import ModelExporter, ExportConfig

__all__ = [
    "FightDetectionModel",
    "ModelConfig",
    "FightDataset",
    "DatasetConfig",
    "FightAugmentations",
    "AugmentationConfig",
    "FightTrainer",
    "TrainingConfig",
    "FightEvaluator",
    "EvalConfig",
    "ModelExporter",
    "ExportConfig",
]