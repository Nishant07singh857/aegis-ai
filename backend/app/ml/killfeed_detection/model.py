# backend/app/ml/killfeed_detection/model.py
"""
AEGIS AI - Kill-Feed Detection Model
YOLOv8-nano (via ultralytics) fine-tuned for kill-feed region/entry detection.

Why YOLOv8-nano for this task:
- The kill feed is a *structured UI region*: high contrast, fixed screen
  area, consistent styling. A nano model learns this fast and runs at
  100+ FPS on CPU — exactly what the live broadcast pipeline needs.
- Fine-tuning (not training from scratch): COCO-pretrained weights already
  know "rectangular UI-ish blobs"; we only retrain the head for our classes.

Modularity for harder tasks (fight detection, zone tracking, ...):
    class MyTaskModel(DetectionModelBase): ...   # implement build/predict/info
and reuse KillFeedTrainer / KillFeedEvaluator / KillFeedExporter unchanged —
they program against the base interface + ultralytics, not this class.

ultralytics is imported lazily so that `import model` works anywhere; the
import error you get without it tells you exactly what to install.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aegis.ml.killfeed.model")


def _require_ultralytics():
    try:
        from ultralytics import YOLO
        return YOLO
    except ImportError as e:
        raise ImportError(
            "ultralytics is required for kill-feed training/inference. "
            "Install the training requirements: "
            "pip install -r backend/app/ml/training/requirements-train.txt"
        ) from e


@dataclass
class ModelConfig:
    """Kill-feed model configuration."""

    # Architecture — nano first; escalate only if eval says so.
    model_name: str = "yolov8n.pt"   # or yolov8s.pt / yolov8m.pt, or a local .pt
    task: str = "detect"

    # Classes (must match DatasetConfig.class_names order)
    class_names: List[str] = field(default_factory=lambda: [
        "killfeed_panel",
        "killfeed_entry",
    ])

    # Input
    imgsz: int = 640

    # Fine-tuning
    freeze_backbone_layers: int = 0  # >0 freezes the first N layers (head-only fine-tune)
    pretrained: bool = True          # False -> random init (not recommended)

    # Inference defaults (overridable per call)
    conf: float = 0.25
    iou: float = 0.6


class DetectionModelBase(ABC):
    """
    Minimal interface every AEGIS detection task model implements.
    Harder tasks (fight detection, drop-spot detection, ...) plug in here;
    the trainer / evaluator / exporter only depend on this interface and on
    the ultralytics result objects, never on KillFeedModel specifics.
    """

    @abstractmethod
    def build(self, device: str = "cpu") -> "DetectionModelBase":
        """Load weights and prepare for train/predict. Returns self."""

    @abstractmethod
    def predict(self, source: Any, **kwargs) -> List[Dict[str, Any]]:
        """Run inference. Returns a list of dicts:
        {"boxes": [[x1,y1,x2,y2], ...], "scores": [...], "classes": [...],
         "class_names": [...]} — one dict per input image."""

    @abstractmethod
    def info(self) -> Dict[str, Any]:
        """Model card: architecture, params, classes, device."""

    @property
    @abstractmethod
    def yolo(self):
        """The underlying ultralytics YOLO handle (for trainer/evaluator)."""


class KillFeedModel(DetectionModelBase):
    """YOLOv8-nano wrapper for kill-feed region/entry detection."""

    def __init__(self, config: Optional[ModelConfig] = None):
        self.config = config or ModelConfig()
        self._yolo = None
        self.device = "cpu"

    # ------------------------------------------------------------------
    def build(self, device: str = "cpu") -> "KillFeedModel":
        YOLO = _require_ultralytics()
        weights = self.config.model_name
        if not os.path.isfile(weights) and not self.config.pretrained:
            raise FileNotFoundError(
                f"model weights {weights!r} not found and pretrained=False; "
                "pass a valid .pt path or set pretrained=True to download."
            )
        logger.info("loading YOLO weights: %s (device=%s)", weights, device)
        self._yolo = YOLO(weights)
        self.device = device
        # NOTE: device is passed explicitly to every train/predict/val call;
        # no .to() needed here (kept lazy so CPU-only installs never break).
        if self.config.freeze_backbone_layers > 0:
            self.freeze_backbone(self.config.freeze_backbone_layers)
        return self

    def freeze_backbone(self, n_layers: int) -> None:
        """Freeze the first n_layers for head-only fine-tuning.

        Useful when the dataset is small (<2k frames): the backbone keeps its
        COCO features and only the detection head learns kill-feed shapes,
        which reduces overfitting. With more data, train unfrozen.
        """
        if self._yolo is None:
            raise RuntimeError("call build() before freeze_backbone()")
        frozen = 0
        for i, (name, param) in enumerate(self._yolo.model.named_parameters()):
            if i < n_layers:
                param.requires_grad = False
                frozen += 1
        logger.info("froze %d backbone parameter groups (head-only fine-tune)", frozen)

    # ------------------------------------------------------------------
    @property
    def yolo(self):
        if self._yolo is None:
            raise RuntimeError("call build() first")
        return self._yolo

    def predict(self, source: Any, conf: Optional[float] = None,
                iou: Optional[float] = None) -> List[Dict[str, Any]]:
        YOLO = _require_ultralytics()  # noqa: F841 (ensures dep present)
        results = self.yolo.predict(
            source,
            conf=self.config.conf if conf is None else conf,
            iou=self.config.iou if iou is None else iou,
            device=self.device,
            verbose=False,
        )
        out = []
        for r in results:
            boxes, scores, classes, names = [], [], [], []
            if r.boxes is not None and len(r.boxes) > 0:
                for b in r.boxes:
                    boxes.append([float(v) for v in b.xyxy[0].tolist()])
                    scores.append(float(b.conf[0]))
                    cls = int(b.cls[0])
                    classes.append(cls)
                    names.append(r.names.get(cls, str(cls)))
            out.append({
                "boxes": boxes, "scores": scores,
                "classes": classes, "class_names": names,
            })
        return out

    def num_parameters(self) -> int:
        if self._yolo is None:
            raise RuntimeError("call build() first")
        return sum(p.numel() for p in self._yolo.model.parameters())

    def info(self) -> Dict[str, Any]:
        try:
            params = self.num_parameters()
        except RuntimeError:
            params = -1
        return {
            "architecture": self.config.model_name,
            "task": self.config.task,
            "parameters": params,
            "classes": self.config.class_names,
            "num_classes": len(self.config.class_names),
            "imgsz": self.config.imgsz,
            "device": self.device,
            "frozen_backbone_layers": self.config.freeze_backbone_layers,
        }
