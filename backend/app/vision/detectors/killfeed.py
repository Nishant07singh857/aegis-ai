# backend/app/vision/detectors/killfeed.py
"""
AEGIS AI - Kill Feed Detector (production module, Phase 14 W2; Phase 23 W2).

Detects kill-feed entries in BGMI spectator frames and emits
``NormalizedEvent`` objects (kill/knock) matching
``backend/app/services/feed_adapter/schemas.py``.

LAYOUT (measured on real frames, Phase 23 W1 — LAYOUT_REPORT.md):
    The killfeed renders TOP-LEFT, stacked below the spectated team's
    roster block — the old top-right region held the minimap/standings
    and never contained a single entry. Scan band: x in [0, 0.34] W,
    y in [0.24, 0.52] H; first row top y ~0.26-0.29 H; bar height
    ~19-21 px and row pitch ~21 px at 360p; new entries append BELOW.

    A real entry has NO action words. Its structure is:
        [killer logo] killer_name [weapon icon] [marker slot]
        [victim logo] victim_name
    Knock = crawling-player glyph in the marker slot; headshot = white
    ring/crosshair glyph in the marker slot; no marker = plain kill.

PARSING PATHS (both live, in order):
    1. Layout parser (Phase 23): bar/row segmentation in the scan band
       (resolution-adaptive port of the W1 harvester's structural bar
       detector), per-row OCR WITH word bounding boxes, weapon/marker
       classification by template matching (``icon_matcher.py`` against
       the real W1 icon library). Killer = name tokens left of the icon
       slot, victim = tokens right of the marker/victim-logo side.
    2. Legacy text parser (``parse_text``): action-word anchored OCR
       parse, kept for text/demo feeds, the Phase-15 hybrid's per-entry
       fallback, and the existing test-suite. Real broadcast entries
       never match it (no action words) — that is expected, not a bug.

OCR ENGINE CHOICE (documented, honest):
    * Tesseract (via ``pytesseract``) is the primary engine: free, fully
      offline, CPU-light. EasyOCR was NOT chosen (100 MB model download,
      torch dependency, not installed here).
    * Neither engine is bundled: Tesseract is imported lazily and every
      code path degrades gracefully (``ocr_available() == False``,
      ``parse_frame`` returns ``[]``) if the binary is missing. We NEVER
      emit a fake detection.

WEAPON ICONS (Phase 23):
    Weapons are icons, not text. They are now classified by REAL
    template matching against the W1 library of crops harvested from
    real VOD frames — at CLASS level only ("ar"/"smg"/"sniper"/"dmr"/
    "frag_grenade"/"pistol"); specific models are indistinguishable at
    360p and are never emitted. Below the confidence threshold — or when
    an unresolved ``unknown_*`` glyph wins — the weapon stays ``None``
    (honest unknown). See ``icon_matcher.py``.

DEDUP (critical):
    The same kill-feed line is visible for several seconds, i.e. across
    hundreds of frames. Each parsed entry is keyed by
    ``(killer.lower(), victim.lower(), event_type)`` and remembered for
    ``dedup_ttl_sec`` (default 30 s). Only the first sighting of a key
    within the TTL is emitted. The table is bounded (default 512 keys)
    and purged on every insertion so memory cannot grow unboundedly.
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

# Kill-feed region in BGMI spectator UI: TOP-LEFT band below the roster
# block, expressed as fractions of frame width/height so it works on any
# capture resolution. Measured by Phase 23 W1 on real frames
# (LAYOUT_REPORT.md §2): x in [0, 0.34] W, y in [0.24, 0.52] H.
# (x1, y1, x2, y2) — override per device via set_config(kill_feed_region=...).
DEFAULT_KILL_FEED_REGION: Tuple[float, float, float, float] = (0.0, 0.24, 0.34, 0.52)

# Per-line band: when we cannot rely on Tesseract line numbers we re-run
# PSM-8 per horizontal band. Kept for documentation; current pipeline uses
# image_to_data line grouping.
_TESSERACT_CONFIG_LINE = "--oem 3 --psm 6"
_TESSERACT_CONFIG_WORD = "--oem 3 --psm 8"

#: Weapon names reference list (kept for documentation; text feeds only —
#: the broadcast weapon is an icon, classified by icon_matcher.py).
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
    # Phase 23: icon-matcher provenance (class-level weapon only).
    weapon_family: Optional[str] = None     # e.g. "ar_a", "frag_grenade"
    weapon_confidence: float = 0.0         # matcher NCC score 0..1
    # Best-matching template family even when it did NOT yield a
    # weapon (unknown_* glyph or below threshold) — diagnostics only.
    icon_family: Optional[str] = None
    # Phase 32 margin gate: top-candidate margin (best - runner-up)
    # and whether the gate abstained an otherwise above-threshold
    # identified family. Abstention keeps the entry; only the weapon
    # is UNKNOWN — never a guessed gun name.
    weapon_margin: float = 0.0
    weapon_abstained: bool = False

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
            # --- Phase 23 layout parser / icon matcher ---
            "layout_parse": True,
            # Dull-icon preprocessing for the matcher:
            # "bin" | "clahe" | "histeq" | "none" (W5 A/B switch;
            # default "bin" chosen on harvest-VOD validation — family
            # top-1 54% vs 41% raw / 43% histeq / 36% clahe; see the
            # Phase 23 W2 report).
            "icon_preprocess": "none",
            "icon_threshold": 0.55,
            # Phase 32 strict margin gate (abstention ON, owner
            # decision): weapon labels ship only at margin >= this.
            # Lowering it re-ships ~48% labels — never silent.
            "icon_margin_threshold": 0.15,
            "marker_threshold": 0.50,
            "icon_upscale": 4,
            # Layout-row OCR: 4x cubic upscale + Otsu (measured on real
            # rows: name fragments land at conf ~30-55, junk at <10;
            # the legacy 2x pipeline reads real rows at conf 0-25).
            "layout_upscale": 4.0,
            "layout_min_word_confidence": 25.0,
            # --- Phase 28 cost cuts (semantics unchanged, A/B-verified
            # by scripts/phase28_profile.py) ---
            # Legacy OCR gate: skip the legacy full-band OCR when the
            # region holds no aligned bright-glyph row (no text line
            # can be read from it anyway). Measured Phase 28: on real
            # quiet frames the legacy fallback was 100% of the parse
            # cost (~0.42 s median) and never produced an entry.
            "legacy_structure_gate": True,
        }
        self.config.update(config)
        self._seen: Dict[str, float] = {}  # dedup_key -> first seen epoch
        self._tesseract_ok: Optional[bool] = None
        self._matcher = None
        self._matcher_key: Optional[tuple] = None
        # Band-parse cache: killfeed bands are static for seconds at a
        # time; re-running segmentation+matching+OCR on identical pixels
        # every frame would waste the ~2.3 FPS CPU budget. Keyed by a
        # hash of the region bytes; stores PRE-dedup entries. A band
        # that produced NOTHING from either path is negative-cached:
        # identical pixels parse identically (the pipeline is
        # deterministic), so re-parsing them can only waste CPU.
        self._band_cache_key: Optional[str] = None
        self._band_cache_entries: List[KillFeedEntry] = []
        self._band_negative_key: Optional[str] = None
        self._band_negative_at: float = 0.0
        self._log = log.bind(detector=name)

    # ------------------------------------------------------------------
    # Icon matcher (lazy, config-aware)
    # ------------------------------------------------------------------

    def _get_matcher(self):
        """IconMatcher built from current config (rebuilt on change)."""
        key = (
            str(self.config.get("icon_preprocess", "none")),
            float(self.config.get("icon_threshold", 0.55)),
            float(self.config.get("marker_threshold", 0.50)),
            int(self.config.get("icon_upscale", 4)),
            float(self.config.get("icon_margin_threshold", 0.15)),
        )
        if self._matcher is not None and self._matcher_key == key:
            return self._matcher
        try:
            from .icon_matcher import IconMatcher

            self._matcher = IconMatcher(
                preprocess=key[0],
                threshold=key[1],
                marker_threshold=key[2],
                upscale=key[3],
                margin_threshold=key[4],
            )
            self._matcher_key = key
        except Exception as exc:  # pragma: no cover - defensive
            self._log.warning("icon_matcher_unavailable", error=str(exc))
            self._matcher = None
            self._matcher_key = key
        return self._matcher

    def set_config(self, **kwargs) -> None:
        super().set_config(**kwargs)
        # Invalidate the band cache: geometry/preprocess may have changed.
        self._band_cache_key = None
        self._band_cache_entries = []
        self._band_negative_key = None
        self._band_negative_at = 0.0

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

    @staticmethod
    def _has_text_row(gray: np.ndarray) -> bool:
        """Cheap structural test: does this grayscale crop hold at
        least one horizontal row of aligned bright glyphs (a readable
        text line)?

        Phase 28 legacy gate. The legacy action-word parser can only
        ever read a text line; a crop whose bright pixels never form
        >= 5 glyph-sized blobs sharing a baseline (within tolerance)
        and spanning >= 22% of the width contains no line to read, so
        paying full-band Tesseract on it is pure waste — measured on
        real quiet frames, that was ~0.42 s median per sample at a
        100% rate (Phase 28 profile). Conservative by construction:
        any doubt (no cv2, odd shapes) returns True = run the OCR.
        """
        try:
            import cv2
        except Exception:
            return True
        if gray is None or gray.size == 0 or gray.ndim != 2:
            return True
        rh, rw = gray.shape[:2]
        bright = (gray >= 150).astype(np.uint8) * 255
        n, _, stats, cents = cv2.connectedComponentsWithStats(bright, 8)
        glyphs: List[Tuple[float, float]] = []
        for i in range(1, n):
            x, y, w, h, area = (int(v) for v in stats[i])
            if h < max(3.0, 0.04 * rh) or h > 0.60 * rh:
                continue
            if w < 2 or w > 0.30 * rw:
                continue
            if area < 3:
                continue
            glyphs.append((float(cents[i][0]), float(cents[i][1])))
        if len(glyphs) < 5:
            return False
        tol = max(2.0, 0.06 * rh)
        glyphs.sort(key=lambda g: g[1])
        best = 0
        best_span = 0.0
        j = 0
        for i in range(len(glyphs)):
            while glyphs[i][1] - glyphs[j][1] > tol:
                j += 1
            if i - j + 1 > best:
                best = i - j + 1
                xs = [g[0] for g in glyphs[j:i + 1]]
                best_span = max(xs) - min(xs)
        return best >= 5 and best_span >= 0.22 * rw

    def _preprocess(self, region: np.ndarray):
        """BGR region -> high-contrast PIL image ready for Tesseract."""
        from PIL import Image

        # BGR -> grayscale via luminance.
        if region.ndim == 3:
            b, g, r = region[:, :, 0], region[:, :, 1], region[:, :, 2]
            gray = (0.114 * b + 0.587 * g + 0.299 * r).astype(np.uint8)
        else:
            gray = region.astype(np.uint8)

        # Phase 28 legacy gate: no aligned glyph row -> no text line
        # exists for the legacy parser; skip the Tesseract call that
        # dominated quiet-feed parse cost. Returns the same None the
        # blank-region fast path returns, so callers see no change.
        if bool(self.config.get("legacy_structure_gate", True)) and not (
            self._has_text_row(gray)
        ):
            return None

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

    def _preprocess_row(self, strip: np.ndarray):
        """Row strip -> PIL image for layout OCR (4x cubic + Otsu).

        Separate from ``_preprocess`` (legacy 2x line pipeline): real
        360p kill-feed glyphs are 8-12 px tall, and Tesseract only
        segments them into words at ~4x. Returns None for blank strips.
        """
        import cv2
        from PIL import Image

        if strip is None or strip.size == 0:
            return None
        if strip.ndim == 3:
            gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
        else:
            gray = strip.astype(np.uint8)
        if float(gray.std()) < float(self.config["blank_std_threshold"]):
            return None
        scale = float(self.config.get("layout_upscale", 4.0)) or 1.0
        big = cv2.resize(
            gray,
            (max(1, int(gray.shape[1] * scale)), max(1, int(gray.shape[0] * scale))),
            interpolation=cv2.INTER_CUBIC,
        )
        _, binary = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if float(binary.mean()) > 127.0:
            binary = 255 - binary
        return Image.fromarray(binary, mode="L")

    def _ocr_words(self, image, scale: Optional[float] = None) -> List[Dict]:
        """Tesseract word boxes for one row image.

        Returns [{text, conf, x, y, w, h}] in ORIGINAL row-image pixels
        (the preprocessing upscale is divided back out), so callers can
        split killer/victim tokens around the icon slot.
        """
        import pytesseract

        if scale is None:
            scale = float(self.config["upscale"]) or 1.0
        data = pytesseract.image_to_data(
            image, config=_TESSERACT_CONFIG_LINE, output_type=pytesseract.Output.DICT
        )
        words: List[Dict] = []
        n = len(data.get("text", []))
        for i in range(n):
            text = str(data["text"][i] or "").strip()
            if not text:
                continue
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1.0
            words.append(
                {
                    "text": text,
                    "conf": conf,
                    "x": float(data["left"][i]) / scale,
                    "y": float(data["top"][i]) / scale,
                    "w": float(data["width"][i]) / scale,
                    "h": float(data["height"][i]) / scale,
                }
            )
        words.sort(key=lambda wd: wd["x"])
        return words

    # ------------------------------------------------------------------
    # Legacy text parsing (kept: text/demo feeds + existing tests)
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
    # Phase 23 layout parser
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_bars(region: np.ndarray, frame_h: int) -> List[Dict]:
        """Structural bar detection inside the scan band.

        Resolution-adaptive port of the W1 harvester's ``detect_bars``
        (data/dataset_work/w2_lib.py, proven on real VOD frames): dark
        translucent bars are found against a median-blurred background,
        then tightened to the row-aligned bright-glyph row inside them.
        All pixel constants scale with ``k = frame_h / 360`` so the same
        geometry works above 360p. Bars are returned in REGION pixels,
        sorted top-to-bottom (feed order: oldest first).
        """
        try:
            import cv2
        except Exception:
            return []
        if region is None or region.size == 0 or region.ndim < 3:
            return []
        k = max(0.5, float(frame_h) / 360.0)
        crop = region
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        med = int(31 * k) | 1  # odd kernel
        bg = cv2.medianBlur(gray, max(3, med))
        dark = (gray.astype(np.int16) < bg.astype(np.int16) - 28).astype(np.uint8) * 255
        close_k = (max(3, int(7 * k)), max(5, int(25 * k)))
        closed = cv2.morphologyEx(
            dark, cv2.MORPH_CLOSE, np.ones(close_k, np.uint8)
        )
        n, _, stats, _ = cv2.connectedComponentsWithStats(closed, 8)
        out: List[Dict] = []
        for i in range(1, n):
            x, y, ww, hh, area = (int(v) for v in stats[i])
            if ww < 50 * k or ww > crop.shape[1]:
                continue
            if hh < 9 * k or hh > 34 * k:
                continue
            if ww / max(hh, 1) < 2.6:
                continue
            if area / max(ww * hh, 1) < 0.40:
                continue
            roi = gray[y:y + hh, x:x + ww]
            bright = int((roi > 150).sum())
            if bright < 15 * k:
                continue
            sub = dark[y:y + hh, x:x + ww]
            ys, xs = np.nonzero(sub)
            if xs.size < 20:
                continue
            tx1, ty1, tx2, ty2 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
            broi = (roi > 150).astype(np.uint8) * 255
            ng, _, gstats, gcents = cv2.connectedComponentsWithStats(broi, 8)
            glyphs = []
            for gi in range(1, ng):
                if int(gstats[gi, cv2.CC_STAT_AREA]) < 3:
                    continue
                glyphs.append(
                    (
                        int(gstats[gi, cv2.CC_STAT_LEFT]),
                        int(gstats[gi, cv2.CC_STAT_TOP]),
                        int(gstats[gi, cv2.CC_STAT_WIDTH]),
                        int(gstats[gi, cv2.CC_STAT_HEIGHT]),
                        float(gcents[gi][1]),
                    )
                )
            if len(glyphs) < 6:
                continue
            medy = float(np.median([g[4] for g in glyphs]))
            row = [g for g in glyphs if abs(g[4] - medy) <= 4.0 * k]
            if len(row) < 6:
                continue
            rx1 = max(x + min(g[0] for g in row) - int(3 * k), x + tx1)
            ry1 = max(y + min(g[1] for g in row) - int(3 * k), y + ty1)
            rx2 = min(x + max(g[0] + g[2] for g in row) + int(3 * k), x + tx2)
            ry2 = min(y + max(g[1] + g[3] for g in row) + int(3 * k), y + ty2)
            if rx2 <= rx1 or ry2 <= ry1:
                continue
            out.append({"x": int(rx1), "y": int(ry1),
                        "w": int(rx2 - rx1), "h": int(ry2 - ry1)})
        out.sort(key=lambda b: b["y"])
        return out

    def _parse_entry_strip(
        self, strip: np.ndarray, frame_h: int
    ) -> Optional[KillFeedEntry]:
        """Parse ONE kill-feed row strip (BGR) via layout + icons + OCR.

        Returns a valid KillFeedEntry or None. Never guesses: any
        missing piece (no confident icon slot, unclear names) -> None.
        Public-ish for tests and the Phase-15 hybrid's entry crops.

        Phase 28 note: a row-result micro-cache keyed by the strip's
        structural glyph mask was built and MEASURED here, then
        removed — on the harvested real sequences the bar
        segmentation jitters every frame (box w 83->130 px across
        consecutive samples) and three key designs (exact strip mask,
        tight-bar mask, fixed-size normalised mask) all measured
        0 repeat hits in 28 strip parses (scripts/phase28_profile.py).
        A cache that never hits is pure collision surface; the wins
        that measured real are the legacy structure gate and the
        step order below.
        """

        # Step order (Phase 28): the icon candidates are computed
        # BEFORE the row OCR, because when no candidate clears the
        # icon threshold the entry is None regardless of what OCR
        # would say — previously the OCR result was computed and
        # discarded on every such strip (Phase 28 profile: row OCR
        # is the single most expensive stage). When a candidate IS
        # viable, OCR runs exactly as before and the loop below is
        # the original one.
        matcher = self._get_matcher()
        if matcher is None or not matcher.available:
            return None
        if strip is None or strip.size == 0 or strip.ndim < 3:
            return None
        # Scale from the strip itself: a full entry strip (bar + margins)
        # is ~24 px tall at 360p, whatever the source frame size was.
        # This keeps the hybrid's per-entry crops (which carry no frame
        # context) on the same geometry as band-segmented rows.
        k = min(4.0, max(0.5, float(strip.shape[0]) / 24.0))
        threshold = float(self.config.get("icon_threshold", 0.55))

        # 1) Row preprocessing (blank-strip gate, unchanged).
        image = self._preprocess_row(strip)
        if image is None:
            return None

        # 2) Weapon icon candidates, best first (moved before OCR —
        #    see docstring). No viable candidate -> no entry, and the
        #    row OCR is skipped entirely.
        candidates = matcher.match_candidates(strip, top_n=4)
        if not any(
            m.location is not None and m.score >= threshold and m.location[2] > 0
            for m in candidates
        ):
            return None

        # 3) OCR word boxes for the row (needed to vet icon candidates).
        try:
            words = self._ocr_words(
                image, scale=float(self.config.get("layout_upscale", 4.0))
            )
        except Exception as exc:
            self._log.warning("layout_ocr_failed", error=str(exc))
            return None
        if len(words) < 2:
            return None

        def _join(tokens) -> Tuple[str, List[float]]:
            parts: List[str] = []
            confs: List[float] = []
            for wd in tokens:
                cleaned = self._clean_name(wd["text"])
                if cleaned:
                    parts.append(cleaned)
                    if wd["conf"] >= 0:
                        confs.append(wd["conf"])
            return " ".join(parts).strip(), confs

        # 4) Walk the pre-computed candidates (step 2). A candidate's
        #    LOCATION defines the icon slot even when the winner is an
        #    unknown_* glyph (weapon stays None, names still split).
        #    Candidates are vetted structurally; the first one passing
        #    every guard wins — a logo mimicking a glyph (GodLike's
        #    gold ring vs the wheel/headshot shapes) must not fail the
        #    whole row.
        for match in candidates:
            if match.location is None or match.score < threshold:
                continue
            ix, iy, iw, ih = match.location
            if iw <= 0:
                continue

            # 5) Marker slot: strip area right of the icon, before the
            #    victim side. Knock/headshot glyphs live there. The
            #    slot is BOUNDED by the victim side: the first OCR word
            #    at/right of the slot start is the victim's name, and
            #    its team logo (~10k px) sits between the markers and
            #    the name. Unbounded, the crop swallows victim text and
            #    the marker templates match letter strokes — false
            #    knock/headshot on marker-less entries (measured on
            #    synthetic strips, Phase 23 W2).
            marker_x1 = ix + iw + int(1 * k)
            marker_x2 = min(strip.shape[1], marker_x1 + int(36 * k))
            for wd in words:
                if wd["x"] < marker_x1:
                    continue
                # Only a NAME-LIKE word bounds the slot: OCR also
                # boxes the marker glyphs themselves as symbol words
                # ("=!", "-»") inside the slot — those must not shrink
                # it. The first real name to the right is the victim.
                if not self._clean_name(wd["text"]):
                    continue
                bound = int(wd["x"]) - int(10 * k)
                if bound >= marker_x1 + int(6 * k):
                    marker_x2 = min(marker_x2, bound)
                break
            knock = False
            headshot = False
            if marker_x2 - marker_x1 >= int(6 * k):
                marker_crop = strip[:, marker_x1:marker_x2]
                mm = matcher.match_markers(marker_crop)
                knock = bool(mm.knock)
                headshot = bool(mm.headshot)

            # 6) Split names around the slots, STRICTLY: a killer
            #    token must END at/before the icon, a victim token must
            #    START at/after the marker slot. Words straddling the
            #    slots are dropped — OCR hallucinates letter fragments
            #    on top of the glyphs themselves, and a straddling
            #    fragment belongs to neither name. (The marker slot
            #    also covers the victim team logo just right of it.)
            victim_min_x = marker_x1 + int(6 * k)
            killer_tokens = [
                wd for wd in words if wd["x"] + wd["w"] <= ix + int(2 * k)
            ]
            victim_tokens = [
                wd for wd in words if wd["x"] >= victim_min_x
            ]
            # The icon-to-victim gap must be wide enough to hold icon +
            # markers + logo — a single-space word gap is a text feed.
            if victim_tokens and killer_tokens:
                gap = victim_tokens[0]["x"] - (
                    killer_tokens[-1]["x"] + killer_tokens[-1]["w"]
                )
                if gap < iw * 0.8:
                    continue

            killer, k_confs = _join(killer_tokens)
            victim, v_confs = _join(victim_tokens)
            if not killer or not victim or killer.lower() == victim.lower():
                continue
            confs = k_confs + v_confs
            mean_conf = sum(confs) / len(confs) if confs else 0.0
            return self._finish_layout_entry(
                killer, victim, knock, headshot, match, mean_conf
            )
        return None

    def _finish_layout_entry(
        self, killer, victim, knock, headshot, match, mean_conf
    ) -> Optional[KillFeedEntry]:
        """Confidence gate + KillFeedEntry build for the layout path."""
        if mean_conf < float(
            self.config.get(
                "layout_min_word_confidence",
                self.config.get("min_word_confidence", 30.0),
            )
        ):
            return None  # unclear names -> skip, never guess

        entry = KillFeedEntry(
            killer=killer,
            victim=victim,
            event_type="knock" if knock else "kill",
            # Phase 32: match.weapon is ALREADY margin-gated inside
            # IconMatcher — a low-margin winner arrives here as None,
            # never as a guessed gun name. The entry still ships.
            weapon=match.weapon,
            headshot=headshot,
            confidence=mean_conf,
            raw_text=f"{killer} [{match.family or 'unknown'}] {victim}",
            is_valid=True,
            weapon_family=match.weapon_family,
            weapon_confidence=round(float(match.score), 3) if match.weapon else 0.0,
            icon_family=match.family,
            weapon_margin=round(float(match.margin), 3),
            weapon_abstained=bool(getattr(match, "weapon_abstained", False)),
        )
        return entry

    def parse_layout_region(
        self, region: np.ndarray, frame_h: int
    ) -> List[KillFeedEntry]:
        """Segment + parse every entry row in a scan-band region crop.

        Returns PRE-dedup entries (possibly empty). ``frame_h`` is the
        FULL frame height (geometry scale reference).
        """
        if not bool(self.config.get("layout_parse", True)):
            return []
        matcher = self._get_matcher()
        if matcher is None or not matcher.available:
            return []
        bars = self._detect_bars(region, frame_h)
        k = max(0.5, float(frame_h) / 360.0)
        entries: List[KillFeedEntry] = []
        rh, rw = region.shape[:2]
        for bar in bars:
            # Harvester margins: the bar box is text-tight; the real
            # entry extends a little beyond it on every side.
            x1 = max(0, bar["x"] - int(8 * k))
            x2 = min(rw, bar["x"] + bar["w"] + int(30 * k))
            y1 = max(0, bar["y"] - int(5 * k))
            y2 = min(rh, bar["y"] + bar["h"] + int(5 * k))
            if x2 - x1 < int(40 * k) or y2 - y1 < int(8 * k):
                continue
            strip = region[y1:y2, x1:x2]
            try:
                entry = self._parse_entry_strip(strip, frame_h)
            except Exception as exc:  # one bad row never kills the feed
                self._log.warning("layout_row_failed", error=str(exc))
                entry = None
            if entry is not None and entry.is_valid:
                entries.append(entry)
        return entries

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
    # Event construction (shared by layout + legacy paths)
    # ------------------------------------------------------------------

    def _entry_to_event(self, entry: KillFeedEntry, match_id, now: float):
        feed_cls, event_cls = _normalized_event_cls()
        payload = {
            "detector": self.name,
            "raw_text": entry.raw_text,
            "ocr_confidence": round(entry.confidence, 1),
            "headshot": entry.headshot,
            # Integrity C2 (source marker ONLY — parser behaviour is
            # unchanged): this headshot flag is icon-derived (measured
            # 7.7 % precision, WEAPON_RECOGNITION §2.7) and must never
            # be persisted as a player stat. The spotter toggle is the
            # only trusted headshot producer.
            "headshot_source": "vision_icon",
            "dedup_key": entry.dedup_key(),
        }
        if entry.weapon is not None:
            # Phase 23: class-level weapon from real icon matching.
            # Phase 32: this branch is reachable ONLY for margin-gated
            # labels (margin >= 0.15) — the matcher enforces it.
            payload["weapon_confidence"] = round(float(entry.weapon_confidence), 3)
            payload["weapon_family"] = entry.weapon_family
            payload["weapon_source"] = "icon_template"
            payload["weapon_granularity"] = "class"
            payload["weapon_margin"] = round(float(entry.weapon_margin), 3)
            payload["weapon_abstained"] = False
        elif entry.weapon_abstained:
            # Phase 32 abstention, stated honestly so downstream can
            # count gated coverage: the glyph was identified and
            # above score threshold, but too close to the runner-up
            # to name. Weapon stays null -> bus renders "Unknown".
            payload["weapon_abstained"] = True
            payload["weapon_margin"] = round(float(entry.weapon_margin), 3)
            payload["weapon_source"] = "icon_template"
            payload["weapon_granularity"] = "class"
            payload["icon_family"] = entry.icon_family
        elif entry.icon_family and entry.icon_family.startswith("unknown_"):
            # An unresolved glyph won the icon slot: the weapon stays
            # null (never guessed), but the payload says so honestly
            # so downstream can count icon coverage.
            payload["icon_glyph"] = "unknown"
            payload["icon_family"] = entry.icon_family
        return event_cls(
            event_type=(
                feed_cls.KILL if entry.event_type == "kill" else feed_cls.KNOCK
            ),
            ts=now,
            match_id=match_id,
            player=entry.killer,
            victim=entry.victim,
            weapon=entry.weapon,
            # Integrity C2: vision provenance — never a trusted
            # headshot source (see feed_adapter/schemas.py).
            source="vision_killfeed",
            payload=payload,
        )

    def _entries_to_events(
        self, entries: List[KillFeedEntry], match_id, now: float
    ) -> List:
        events = []
        for entry in entries:
            if entry is None or not entry.is_valid:
                continue
            if not self._remember(entry, now):
                continue  # already emitted within TTL
            events.append(self._entry_to_event(entry, match_id, now))
        return events

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

        # Phase 23 layout path first (real broadcast geometry). The
        # band-hash cache keeps static bands from being re-parsed.
        frame_h = int(frame.shape[0])
        cache_key = None
        try:
            cache_key = hashlib.sha1(
                region.tobytes() + str(region.shape).encode()
            ).hexdigest()
        except Exception:
            cache_key = None
        if (
            cache_key is not None
            and cache_key == self._band_negative_key
            and now - self._band_negative_at < float(self.config["dedup_ttl_sec"])
        ):
            return []
        if cache_key is not None and cache_key == self._band_cache_key:
            layout_entries = self._band_cache_entries
        else:
            try:
                layout_entries = self.parse_layout_region(region, frame_h)
            except Exception as exc:
                self._log.warning("layout_parse_failed", error=str(exc))
                layout_entries = []
            if cache_key is not None:
                self._band_cache_key = cache_key
                self._band_cache_entries = layout_entries
        if layout_entries:
            return self._entries_to_events(layout_entries, match_id, now)

        events = self._legacy_region_to_events(region, match_id, now)
        if not events and cache_key is not None:
            self._band_negative_key = cache_key
            self._band_negative_at = now
        return events

    def _ocr_region_to_events(
        self,
        region: np.ndarray,
        match_id: Optional[str],
        now: float,
    ) -> List:
        """
        OCR one BGR crop -> list of NEW (deduplicated) NormalizedEvents.

        The shared reading step used by the Phase-15 hybrid detector
        (per-entry crops proposed by the trained model). The layout
        parser gets first try on the crop (a hybrid entry crop IS one
        real entry row); the legacy action-word parse runs when the
        layout path cannot read it. Never guesses: unclear text is
        skipped, not emitted.
        """
        # Layout attempt: single/multi-row real-entry parse.
        if bool(self.config.get("layout_parse", True)):
            try:
                entries = self._layout_entries_for_crop(region)
                if entries:
                    return self._entries_to_events(entries, match_id, now)
            except Exception as exc:
                self._log.warning("layout_crop_failed", error=str(exc))

        return self._legacy_region_to_events(region, match_id, now)

    def _legacy_region_to_events(
        self,
        region: np.ndarray,
        match_id: Optional[str],
        now: float,
    ) -> List:
        """Legacy action-word OCR parse of one crop (text/demo feeds)."""
        image = self._preprocess(region)
        if image is None:  # blank / uniform region — nothing to read
            return []

        events = []
        try:
            lines = self._ocr_lines(image)
        except Exception as exc:
            self._log.warning("ocr failed", error=str(exc))
            return []

        for text, conf in lines:
            entry = self.parse_text(text, confidence=conf)
            if entry is None or not entry.is_valid:
                continue
            if conf < float(self.config["min_word_confidence"]):
                continue  # unclear OCR -> do not guess, skip
            if not self._remember(entry, now):
                continue  # already emitted within TTL
            events.append(self._entry_to_event(entry, match_id, now))
        return events

    def _layout_entries_for_crop(
        self, crop: np.ndarray
    ) -> List[KillFeedEntry]:
        """Layout-parse an arbitrary crop (whole band OR one entry row).

        A wide, short crop (an entry strip: aspect >= 2.5) is parsed
        directly as one row. A taller crop is treated as a scan band
        whose height is the configured region fraction of the frame,
        and goes through bar segmentation.
        """
        if crop is None or crop.size == 0 or crop.ndim < 3:
            return []
        ch, cw = int(crop.shape[0]), int(crop.shape[1])
        if cw >= ch * 2.5 and ch <= 64:
            entry = self._parse_entry_strip(crop, 360)
            return [entry] if entry is not None else []
        frac = float(self.config["kill_feed_region"][3]) - float(
            self.config["kill_feed_region"][1]
        )
        frame_h = int(ch / frac) if frac > 1e-6 else 360
        return self.parse_layout_region(crop, frame_h)

    def clear_dedup(self) -> None:
        """Forget all seen entries (e.g. between matches)."""
        self._seen.clear()
