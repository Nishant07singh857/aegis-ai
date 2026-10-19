# backend/app/ml/__init__.py
"""
AEGIS AI - Machine Learning Models
Training pipelines, model architectures, datasets,
augmentations, evaluation, and model export utilities.

Submodules are imported lazily (PEP 562) so that importing the ``app.ml``
package never pulls in heavy optional dependencies (torch, cv2, …) unless
the caller actually touches them. In the full backend venv those packages
are installed and everything behaves exactly as before.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .fight_detection import (
        FightDetectionModel,
        FightDataset,
        FightAugmentations,
        FightTrainer,
        FightEvaluator,
        ModelExporter,
    )
    from .killfeed_detection import (
        KillFeedModel,
        KillFeedDataset,
        KillFeedAugmentations,
        KillFeedTrainer,
        KillFeedEvaluator,
        KillFeedExporter,
    )
    from . import player_profiling
    from .registry import VisionModelRegistry, get_registry
    from .trained_detector import TrainedDetector, TrainedDetectorConfig
    from .fallback_chain import ChainedDetector
    from .detection_result import DetectionBox, DetectionResult, DetectionStatus

_LAZY_EXPORTS = {
    "FightDetectionModel": ".fight_detection",
    "FightDataset": ".fight_detection",
    "FightAugmentations": ".fight_detection",
    "FightTrainer": ".fight_detection",
    "FightEvaluator": ".fight_detection",
    "ModelExporter": ".fight_detection",
    "KillFeedModel": ".killfeed_detection",
    "KillFeedDataset": ".killfeed_detection",
    "KillFeedAugmentations": ".killfeed_detection",
    "KillFeedTrainer": ".killfeed_detection",
    "KillFeedEvaluator": ".killfeed_detection",
    "KillFeedExporter": ".killfeed_detection",
    "player_profiling": ".player_profiling",
    "VisionModelRegistry": ".registry",
    "get_registry": ".registry",
    "TrainedDetector": ".trained_detector",
    "TrainedDetectorConfig": ".trained_detector",
    "ChainedDetector": ".fallback_chain",
    "build_chained_detector": ".vision_wiring",
    "build_killfeed_detector": ".vision_wiring",
    "KILLFEED_ENTRY_CLASS": ".vision_wiring",
    "DetectionBox": ".detection_result",
    "DetectionResult": ".detection_result",
    "DetectionStatus": ".detection_result",
}


def __getattr__(name: str):
    """Lazily import heavy submodules on first attribute access."""
    if name in _LAZY_EXPORTS:
        import importlib

        module = importlib.import_module(_LAZY_EXPORTS[name], __name__)
        value = module if name == "player_profiling" else getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "FightDetectionModel",
    "FightDataset",
    "FightAugmentations",
    "FightTrainer",
    "FightEvaluator",
    "ModelExporter",
    "KillFeedModel",
    "KillFeedDataset",
    "KillFeedAugmentations",
    "KillFeedTrainer",
    "KillFeedEvaluator",
    "KillFeedExporter",
    "player_profiling",
    "VisionModelRegistry",
    "get_registry",
    "TrainedDetector",
    "TrainedDetectorConfig",
    "ChainedDetector",
    "build_chained_detector",
    "build_killfeed_detector",
    "KILLFEED_ENTRY_CLASS",
    "DetectionBox",
    "DetectionResult",
    "DetectionStatus",
]
