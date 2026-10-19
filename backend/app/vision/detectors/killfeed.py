# backend/app/vision/detectors/killfeed.py
"""
AEGIS AI - Kill Feed Detector (production module, Phase 14 W2).

Detects kill-feed entries in BGMI spectator frames using Tesseract OCR on
the top-right kill-feed region, parses killer/victim names, deduplicates
across frames, and emits ``NormalizedEvent`` objects (kill/knock) matching
``backend/app/services/feed_adapter/schemas.py``.

OCR ENGINE CHOICE (documented, honest):
    * Tesseract (via ``pytesseract``) is the primary engine.
      - Free, fully offline, ~5 MB, CPU-light, no model download.
      - Reads clean high-contrast kill-feed text (white on dark translucent
        background) well when pre-processed (upscale + threshold + PSM 6).
    * EasyOCR was NOT chosen: it requires a ~100 MB model download on first
      use plus a torch dependency, is much slower on CPU, and is not
      installed in this environment. The previous ``kill_feed_ocr.py``
      claimed an EasyOCR backend that cannot even be imported here.
    * Neither engine is bundled: Tesseract is imported lazily and every
      code path degrades gracefully (``ocr_available() == False``,
      ``parse_frame`` returns ``[]``) if the ``tesseract`` binary or
      ``pytesseract`` is missing. We NEVER emit a fake detection.

WEAPON ICONS:
    Kill-feed weapons are icons, not text. Template matching them would
    require real captured templates that do not exist in this VM, so per
    the "never guess" rule the weapon is always left as ``None``
    ("unknown") — icons are never matched or guessed.

DEDUP (critical):
    The same kill-feed line is visible for several seconds, i.e. across
    hundreds of frames. Each parsed entry is keyed by
    ``(killer.lower(), victim.lower(), event_type)`` and remembered for
    ``dedup_ttl_sec`` (default 30 s). Only the first sighting of a key
    within the TTL is emitted. The table is bounded (default 512 keys) and
    purged on every insertion so memory cannot grow unboundedly.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import structlog

from .base_detector import (
    BaseDetector,
    DetectionBox,
    DetectionResult,
    DetectionStatus,
)

log = structlog.get_logger("aegis.vision.killfeed")

# NormalizedEvent import is deferred so this module stays importable even if
# pydantic is missing at vision-module import time; we import on demand.
_NormalizedEvent = None
_normalized_event_lock = threading.Lock()


def _normalized_event_cls():
    global _NormalizedEvent
    if _NormalizedEvent is not None:
        return _NormalizedEvent
    # Serialized: two detector worker threads racing the first import can
    # otherwise deadlock on the module import lock.
    with _normalized_event_lock:
        if _NormalizedEvent is not None:
            return _NormalizedEvent
        try:
            from ...services.feed_adapter.schemas import (  # type: ignore
                FeedEventType,
                NormalizedEvent,
            )
        except ImportError:  # pragma: no cover - repo-layout guard
            from app.services.feed_adapter.schemas import FeedEventType, NormalizedEvent  # type: ignore
        _NormalizedEvent = (FeedEventType, NormalizedEvent)
    return _NormalizedEvent


# --------------------------------------------------------------------------
# Region / OCR constants
# --------------------------------------------------------------------------

# Kill-feed region in BGMI spectator UI: top-right corner, expressed as
# fractions of frame width/height so it works on any capture resolution.
# (x1, y1, x2, y2) — override per device via set_config(kill_feed_region=...).
DEFAULT_KILL_FEED_REGION: Tuple[float, float, float, float] = (0.62, 0.02, 0.99, 0.30)

# Per-line band: when we cannot rely on Tesseract line numbers we re-run
# PSM-8 per horizontal band. Kept for documentation; current pipeline uses
# image_to_data line grouping.
_TESSERACT_CONFIG_LINE = "--oem 3 --psm 6"
_TESSERACT_CONFIG_WORD = "--oem 3 --psm 8"

#: Weapon names reference list (kept for documentation; the detector does
#: NOT template-match weapon icons — see module docstring).
KNOWN_WEAPON_TEXT = {
    "m416", "akm", "m762", "beryl", "groza", "aug", "qbz",
    "ump45", "uzi", "vector", "pp19", "pp-19", "mp5k",
    "awm", "kar98k", "kar98", "m24", "slr", "sks", "mini14", "mk14",
    "s686", "s12k", "dp-28", "dp28", "m249", "m79", "pan",
    "crossbow", "vss", "win94", "scar-l", "scar", "famas",
}

# Action-word anchor: word boundaries keep a killer literally named
# "SkillzOP" from being misclassified by the "kill" substring.
_ACTION_RE = re.compile(
    r"\b(?P<action>knocked|eliminated|killed|finished|downed|knock|elim|kill|finish|ko)\b",
    re.IGNORECASE,
)
_KILL_ACTIONS = {"eliminated", "elim", "killed", "kill", "finished", "finish"}


@dataclass
class KillFeedEntry:
    """One parsed kill-feed line (intermediate representation)."""

    killer: str = ""
    victim: str = ""
    event_type: str = "knock"  # "kill" or "knock"
    weapon: Optional[str] = None
    headshot: bool = False
    confidence: float = 0.0
    raw_text: str = ""
    is_valid: bool = False
    seen_at: float = field(default_factory=time.time)

    def dedup_key(self) -> str:
        """Stable identity for cross-frame deduplication."""
        base = f"{self.killer.strip().lower()}|{self.victim.strip().lower()}|{self.event_type}"
        return hashlib.sha1(base.encode("utf-8")).hexdigest()


class KillFeedDetector(BaseDetector):
    """
    Production kill-feed detector.

    Usage:
        det = KillFeedDetector()
        if det.ocr_available():
            events = det.parse_frame(frame_bgr, match_id="m1")  # List[NormalizedEvent]
            # or the async BaseDetector contract:
            result = await det.detect(frame_bgr, match_id="m1")
    """

    def __init__(self, name: str = "killfeed", **config):
        super().__init__(name=name)
        self.config = {
            "kill_feed_region": DEFAULT_KILL_FEED_REGION,
            "dedup_ttl_sec": 30.0,
            "dedup_max_keys": 512,
            "upscale": 2.0,
            "min_text_len": 4,
            # Skip OCR entirely if the region is nearly uniform (blank HUD).
            "blank_std_threshold": 4.0,
            # Minimum mean word-confidence to accept a parsed entry.
            "min_word_confidence": 30.0,
        }
        self.config.update(config)
        self._seen: Dict[str, float] = {}  # dedup_key -> first seen epoch
        self._tesseract_ok: Optional[bool] = None
        self._log = log.bind(detector=name)

    # ------------------------------------------------------------------
    # BaseDetector contract
    # ------------------------------------------------------------------

    def get_detection_regions(
        self, frame_width: int, frame_height: int
    ) -> List[DetectionBox]:
        x1r, y1r, x2r, y2r = self.config["kill_feed_region"]
        return [
            DetectionBox(
                x=x1r,
                y=y1r,
                width=max(0.0, x2r - x1r),
                height=max(0.0, y2r - y1r),
                confidence=1.0,
                label="kill_feed",
            )
        ]

    async def detect(self, frame: np.ndarray, **kwargs) -> DetectionResult:
        """Async BaseDetector entry point. Returns per-event detections."""
        started = time.time()
        h, w = (frame.shape[0], frame.shape[1]) if frame is not None else (0, 0)
        events = self.parse_frame(
            frame, match_id=kwargs.get("match_id"), now=kwargs.get("now")
        )
        boxes, labels, confs = [], [], []
        for ev in events:
            region = self.get_detection_regions(w, h)[0]
            boxes.append(region)
            labels.append(ev.event_type)
            confs.append(float(ev.payload.get("ocr_confidence", 0.0)) / 100.0)
        status = (
            DetectionStatus.SUCCESS
            if events
            else (
                DetectionStatus.NOT_FOUND
                if self.ocr_available()
                else DetectionStatus.SKIPPED
            )
        )
        result = DetectionResult(
            detector_name=self.name,
            status=status,
            frame_width=w,
            frame_height=h,
            processing_time_ms=self._time_detection(started),
            boxes=boxes,
            labels=labels,
            confidences=confs,
            extra_data={
                "events": [ev.model_dump() for ev in events],
                "ocr_available": self.ocr_available(),
            },
        )
        self._last_result = result
        return result

    # ------------------------------------------------------------------
    # OCR plumbing
    # ------------------------------------------------------------------

    def ocr_available(self) -> bool:
        """True when pytesseract + the tesseract binary are usable."""
        if self._tesseract_ok is None:
            try:
                import pytesseract

                pytesseract.get_tesseract_version()
                self._tesseract_ok = True
            except Exception as exc:  # ImportError, TesseractNotFoundError, ...
                self._log.warning("tesseract unavailable", error=str(exc))
                self._tesseract_ok = False
        return self._tesseract_ok

    def _crop_region(self, frame: np.ndarray) -> Optional[np.ndarray]:
        if frame is None or frame.ndim < 2:
            return None
        h, w = frame.shape[:2]
        x1r, y1r, x2r, y2r = self.config["kill_feed_region"]
        x1, y1 = int(w * x1r), int(h * y1r)
        x2, y2 = int(w * x2r), int(h * y2r)
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[max(0, y1):y2, max(0, x1):x2]

    def _preprocess(self, region: np.ndarray):
        """BGR region -> high-contrast PIL image ready for Tesseract."""
        from PIL import Image

        # BGR -> grayscale via luminance.
        if region.ndim == 3:
            b, g, r = region[:, :, 0], region[:, :, 1], region[:, :, 2]
            gray = (0.114 * b + 0.587 * g + 0.299 * r).astype(np.uint8)
        else:
            gray = region.astype(np.uint8)

        img = Image.fromarray(gray, mode="L")
        scale = float(self.config["upscale"])
        if scale != 1.0:
            img = img.resize(
                (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                Image.BILINEAR,
            )
        arr = np.asarray(img).astype(np.float32)

        # Blank-region fast path: kill-feed text is bright on a dark
        # translucent strip; a nearly uniform crop has no text.
        if float(arr.std()) < float(self.config["blank_std_threshold"]):
            return None

        # Otsu threshold for bright text on dark background.
        hist, _ = np.histogram(arr, bins=256, range=(0, 255))
        total = arr.size
        sum_all = np.dot(np.arange(256), hist)
        sum_b, w_b, best, thresh = 0.0, 0.0, -1.0, 128
        for t in range(256):
            w_b += hist[t]
            if w_b == 0:
                continue
            w_f = total - w_b
            if w_f == 0:
                break
            sum_b += t * hist[t]
            m_b = sum_b / w_b
            m_f = (sum_all - sum_b) / w_f
            var_between = w_b * w_f * (m_b - m_f) ** 2
            if var_between > best:
                best, thresh = var_between, t
        binary = np.where(arr > thresh, 255, 0).astype(np.uint8)
        # Kill-feed text is white; if most pixels are white we inverted it.
        if float(binary.mean()) > 127.0:
            binary = 255 - binary
        return Image.fromarray(binary, mode="L")

    def _ocr_lines(self, image) -> List[Tuple[str, float]]:
        """Run Tesseract, group words into text lines with mean confidence."""
        import pytesseract

        data = pytesseract.image_to_data(
            image, config=_TESSERACT_CONFIG_LINE, output_type=pytesseract.Output.DATAFRAME
        )
        lines: Dict[Tuple[int, int, int], List[Tuple[str, float]]] = {}
        for row in data.itertuples():
            text = str(getattr(row, "text", "") or "").strip()
            if not text:
                continue
            try:
                conf = float(getattr(row, "conf", -1))
            except (TypeError, ValueError):
                conf = -1.0
            key = (int(row.block_num), int(row.par_num), int(row.line_num))
            lines.setdefault(key, []).append((text, conf))
        out = []
        for words in lines.values():
            text = " ".join(w for w, _ in words)
            confs = [c for _, c in words if c >= 0]
            mean_conf = sum(confs) / len(confs) if confs else 0.0
            if len(text) >= int(self.config["min_text_len"]):
                out.append((text, mean_conf))
        return out

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_name(raw: str) -> str:
        """Strip OCR junk from a name token; '' if not name-like."""
        name = raw.strip().strip(".,:;!?\"'()[]{}|")
        if len(name) < 2 or len(name) > 32:
            return ""
        if not re.fullmatch(r"[A-Za-z0-9_.\-·~^' ]+", name):
            return ""
        return name

    def parse_text(self, text: str, confidence: float = 0.0) -> Optional[KillFeedEntry]:
        """
        Parse one OCR'd kill-feed line. Returns None when the text is not
        a recognizable kill/knock entry — we never guess names.

        Strategy: anchor on the action word ("knocked", "eliminated", ...)
        with word boundaries (so a killer literally named "SkillzOP" is not
        misclassified by the "kill" substring), then take the name token
        immediately left (killer) and right (victim).
        """
        text = (text or "").strip()
        if len(text) < int(self.config["min_text_len"]):
            return None

        lowered = text.lower()
        entry = KillFeedEntry(raw_text=text, confidence=confidence)

        m = _ACTION_RE.search(text)
        if not m:
            return None  # no action token -> not a kill-feed line
        action = m.group("action").lower()
        entry.event_type = "kill" if action in _KILL_ACTIONS else "knock"

        entry.headshot = "headshot" in lowered or re.search(r"\bhs\b", lowered) is not None

        left_tokens = text[: m.start()].split()
        right_tokens = text[m.end():].split()
        killer = self._clean_name(left_tokens[-1]) if left_tokens else ""
        victim = self._clean_name(right_tokens[0]) if right_tokens else ""
        if not killer or not victim or killer.lower() == victim.lower():
            return None

        entry.killer = killer
        entry.victim = victim
        entry.is_valid = True
        return entry

    # ------------------------------------------------------------------
    # Dedup
    # ------------------------------------------------------------------

    def _remember(self, entry: KillFeedEntry, now: float) -> bool:
        """
        Return True if this entry is NEW (not seen within TTL) and record it.
        Purges expired/beyond-cap keys so the table stays bounded.
        """
        ttl = float(self.config["dedup_ttl_sec"])
        max_keys = int(self.config["dedup_max_keys"])
        key = entry.dedup_key()

        # Purge expired keys opportunistically.
        expired = [k for k, t in self._seen.items() if now - t > ttl]
        for k in expired:
            del self._seen[k]
        # Hard cap (oldest first).
        while len(self._seen) >= max_keys:
            oldest = min(self._seen, key=lambda k: self._seen[k])
            del self._seen[oldest]

        first_seen = self._seen.get(key)
        if first_seen is not None and now - first_seen <= ttl:
            return False
        self._seen[key] = now
        return True

    # ------------------------------------------------------------------
    # Public frame API
    # ------------------------------------------------------------------

    def parse_frame(
        self,
        frame: np.ndarray,
        match_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> List:
        """
        Run the full pipeline on one BGR frame.

        Returns a list of ``NormalizedEvent`` — one per NEW (deduplicated)
        kill-feed line. Empty list when OCR is unavailable, the frame has no
        text, or every line was already emitted (dedup).
        """
        now = time.time() if now is None else now
        if not self.ocr_available():
            return []

        region = self._crop_region(frame)
        if region is None or region.size == 0:
            return []

        image = self._preprocess(region)
        if image is None:  # blank / uniform region — nothing to read
            return []

        events = []
        try:
            lines = self._ocr_lines(image)
        except Exception as exc:
            self._log.warning("ocr failed", error=str(exc))
            return []

        feed_cls, event_cls = _normalized_event_cls()
        for text, conf in lines:
            entry = self.parse_text(text, confidence=conf)
            if entry is None or not entry.is_valid:
                continue
            if conf < float(self.config["min_word_confidence"]):
                continue  # unclear OCR -> do not guess, skip
            if not self._remember(entry, now):
                continue  # already emitted within TTL
            events.append(
                event_cls(
                    event_type=(
                        feed_cls.KILL if entry.event_type == "kill" else feed_cls.KNOCK
                    ),
                    ts=now,
                    match_id=match_id,
                    player=entry.killer,
                    victim=entry.victim,
                    weapon=entry.weapon,
                    payload={
                        "detector": self.name,
                        "raw_text": entry.raw_text,
                        "ocr_confidence": round(entry.confidence, 1),
                        "headshot": entry.headshot,
                        "dedup_key": entry.dedup_key(),
                    },
                )
            )
        return events

    def clear_dedup(self) -> None:
        """Forget all seen entries (e.g. between matches)."""
        self._seen.clear()
