# backend/app/services/feed_adapter/vision_adapter.py
"""Vision-pipeline feed adapter (Phase 14 W4 integration).

Exposes the Phase 14 vision pipeline (device screen capture -> OCR/HUD
detectors -> NormalizedEvent) as a first-class feed source with
``adapter_id == "vision"``.

Lifecycle is OPERATOR-GATED, never automatic:
    POST /api/v1/feed/vision/start  -> connect()  (starts capture + loop)
    POST /api/v1/feed/vision/stop   -> disconnect() (stops everything)

``connect()`` lazily imports ``app.vision.runtime`` so that a machine
without the vision stack (no cv2/tesseract) can still import this module
and run the rest of the backend: the import failure becomes a clear
RuntimeError surfaced as HTTP 503, never a crashed process.

All detector output reaches the event bus through BaseFeedAdapter.ingest()
-- the same path as every other feed source -- so replay buffer,
highlight detection, and the kill feed consume vision events unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, TYPE_CHECKING

import structlog

from .base import BaseFeedAdapter

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.event_bus import EventBus
    from app.vision.runtime import VisionCaptureService

logger = structlog.get_logger("aegis.feed_adapter.vision")


class VisionFeedAdapter(BaseFeedAdapter):
    """Feed adapter backed by the Phase 14 vision pipeline."""

    adapter_id = "vision"
    name = "Vision pipeline (device capture + OCR/HUD readers)"

    def __init__(self, bus: Optional["EventBus"] = None) -> None:
        super().__init__(bus)
        self._service: Optional["VisionCaptureService"] = None

    async def connect(self, match_id: Optional[str] = None) -> None:
        """Start the vision capture service.

        Raises RuntimeError when the vision stack cannot be imported
        (missing cv2/tesseract) -- the caller maps this to HTTP 503.
        Safe to call when already connected (no-op).
        """
        if self._service is not None and self._service.running:
            logger.info("vision_adapter_already_connected")
            return
        try:
            from app.vision.runtime import VisionCaptureService
        except ImportError as exc:
            raise RuntimeError(
                "Vision pipeline unavailable on this machine: "
                f"could not import app.vision.runtime ({exc}). "
                "Install opencv-python and tesseract-ocr (both free)."
            ) from exc
        self._service = VisionCaptureService(
            ingest=self.ingest, match_id=match_id
        )
        await self._service.start()
        self._connected = True
        logger.info(
            "vision_adapter_connected",
            backend=self._service.config.backend,
            match_id=self._service.status().get("match_id"),
        )

    async def disconnect(self) -> None:
        """Stop the vision capture service. Idempotent."""
        service, self._service = self._service, None
        if service is not None:
            await service.stop()
        self._connected = False
        logger.info("vision_adapter_disconnected")

    @property
    def service(self) -> Optional["VisionCaptureService"]:
        return self._service

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base["operator_gated"] = True
        base["note"] = (
            "Start with POST /api/v1/feed/vision/start (operator). "
            "When idle, no frames are captured and no events are emitted."
        )
        if self._service is not None:
            base["vision"] = self._service.status()
        else:
            base["vision"] = {"running": False}
        return base
