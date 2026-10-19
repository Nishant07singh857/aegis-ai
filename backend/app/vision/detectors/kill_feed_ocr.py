# backend/app/vision/detectors/kill_feed_ocr.py
"""
DEPRECATED shim (Phase 14 W2).

The production kill-feed detector now lives in
``backend/app/vision/detectors/killfeed.py`` (``KillFeedDetector``:
Tesseract OCR, relative kill-feed region, 30 s TTL dedup, emits
``NormalizedEvent``).

This module is kept only so existing imports keep working. It re-exports
the new implementation under the old names; there is no duplicated logic.
"""

from .killfeed import (  # noqa: F401
    KNOWN_WEAPON_TEXT,
    DEFAULT_KILL_FEED_REGION,
    KillFeedDetector,
    KillFeedEntry,
)

# Backwards-compatible aliases for the pre-Phase-14 API.
KillFeedOCR = KillFeedDetector
KNOWN_WEAPONS = KNOWN_WEAPON_TEXT

__all__ = [
    "KillFeedOCR",
    "KillFeedDetector",
    "KillFeedEntry",
    "KNOWN_WEAPONS",
    "KNOWN_WEAPON_TEXT",
    "DEFAULT_KILL_FEED_REGION",
]
