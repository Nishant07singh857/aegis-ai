# backend/app/vision/detectors/kill_feed_ocr.py
"""
AEGIS AI - Kill Feed OCR (FIXED & PRODUCTION READY)
Uses EasyOCR to read BGMI kill feed text from YOLO-detected bounding boxes.
Extracts killer name, victim name, weapon, and kill type in real-time.
"""

import re
import time
import cv2
import numpy as np
import structlog
from typing import Optional, List
from dataclasses import dataclass, field

logger = structlog.get_logger("aegis.vision.kill_feed_ocr")

# Lazy load EasyOCR so it only initializes when first used
_ocr_reader = None

def get_ocr_reader():
    """Lazy-load EasyOCR reader (English). Caches after first load."""
    global _ocr_reader
    if _ocr_reader is None:
        try:
            import easyocr
            logger.info("Initializing EasyOCR reader (English)...")
            _ocr_reader = easyocr.Reader(['en'], gpu=False, verbose=False)
            logger.info("EasyOCR ready.")
        except ImportError:
            logger.error("EasyOCR not installed! Run: pip install easyocr")
            _ocr_reader = None
    return _ocr_reader


@dataclass
class KillFeedEntry:
    """A single parsed kill feed entry."""
    killer_name: str = ""
    victim_name: str = ""
    weapon: str = ""
    kill_type: str = "knock"   # "knock" or "kill"
    headshot: bool = False
    confidence: float = 0.0
    raw_text: str = ""
    is_valid: bool = False
    timestamp: float = field(default_factory=time.time)


class KillFeedOCR:
    """
    PRODUCTION-READY OCR for BGMI Kill Feed.
    
    Workflow:
    1. YOLO detects the killfeed bounding box on screen
    2. We crop that region from the frame
    3. EasyOCR reads the text inside
    4. We parse: "SoulMortal [knocked] GodLJonathan"
    """

    # Common BGMI weapon names for validation
    KNOWN_WEAPONS = {
        "m416", "akm", "m762", "beryl", "groza", "aug", "qbz",
        "ump45", "uzi", "vector", "pp-19", "mp5k",
        "awm", "kar98k", "m24", "slr", "skk",
        "s686", "s12k", "dp-28", "mk14",
        "m79", "pan", "crossbow"
    }

    # OCR patterns to detect kill type
    KNOCK_KEYWORDS = ["knocked", "knock", "kd", "downed"]
    KILL_KEYWORDS = ["eliminated", "killed", "finished", "elim"]

    def __init__(self):
        # Cache last few entries to avoid duplicate events
        self._recent_entries: List[KillFeedEntry] = []
        self._max_cache = 10
        logger.info("KillFeedOCR initialized (EasyOCR backend)")

    def read_from_frame(
        self,
        frame: np.ndarray,
        bbox: Optional[tuple] = None
    ) -> Optional[KillFeedEntry]:
        """
        Main method: Crop the BGMI kill feed region (top-right corner) and OCR it.

        In BGMI/PUBG, kill feed is ALWAYS at top-right corner of screen.
        We use fixed region instead of YOLO bbox (which covers wrong area).

        Args:
            frame: Full game frame as numpy array (H, W, C)
            bbox: Ignored. We use fixed region for accuracy.

        Returns:
            KillFeedEntry if a valid kill/knock was detected, else None
        """
        reader = get_ocr_reader()
        if reader is None:
            return None

        try:
            h, w = frame.shape[:2]

            # BGMI Kill Feed is in top-right: x 68%-100%, y 3%-28% of screen
            x1 = int(w * 0.68)
            y1 = int(h * 0.03)
            x2 = w
            y2 = int(h * 0.28)

            region = frame[y1:y2, x1:x2]

            if region.size == 0:
                return None

            # Upscale small regions for better OCR accuracy
            scale = 2.0
            region = cv2.resize(region, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

            # Enhance contrast for white kill-feed text on dark background
            gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY)
            region_for_ocr = cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)

            # Run OCR
            results = reader.readtext(region_for_ocr, detail=1, paragraph=False,
                                      min_size=8, text_threshold=0.6)

            if not results:
                return None

            full_text = " ".join([r[1] for r in results]).strip()
            avg_confidence = sum([r[2] for r in results]) / len(results)

            if len(full_text) < 4:
                return None

            entry = self._parse_kill_text(full_text, avg_confidence)

            if entry and entry.is_valid:
                if not self._is_duplicate(entry):
                    self._recent_entries.append(entry)
                    if len(self._recent_entries) > self._max_cache:
                        self._recent_entries.pop(0)
                    logger.info(
                        "KillFeed OCR detected",
                        killer=entry.killer_name,
                        victim=entry.victim_name,
                        type=entry.kill_type,
                        raw=entry.raw_text[:50]
                    )
                    return entry

        except Exception as e:
            logger.error(f"OCR error: {e}")

        return None


    def _parse_kill_text(self, text: str, confidence: float) -> Optional[KillFeedEntry]:
        """
        Parse raw OCR text into structured kill feed data.
        
        BGMI kill feed format examples:
        - "SoulMortal knocked GodLJonathan"
        - "Sc0utOP eliminated TeamMate01"
        - "SoulMortal M416 GodLJonathan" (icon replaced by OCR as weapon name)
        """
        if not text or len(text) < 5:
            return None

        entry = KillFeedEntry(raw_text=text, confidence=confidence)
        text_lower = text.lower()

        # Detect kill type
        if any(k in text_lower for k in self.KNOCK_KEYWORDS):
            entry.kill_type = "knock"
        elif any(k in text_lower for k in self.KILL_KEYWORDS):
            entry.kill_type = "kill"
        else:
            entry.kill_type = "knock"  # Default assumption

        # Detect headshot
        entry.headshot = "headshot" in text_lower or "hs" in text_lower

        # Try structured regex parse: "Name1 [action] Name2"
        patterns = [
            r"([A-Za-z0-9_\-\.]+)\s+(knocked|eliminated|killed|finished|downed)\s+([A-Za-z0-9_\-\.]+)",
            r"([A-Za-z0-9_\-\.]{3,})\s+([A-Za-z0-9_\-\.]{3,})\s+([A-Za-z0-9_\-\.]{3,})",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                groups = match.groups()
                if len(groups) == 3:
                    entry.killer_name = groups[0].strip()
                    entry.victim_name = groups[2].strip()

                    # Check if middle part is a weapon
                    middle = groups[1].lower()
                    if any(w in middle for w in self.KNOWN_WEAPONS):
                        entry.weapon = groups[1].strip()
                    break

        # Validate: need at least killer and victim
        if entry.killer_name and entry.victim_name:
            if entry.killer_name.lower() != entry.victim_name.lower():
                entry.is_valid = True

        return entry

    def _is_duplicate(self, entry: KillFeedEntry, window_seconds: float = 3.0) -> bool:
        """Check if this kill was already emitted recently."""
        now = time.time()
        for recent in self._recent_entries:
            if now - recent.timestamp > window_seconds:
                continue
            if (recent.killer_name.lower() == entry.killer_name.lower() and
                    recent.victim_name.lower() == entry.victim_name.lower()):
                return True
        return False

    def clear_cache(self):
        """Clear recent entries cache."""
        self._recent_entries.clear()