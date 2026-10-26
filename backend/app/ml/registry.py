# backend/app/ml/registry.py
"""
AEGIS AI - Vision Model Registry (Phase 15, W3)

Loads a trained ONNX model from the ``VISION_MODEL_PATH`` environment
variable and hands out ``TrainedDetector`` instances. When the env var
is unset, empty, or the file is missing, the registry reports
``has_model() == False`` and every caller degrades to heuristics —
honestly, and logged.

Design notes:
    - ``onnxruntime`` is imported lazily inside ``load_model()`` only.
      Importing this module (or the whole ``app.ml`` package) never
      requires it.
    - ``has_model()`` never loads the model — it is a cheap
      env-var + file-exists check suitable for health endpoints.
    - Singleton via ``get_registry()`` so the (heavy) session is
      created once per process; ``unload()`` releases it.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

from .trained_detector import (
    DEFAULT_CLASSES,
    KILLFEED_DEFAULT_CLASSES,
    TrainedDetector,
    TrainedDetectorConfig,
)

logger = structlog.get_logger("aegis.ml.registry")

VISION_MODEL_PATH_ENV = "VISION_MODEL_PATH"

# Which Phase-14 detector names a trained model can back, and which of
# the model's classes each one cares about. Unknown names → get_detector
# returns None so the caller falls back to the heuristic detector.
MODEL_DETECTOR_MAP: Dict[str, Optional[List[str]]] = {
    # Generic: all 7 fight classes (yolov5-style anchor model).
    "fight_detector": None,
    # Phase-14 knock detector's job, expressed with model classes.
    "knock_detector": [
        "player_knocked",
        "knock_animation",
        "blood_effect",
        "damage_indicator",
    ],
    # Phase-15 kill-feed model (YOLOv8, single-output export): the
    # Phase-14 OCR KillFeedDetector consults it FIRST for entry
    # localisation (see app/ml/vision_wiring.py).
    # Phase 33 W4 GATE — deliberately NOT flipped to "rfdetr": the
    # RF-DETR decode exists in trained_detector.py but is opt-in
    # only (explicit decode_mode="rfdetr" override). The YOLO26
    # export plugs into this yolov8 decode unchanged and is the
    # decodable fallback TODAY. Flip this entry only after a real
    # best.onnx passes the smoke test in the W4 report — never on
    # the strength of this patch alone.
    "killfeed": list(KILLFEED_DEFAULT_CLASSES),
}

# Per-detector decode configuration: the .onnx behind each name must be
# decoded the way its training pipeline exported it.
DETECTOR_MODEL_CONFIG: Dict[str, Dict[str, Any]] = {
    "fight_detector": {"decode_mode": "yolov5", "classes": list(DEFAULT_CLASSES)},
    "knock_detector": {"decode_mode": "yolov5", "classes": list(DEFAULT_CLASSES)},
    "killfeed": {"decode_mode": "yolov8", "classes": list(KILLFEED_DEFAULT_CLASSES)},
}


class VisionModelRegistry:
    """Registry for the optional trained vision model."""

    def __init__(self, model_dir: Optional[str] = None):
        self.model_dir = Path(
            model_dir or os.environ.get("AEGIS_MODEL_DIR", "backend/models")
        )
        self._session = None
        self._model_path: Optional[Path] = None
        self._onnxruntime_available: Optional[bool] = None

    # ── discovery ────────────────────────────────────────────────

    def resolve_model_path(self) -> Optional[Path]:
        """Path from VISION_MODEL_PATH, or None if unset/empty."""
        raw = os.environ.get(VISION_MODEL_PATH_ENV, "").strip().strip("\"'")
        if not raw:
            return None
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (self.model_dir / path).resolve()
        return path

    def has_model(self) -> bool:
        """
        Cheap check: env var set AND file exists. Never loads anything.
        """
        path = self.resolve_model_path()
        return path is not None and path.is_file()

    def is_loaded(self) -> bool:
        return self._session is not None

    def onnxruntime_available(self) -> bool:
        if self._onnxruntime_available is None:
            try:
                import onnxruntime  # noqa: F401

                self._onnxruntime_available = True
            except ImportError:
                self._onnxruntime_available = False
        return self._onnxruntime_available

    def get_status(self) -> Dict[str, object]:
        """Health-check friendly snapshot — no model loading."""
        path = self.resolve_model_path()
        return {
            "env_var": VISION_MODEL_PATH_ENV,
            "env_set": path is not None,
            "path": str(path) if path else None,
            "file_exists": bool(path and path.is_file()),
            "onnxruntime_available": self.onnxruntime_available(),
            "loaded": self.is_loaded(),
            "has_model": self.has_model(),
            "mode": "trained" if self.is_loaded() else "heuristic",
            "supported_detectors": sorted(MODEL_DETECTOR_MAP),
        }

    # ── lifecycle ────────────────────────────────────────────────

    def load_model(self, path: Optional[str] = None, **detector_overrides) -> bool:
        """
        Load the ONNX model into a shared InferenceSession.

        Returns True on success, False (logged) when the path is
        missing or onnxruntime is unavailable. Never raises for the
        "no model" case — that is a normal, supported configuration.
        """
        model_path = Path(path).expanduser() if path else self.resolve_model_path()
        if model_path is None or not model_path.is_file():
            logger.warning(
                "no_trained_model",
                detail=(
                    f"{VISION_MODEL_PATH_ENV} is unset or points to a missing file — "
                    "vision stays 100% heuristic"
                ),
                path=str(model_path) if model_path else None,
            )
            return False
        if not self.onnxruntime_available():
            logger.warning(
                "onnxruntime_missing",
                detail="install the backend 'ml' extra to use trained models",
            )
            return False

        import onnxruntime as ort

        self._session = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )
        self._model_path = model_path
        self._detector_overrides = detector_overrides
        logger.info(
            "trained_model_loaded",
            path=str(model_path),
            inputs=[i.name for i in self._session.get_inputs()],
            outputs=[o.name for o in self._session.get_outputs()],
        )
        return True

    def unload(self) -> None:
        self._session = None
        self._model_path = None
        logger.info("trained_model_unloaded")

    def refresh(self) -> Dict[str, object]:
        """Re-check env/file and unload a session whose file vanished."""
        if self._session is not None and not self.has_model():
            logger.warning("model_file_vanished", action="unloading_session")
            self.unload()
        return self.get_status()

    # ── detectors ────────────────────────────────────────────────

    def supported_detectors(self) -> List[str]:
        return sorted(MODEL_DETECTOR_MAP)

    def get_detector(self, name: str, **overrides) -> Optional[TrainedDetector]:
        """
        Return a TrainedDetector for ``name`` (see MODEL_DETECTOR_MAP),
        or None when there is no usable trained model — the caller must
        then fall back to its heuristic detector.

        Returns None (never raises) for unknown names and for the
        no-model case; logs both.
        """
        if name not in MODEL_DETECTOR_MAP:
            logger.debug("detector_not_supported_by_model", name=name)
            return None
        if not self.has_model():
            logger.debug(
                "no_model_for_detector",
                name=name,
                detail="heuristic fallback expected",
            )
            return None
        if self._session is None:
            if not self.load_model():
                return None

        class_filter = MODEL_DETECTOR_MAP[name]
        model_cfg = DETECTOR_MODEL_CONFIG.get(name, {})
        config = TrainedDetectorConfig(
            classes=list(model_cfg.get("classes", DEFAULT_CLASSES)),
            decode_mode=model_cfg.get("decode_mode", "yolov5"),
            # Forwarded generically so a future rfdetr entry CAN carry
            # normalize_imagenet/input_size; no current entry sets
            # them, so defaults (False / (640, 640)) apply today and
            # live behaviour is unchanged.
            normalize_imagenet=bool(model_cfg.get("normalize_imagenet", False)),
            input_size=tuple(model_cfg.get("input_size", (640, 640))),
            class_filter=class_filter,
        )
        for key, value in {**(getattr(self, "_detector_overrides", {})), **overrides}.items():
            if hasattr(config, key):
                setattr(config, key, value)

        return TrainedDetector(
            name=f"trained_{name}",
            model_path=str(self._model_path),
            session=self._session,
            config=config,
        )


_registry: Optional[VisionModelRegistry] = None


def get_registry() -> VisionModelRegistry:
    """Process-wide singleton registry."""
    global _registry
    if _registry is None:
        _registry = VisionModelRegistry()
    return _registry
