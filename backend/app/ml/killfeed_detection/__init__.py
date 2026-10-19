# backend/app/ml/killfeed_detection/__init__.py
"""
AEGIS AI - Kill-Feed Detection ML Module
Training pipeline for the kill-feed region/entry detector.

This is the highest-value first ML task for AEGIS: the kill feed is a
*structured UI region* (top-right overlay), so a small YOLOv8-nano model
learns it fast and cheaply. The detector outputs:

- ``killfeed_panel`` — the whole kill-feed region (crop target for OCR)
- ``killfeed_entry`` — one kill entry row inside the panel (per-row OCR crop)

Downstream, the Phase 14 vision ``KillFeedDetector`` consumes these crops
for OCR; this package produces the trained weights it runs on.

House pattern (mirrors ``fight_detection``):
    model.py         -> KillFeedModel, ModelConfig
    dataset.py       -> KillFeedDataset, DatasetConfig
    augmentations.py -> KillFeedAugmentations, AugmentationConfig
    trainer.py       -> KillFeedTrainer, TrainingConfig
    evaluator.py     -> KillFeedEvaluator, EvalConfig
    export.py        -> KillFeedExporter, ExportConfig

Heavy dependencies (torch, ultralytics, cv2) are imported lazily inside the
functions that need them, so importing this package never fails on a machine
that only has the training requirements partially installed.
"""

from .model import KillFeedModel, ModelConfig, DetectionModelBase
from .dataset import KillFeedDataset, DatasetConfig
from .augmentations import KillFeedAugmentations, AugmentationConfig
from .trainer import KillFeedTrainer, TrainingConfig, TrainingReport
from .evaluator import KillFeedEvaluator, EvalConfig, EvalReport
from .export import KillFeedExporter, ExportConfig, ExportReport

__all__ = [
    "DetectionModelBase",
    "KillFeedModel",
    "ModelConfig",
    "KillFeedDataset",
    "DatasetConfig",
    "KillFeedAugmentations",
    "AugmentationConfig",
    "KillFeedTrainer",
    "TrainingConfig",
    "TrainingReport",
    "KillFeedEvaluator",
    "EvalConfig",
    "EvalReport",
    "KillFeedExporter",
    "ExportConfig",
    "ExportReport",
]
