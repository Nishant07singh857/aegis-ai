# backend/app/vision/models/trained_registration.py
"""
AEGIS AI - Trained-Model Registration Guards (Phase 22 / W3, M3).

Two plug-ready wirings, both gated on the SAME honesty rule: generic
COCO weights are NOT BGMI detection and must never be presented as such.

1. YOLOv8 service registration
   --------------------------
   ``resolve_yolo_registration()`` returns a ready ``YOLOService`` ONLY
   when a BGMI-trained model path is configured:

   * ``YOLO_TRAINED_MODEL_PATH`` env, or
   * ``VISION_MODEL_PATH`` env pointing at a ``.pt`` weights file, or
   * ``settings.YOLO_MODEL_PATH`` when it exists on disk AND its name
     marks it as trained (contains ``bgmi``).

   The bundled ``yolov8n.pt`` (COCO) is explicitly refused. Until the
   Kaggle-trained BGMI model lands, registration returns ``None`` with
   a reason — the live runtime keeps using the Phase 14/15 path
   (kill-feed ONNX hybrid + OCR), unchanged.

2. ByteTrack behind position tracking
   ---------------------------------
   ``PositionTrackerAdapter`` wraps ``ByteTrackService``. It consumes
   detection boxes (from a future trained detector / minimap positions)
   and returns tracks. With no detection producer it is fed nothing
   and returns no tracks — plug-ready, never fabricating positions.
   ``boxmot`` is an optional dependency; the service's IOU fallback
   keeps the adapter functional without it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger("aegis.vision.trained_registration")

#: Env var naming the BGMI-trained YOLO weights (preferred over the
#: generic ONNX-oriented VISION_MODEL_PATH, which may hold either).
YOLO_TRAINED_ENV = "YOLO_TRAINED_MODEL_PATH"


def coco_weights_refused(path: str) -> bool:
    """True when ``path`` names generic COCO weights (e.g. yolov8n.pt).

    COCO knows 'person'/'truck'; it cannot read BGMI UI, players or
    vehicles. Registering it as the BGMI detector would be a lie, so
    stock ultralytics names (yolov8n/s/m/l/x, yolo11*, incl. -seg /
    -pose / -cls variants) are refused UNLESS the name carries a
    trained marker (``bgmi`` — e.g. the settings default
    ``yolov8m_bgmi.pt`` is a BGMI fine-tune, not stock COCO).
    """
    import re

    stem = Path(path).stem.lower()
    if "bgmi" in stem or "killfeed" in stem:
        return False
    return bool(re.match(r"^yolo(v?8|11)[nsmlx](-|$)", stem))


def resolve_trained_yolo_path() -> Optional[str]:
    """The configured BGMI-trained YOLO path, or None (honest)."""
    for env_name in (YOLO_TRAINED_ENV, "VISION_MODEL_PATH"):
        raw = (os.environ.get(env_name) or "").strip()
        if raw and raw.lower().endswith(".pt") and not coco_weights_refused(raw):
            return raw
    try:
        from app.config import settings

        configured = str(getattr(settings, "YOLO_MODEL_PATH", "") or "")
    except Exception:  # pragma: no cover - config unavailable in tools
        configured = ""
    if configured and "bgmi" in Path(configured).name.lower():
        if Path(configured).exists() and not coco_weights_refused(configured):
            return configured
    return None


def resolve_yolo_registration() -> Dict[str, Any]:
    """Registration decision for the live runtime.

    Returns ``{"registered": bool, "service": YOLOService|None,
    "model_path": str|None, "reason": str}``. ``registered`` is True
    only with a real trained model file on disk.
    """
    path = resolve_trained_yolo_path()
    if path is None:
        return {
            "registered": False,
            "service": None,
            "model_path": None,
            "reason": (
                "no BGMI-trained YOLO model configured "
                f"({YOLO_TRAINED_ENV}/VISION_MODEL_PATH unset or COCO "
                "weights refused); live detection stays on the "
                "kill-feed ONNX hybrid + OCR path"
            ),
        }
    if not Path(path).exists():
        return {
            "registered": False,
            "service": None,
            "model_path": path,
            "reason": f"trained model path configured but file missing: {path}",
        }
    try:
        from .yolo_service import YOLOConfig, YOLOService

        service = YOLOService(YOLOConfig(model_path=path))
    except Exception as exc:  # ultralytics missing / bad weights
        return {
            "registered": False,
            "service": None,
            "model_path": path,
            "reason": f"YOLOService construction failed: {exc}",
        }
    logger.info("yolo_service_registered_trained", model_path=path)
    return {
        "registered": True,
        "service": service,
        "model_path": path,
        "reason": "BGMI-trained model present; YOLO service registered",
    }


class PositionTrackerAdapter:
    """ByteTrack behind position tracking (plug-ready).

    Feed it detection dicts ``{"bbox": (x, y, w, h), "confidence": f,
    "class_id": int, "class_name": str}`` per frame via :meth:`update`;
    it returns the tracker's serialised tracks. Until a trained
    detector supplies detections, callers simply never call ``update``
    (or call it with ``[]``, which yields no tracks).
    """

    def __init__(self, service: Optional[Any] = None) -> None:
        self._service = service
        self._service_error: Optional[str] = None

    def _ensure_service(self) -> Optional[Any]:
        if self._service is not None:
            return self._service
        if self._service_error is not None:
            return None
        try:
            from .bytetrack_service import ByteTrackService

            self._service = ByteTrackService()
        except Exception as exc:  # pragma: no cover - import guard
            self._service_error = str(exc)
        return self._service

    @property
    def available(self) -> bool:
        return self._ensure_service() is not None

    def update(self, detections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Advance tracking by one frame; returns serialised tracks."""
        service = self._ensure_service()
        if service is None or not detections:
            return []
        try:
            result = service.update(detections, frame_shape=(1920, 1080))
        except Exception as exc:
            logger.warning("bytetrack_update_failed", error=str(exc)[:200])
            return []
        objects = getattr(result, "tracked_objects", []) or []
        return [o.to_dict() for o in objects if getattr(o, "is_active", True)]

    def status(self) -> Dict[str, Any]:
        return {
            "tracker": "bytetrack",
            "available": self.available(),
            "error": self._service_error,
            "note": (
                "plug-ready: produces tracks only when a detection "
                "producer (trained model) feeds it boxes"
            ),
        }
