# backend/app/ml/trained_detector.py
"""
AEGIS AI - Trained Model Detector (Phase 15, W3)

ONNX-runtime wrapper around the models produced by the Phase-15
training pipelines (``app/ml/fight_detection/export.py`` and
``app/ml/killfeed_detection/export.py``).

Drop-in replacement for the Phase-14 heuristic detectors: same
duck-type interface — ``async detect(frame, **kwargs)``,
``get_detection_regions()``, ``set_config/get_config``,
``get_last_result()``, ``get_stats()``, ``reset()`` — and results
use ``app.ml.detection_result`` types with the identical method
surface (``is_successful``, ``filter_by_confidence``, ``to_dict``,
``count``, ``get_best_detection``).

No top-level imports from ``app.vision`` (concurrent edits) and no
heavy imports at module load: ``onnxruntime`` and ``cv2`` are
imported lazily, only when a model is actually available. Without
them, ``detect()`` raises a clear ``RuntimeError`` — the
``ChainedDetector`` catches it and falls back to heuristics.

Expected model formats:
    - decode_mode "yolov5" (from fight_detection's ModelExporter):
        input name ``input``, shape (B, 3, 640, 640), RGB, float32, /255
        outputs ``output_p3`` (stride 8), ``output_p4`` (stride 16),
        ``output_p5`` (stride 32); each (B, 3*(4+num_classes), H, W)
        with YOLOv5-style anchor decoding and NO separate objectness
        score — confidence = max sigmoid class score.
        7 fight classes by default:
        player_shooting, player_knocked, explosion, muzzle_flash,
        blood_effect, damage_indicator, knock_animation
    - decode_mode "yolov8" (from killfeed_detection's KillFeedExporter,
        i.e. ultralytics YOLOv8 ``model.export(format="onnx")``):
        SINGLE output of shape (B, 4+num_classes, N) — rows 0..3 are
        DECODED xyxy box corners in model-input pixels, rows 4.. are
        the sigmoided class scores (ultralytics bakes the DFL box
        decode and the sigmoid into the exported graph; verified
        against ultralytics 8.4.182, the version pinned in
        training/requirements-train.txt). (B, N, 4+num_classes)
        transposed layouts are accepted too. Confidence = max class
        score; there is no objectness head in YOLOv8.
        2 kill-feed classes by default: killfeed_panel, killfeed_entry.

Honest limits (stated, not hidden):
    - decode_mode must match the model that produced the .onnx. The
      "yolov5" mode matches THIS repo's FightDetectionModel; the
      "yolov8" mode matches ultralytics-exported YOLOv8 weights
      (the Phase-15 kill-feed pipeline). A wrong decode yields
      boxes, not silence — verify visually on real frames.
    - No accuracy claims are made anywhere here; mAP lives in the
      training reports (app/ml/*/evaluator.py).
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import structlog

from .detection_result import DetectionBox, DetectionResult, DetectionStatus

logger = structlog.get_logger("aegis.ml.trained_detector")

DEFAULT_CLASSES = [
    "player_shooting",
    "player_knocked",
    "explosion",
    "muzzle_flash",
    "blood_effect",
    "damage_indicator",
    "knock_animation",
]

#: Classes of the Phase-15 kill-feed pipeline (killfeed_detection/):
#: ``killfeed_panel`` = whole feed region (OCR crop target),
#: ``killfeed_entry`` = one kill row (per-row OCR crop).
#: Order must match DatasetConfig.class_names in killfeed_detection/dataset.py.
KILLFEED_DEFAULT_CLASSES = [
    "killfeed_panel",
    "killfeed_entry",
]

# Anchors from FightDetectionModel config: (p3/stride8, p4/stride16, p5/stride32)
DEFAULT_ANCHORS = [
    [(10, 13), (16, 30), (33, 23)],       # P3 small
    [(30, 61), (62, 45), (59, 119)],      # P4 medium
    [(116, 90), (156, 198), (373, 326)],  # P5 large
]
DEFAULT_STRIDES = (8, 16, 32)
DEFAULT_INPUT_SIZE = (640, 640)  # (height, width)


@dataclass
class TrainedDetectorConfig:
    """Runtime knobs for TrainedDetector (overridable via set_config)."""

    classes: List[str] = field(default_factory=lambda: list(DEFAULT_CLASSES))
    anchors: List[List[Tuple[int, int]]] = field(
        default_factory=lambda: [list(a) for a in DEFAULT_ANCHORS]
    )
    strides: Tuple[int, int, int] = DEFAULT_STRIDES
    input_size: Tuple[int, int] = DEFAULT_INPUT_SIZE  # (h, w)
    conf_threshold: float = 0.25
    nms_iou: float = 0.45
    max_detections: int = 300
    class_filter: Optional[List[str]] = None  # None = keep all model classes
    decode_mode: str = "yolov5"  # "yolov5" | "yolov8" | "direct"
    #   yolov5 : anchor heads (B, 3*(4+C), H, W) x3 — fight_detection models
    #   yolov8 : ultralytics-exported single output (B, 4+C, N), decoded
    #            xyxy + sigmoided scores — killfeed_detection models
    #   direct : pre-decoded (B, N, 6) xyxy+conf+class_id passthrough
    onnx_providers: List[str] = field(default_factory=lambda: ["CPUExecutionProvider"])
    intra_op_threads: int = 0  # 0 = onnxruntime default


class TrainedDetector:
    """
    Duck-typed drop-in for Phase-14 detectors, backed by an ONNX model.

    Pass either an existing ``onnxruntime.InferenceSession`` or a
    ``model_path`` (loaded lazily on first ``detect``).
    """

    def __init__(
        self,
        name: str = "trained_detector",
        model_path: Optional[str] = None,
        session: Any = None,
        config: Optional[TrainedDetectorConfig] = None,
        **overrides: Any,
    ):
        self.name = name
        self.model_path = model_path
        self._session = session
        self.config = config or TrainedDetectorConfig()
        for key, value in overrides.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
            else:
                logger.warning("unknown_config_key", key=key, detector=name)

        self._total_detections = 0
        self._total_processing_time = 0.0
        self._last_result: Optional[DetectionResult] = None
        self._input_name: Optional[str] = None
        self._log = logger.bind(detector=name)

    # ── duck-type interface (mirrors BaseDetector) ──────────────────

    async def detect(self, frame: np.ndarray, **kwargs) -> DetectionResult:
        """
        Run the trained model on a BGR frame → DetectionResult.

        Raises:
            RuntimeError: no model loaded, onnxruntime/cv2 missing.
            ValueError: frame is not a valid BGR image.
        """
        start = time.time()

        if frame is None or not isinstance(frame, np.ndarray) or frame.ndim != 3:
            raise ValueError("frame must be a BGR np.ndarray with ndim == 3")

        conf_threshold = float(kwargs.get("conf_threshold", self.config.conf_threshold))
        nms_iou = float(kwargs.get("nms_iou", self.config.nms_iou))
        class_filter = kwargs.get("class_filter", self.config.class_filter)

        session = self._ensure_session()
        tensor, scale, pad = self._preprocess(frame)

        input_name = self._input_name or session.get_inputs()[0].name
        self._input_name = input_name
        outputs = session.run(None, {input_name: tensor})

        boxes, labels, confidences = self._decode_outputs(
            outputs, scale, pad,
            frame_width=frame.shape[1], frame_height=frame.shape[0],
            conf_threshold=conf_threshold, nms_iou=nms_iou,
            class_filter=class_filter,
        )

        elapsed_ms = (time.time() - start) * 1000
        self._total_detections += 1
        self._total_processing_time += elapsed_ms

        result = DetectionResult(
            detector_name=self.name,
            status=DetectionStatus.SUCCESS if boxes else DetectionStatus.NOT_FOUND,
            boxes=boxes,
            labels=labels,
            confidences=confidences,
            frame_width=frame.shape[1],
            frame_height=frame.shape[0],
            processing_time_ms=elapsed_ms,
            extra_data={
                "source": "trained_model",
                "model": self._model_label(),
                "conf_threshold": conf_threshold,
                "nms_iou": nms_iou,
            },
        )
        self._last_result = result
        self._log.debug("detect", count=len(boxes), ms=round(elapsed_ms, 1))
        return result

    def get_detection_regions(
        self, frame_width: int, frame_height: int
    ) -> List[DetectionBox]:
        """The trained model scans the full frame (letterboxed internally)."""
        return [
            DetectionBox(
                x=0.0, y=0.0,
                width=float(frame_width), height=float(frame_height),
                confidence=1.0, label="full_frame",
            )
        ]

    def set_config(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
            else:
                raise ValueError(f"unknown config key: {key}")

    def get_config(self) -> Dict[str, Any]:
        return {
            "classes": list(self.config.classes),
            "strides": list(self.config.strides),
            "input_size": list(self.config.input_size),
            "conf_threshold": self.config.conf_threshold,
            "nms_iou": self.config.nms_iou,
            "max_detections": self.config.max_detections,
            "class_filter": self.config.class_filter,
            "decode_mode": self.config.decode_mode,
            "model_path": self.model_path,
        }

    def get_last_result(self) -> Optional[DetectionResult]:
        return self._last_result

    def get_stats(self) -> Dict[str, Any]:
        avg = (
            self._total_processing_time / max(1, self._total_detections)
            if self._total_detections > 0
            else 0.0
        )
        return {
            "detector": self.name,
            "backend": "trained_onnx",
            "model": self._model_label(),
            "total_detections": self._total_detections,
            "avg_processing_time_ms": round(avg, 2),
            "last_result": self._last_result.to_dict() if self._last_result else None,
        }

    def reset(self) -> None:
        self._total_detections = 0
        self._total_processing_time = 0.0
        self._last_result = None
        self._log.debug("reset")

    def close(self) -> None:
        """Release the ONNX session (safe to call multiple times)."""
        self._session = None
        self._input_name = None

    # ── session handling (lazy) ─────────────────────────────────────

    def _model_label(self) -> str:
        if self.model_path:
            return self.model_path.rsplit("/", 1)[-1]
        return "in-memory-session"

    def _ensure_session(self) -> Any:
        if self._session is not None:
            return self._session
        if not self.model_path:
            raise RuntimeError(
                f"{self.name}: no trained model loaded (no session, no model_path)"
            )
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError(
                "onnxruntime is not installed — install the backend 'ml' extra "
                "(pip install '.[ml]') to run trained models"
            ) from exc

        sess_options = ort.SessionOptions()
        if self.config.intra_op_threads > 0:
            sess_options.intra_op_num_threads = self.config.intra_op_threads
        self._session = ort.InferenceSession(
            self.model_path,
            sess_options=sess_options,
            providers=self.config.onnx_providers,
        )
        self._log.info("model_loaded", path=self.model_path)
        return self._session

    # ── preprocessing ───────────────────────────────────────────────

    def _preprocess(self, frame: np.ndarray) -> Tuple[np.ndarray, float, Tuple[float, float]]:
        """
        Letterbox BGR frame to input_size → (1,3,H,W) float32 RGB /255.
        Returns (tensor, scale, (pad_x, pad_y)).
        """
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError(
                "opencv (cv2) is not installed — install the backend 'ml' extra "
                "to run trained models"
            ) from exc

        target_h, target_w = self.config.input_size
        h, w = frame.shape[:2]
        scale = min(target_w / w, target_h / h)
        new_w, new_h = int(round(w * scale)), int(round(h * scale))

        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        pad_x = (target_w - new_w) / 2.0
        pad_y = (target_h - new_h) / 2.0
        canvas = np.full((target_h, target_w, 3), 114, dtype=np.uint8)
        canvas[int(round(pad_y)):int(round(pad_y)) + new_h,
               int(round(pad_x)):int(round(pad_x)) + new_w] = resized

        rgb = canvas[:, :, ::-1]  # BGR → RGB
        tensor = rgb.transpose(2, 0, 1).astype(np.float32) / 255.0
        return tensor[None], scale, (pad_x, pad_y)

    # ── decoding ────────────────────────────────────────────────────

    def _decode_outputs(
        self,
        outputs: List[np.ndarray],
        scale: float,
        pad: Tuple[float, float],
        frame_width: int,
        frame_height: int,
        conf_threshold: float,
        nms_iou: float,
        class_filter: Optional[List[str]],
    ) -> Tuple[List[DetectionBox], List[str], List[float]]:
        if self.config.decode_mode == "direct":
            all_preds = [self._decode_direct(o) for o in outputs]
        elif self.config.decode_mode == "yolov8":
            all_preds = [self._decode_yolov8(o) for o in outputs]
        else:
            all_preds = [
                self._decode_yolov5_head(out, anchors, stride)
                for out, anchors, stride in zip(outputs, self.config.anchors, self.config.strides)
            ]
        # each pred: (N, 6) → x1,y1,x2,y2,conf,class_id (model-input coords)
        preds = np.concatenate(all_preds, axis=0) if all_preds else np.zeros((0, 6))

        keep_classes = None
        if class_filter:
            keep_classes = {self.config.classes.index(c) for c in class_filter
                            if c in self.config.classes}

        boxes: List[DetectionBox] = []
        labels: List[str] = []
        confidences: List[float] = []

        if preds.shape[0] == 0:
            return boxes, labels, confidences

        for class_id in np.unique(preds[:, 5].astype(int)):
            cid = int(class_id)
            if keep_classes is not None and cid not in keep_classes:
                continue
            cls_preds = preds[preds[:, 5] == class_id]
            cls_preds = cls_preds[cls_preds[:, 4] >= conf_threshold]
            if cls_preds.shape[0] == 0:
                continue
            for idx in self._nms(cls_preds[:, :4], cls_preds[:, 4], nms_iou):
                x1, y1, x2, y2 = cls_preds[idx, :4]
                # undo letterbox → frame coords
                fx1 = max(0.0, (x1 - pad[0]) / scale)
                fy1 = max(0.0, (y1 - pad[1]) / scale)
                fx2 = min(float(frame_width), (x2 - pad[0]) / scale)
                fy2 = min(float(frame_height), (y2 - pad[1]) / scale)
                if fx2 <= fx1 or fy2 <= fy1:
                    continue
                conf = float(cls_preds[idx, 4])
                label = self.config.classes[cid] if cid < len(self.config.classes) else f"class_{cid}"
                boxes.append(DetectionBox(
                    x=fx1, y=fy1, width=fx2 - fx1, height=fy2 - fy1,
                    confidence=conf, label=label,
                ))
                labels.append(label)
                confidences.append(conf)

        # cap + sort by confidence desc
        order = np.argsort([-b.confidence for b in boxes])[: self.config.max_detections]
        boxes = [boxes[i] for i in order]
        labels = [labels[i] for i in order]
        confidences = [confidences[i] for i in order]
        return boxes, labels, confidences

    @staticmethod
    def _sigmoid(x: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-x))

    def _decode_yolov5_head(
        self, head: np.ndarray, anchors: List[Tuple[int, int]], stride: int
    ) -> np.ndarray:
        """
        Decode one YOLOv5-style anchor head: (B, 3*(4+C), H, W)
        → (N, 6) [x1,y1,x2,y2,conf,class_id] in model-input pixels.
        """
        b, _, gh, gw = head.shape
        num_classes = len(self.config.classes)
        head = head.reshape(b, 3, 4 + num_classes, gh, gw).transpose(0, 1, 3, 4, 2)
        pred = head[0]  # batch 0

        grid_y, grid_x = np.meshgrid(np.arange(gh), np.arange(gw), indexing="ij")
        grid = np.stack([grid_x, grid_y], axis=-1)[None, None]  # (1,1,gh,gw,2)

        xy = (self._sigmoid(pred[..., 0:2]) * 2.0 - 0.5 + grid) * stride
        anchor = np.array(anchors, dtype=np.float32).reshape(1, 3, 1, 1, 2)
        wh = (self._sigmoid(pred[..., 2:4]) * 2.0) ** 2 * anchor

        cls_scores = self._sigmoid(pred[..., 4:])
        class_id = cls_scores.argmax(axis=-1)
        conf = cls_scores.max(axis=-1)

        x1y1 = xy - wh / 2.0
        x2y2 = xy + wh / 2.0
        out = np.concatenate(
            [x1y1, x2y2, conf[..., None], class_id[..., None].astype(np.float32)],
            axis=-1,
        )
        return out.reshape(-1, 6)

    def _decode_yolov8(self, out: np.ndarray) -> np.ndarray:
        """
        Decode an ultralytics-exported YOLOv8 ONNX output.

        The exported graph (``model.export(format="onnx")``) emits ONE
        tensor of shape (B, 4+num_classes, N): rows 0..3 are the DECODED
        xyxy box corners in model-input pixels, rows 4.. are the
        sigmoided class scores. Verified against ultralytics 8.4.182
        (the version pinned in training/requirements-train.txt).
        (B, N, 4+num_classes) transposed layouts are accepted too.

        Returns (N, 6) [x1,y1,x2,y2,conf,class_id] in model-input pixels.
        """
        arr = np.asarray(out)
        if arr.ndim != 3 or arr.shape[0] < 1:
            raise ValueError(
                f"yolov8 decode expects a (B, 4+C, N) or (B, N, 4+C) "
                f"tensor, got shape {arr.shape}"
            )
        num_classes = len(self.config.classes)
        want = 4 + num_classes
        head = arr[0]  # batch 0
        if head.shape[0] == want:
            pass  # (4+C, N) — the standard ultralytics layout
        elif head.shape[-1] == want:
            head = head.T  # (N, 4+C) → (4+C, N)
        else:
            raise ValueError(
                f"yolov8 decode: no axis of length {want} (4+{num_classes} "
                f"classes) in output shape {arr.shape} — wrong decode_mode "
                f"or wrong model?"
            )
        boxes_xyxy = head[0:4, :].T          # (N, 4)
        scores = head[4:4 + num_classes, :].T  # (N, C), already sigmoided
        class_id = scores.argmax(axis=1)
        conf = scores.max(axis=1)
        out6 = np.concatenate(
            [boxes_xyxy,
             conf[:, None],
             class_id[:, None].astype(np.float32)],
            axis=1,
        )
        return out6

    def _decode_direct(self, head: np.ndarray) -> np.ndarray:
        """Passthrough decode: head already (B, N, 6) xyxy+conf+class_id."""
        return np.asarray(head[0]).reshape(-1, 6)

    @staticmethod
    def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> List[int]:
        """Classic per-call NMS (boxes already single-class)."""
        if boxes.shape[0] == 0:
            return []
        x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
        order = scores.argsort()[::-1]
        keep: List[int] = []
        while order.size > 0:
            i = int(order[0])
            keep.append(i)
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
            iou = inter / np.maximum(areas[i] + areas[order[1:]] - inter, 1e-9)
            order = order[1:][iou <= iou_threshold]
        return keep
