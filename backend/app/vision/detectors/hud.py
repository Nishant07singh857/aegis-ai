# backend/app/vision/detectors/hud.py
"""
AEGIS AI - HUD Readers (Phase 14 Vision Pipeline, W3)
====================================================

Clean production module for reading BGMI spectator-mode HUD regions with
OpenCV + Tesseract only (zero paid APIs).

Readers in this module
----------------------
- ``AliveCountReader``  -- OCR the alive-counter HUD region; emits a state
  update ONLY when the count changes. Values are range-validated
  (0..max_players); anything else is reported as ``"unknown"``.
- ``ZoneTimerReader``   -- OCR the zone timer region (``M:SS``); emits a
  ``zone_update`` :class:`NormalizedEvent` when a zone phase change is
  detected (timer reset edge). Phase numbers are *inferred* from resets,
  not read from the HUD.
- ``TeamStatusReader``  -- classifies each teammate card in the team bar as
  ``alive`` / ``knocked`` / ``dead`` / ``unknown`` using color thresholding
  on the HP strip. See the class docstring for why color thresholding was
  chosen over template matching.

OCR engine design
-----------------
Engines implement ``OCREngine.read_text(roi, whitelist)`` and return
``(text, confidence)`` or ``None``. The default engine is ``TesseractEngine``
(pytesseract). Where the Tesseract binary is not installed, readers fall back
to ``TemplateDigitEngine`` -- a fully implemented, deterministic OpenCV
digit/colon template matcher used for HUD counters only (HUD counters are a
fixed, high-contrast digit font, which is exactly the case template matching
handles well). The active engine is logged at construction time and reported
in every result's ``extra_data`` so downstream consumers know which path ran.

Hard rules enforced here
------------------------
- No paid APIs. No secrets.
- No placeholders: every code path is implemented.
- No fake detections: unreadable regions return ``"unknown"`` /
  ``status=UNKNOWN`` -- never an invented number. When Tesseract is missing
  AND a region is unreadable, the reader says so instead of guessing.

Region coordinates are relative (0..1) and configurable per reader via
``set_region(...)``. The shipped defaults are best-guess BGMI spectator-mode
placements and MUST be re-calibrated against real footage before production
use; misconfigured regions fail loudly (UNKNOWN) rather than silently.
"""

from __future__ import annotations

import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, Tuple

import numpy as np
import structlog

from .base_detector import (
    BaseDetector,
    DetectionBox,
    DetectionResult,
    DetectionStatus,
)

logger = structlog.get_logger("aegis.vision.hud")


# ---------------------------------------------------------------------------
# NormalizedEvent loading (decoupled from heavy app.services __init__)
# ---------------------------------------------------------------------------

_schemas_module = None
_schemas_lock = threading.Lock()


def _feed_schemas():
    """Return the feed_adapter.schemas module without importing the heavy
    ``app.services`` package ``__init__`` (which pulls SQLAlchemy via
    match_service). Canonical package import first; file-path fallback for
    minimal environments. schemas.py itself only needs pydantic.

    Thread-safe: the first import is serialized so two detector worker
    threads cannot deadlock on the module import lock.
    """
    global _schemas_module
    if _schemas_module is not None:
        return _schemas_module
    with _schemas_lock:
        if _schemas_module is not None:
            return _schemas_module
        _schemas_module = _load_schemas()
        return _schemas_module


def _load_schemas():
    """Actually import the schemas module (called once, under _schemas_lock)."""
    try:
        from app.services.feed_adapter import schemas as mod
    except ImportError:
        import importlib.util
        import pathlib
        import sys
        path = (pathlib.Path(__file__).resolve().parent.parent.parent
                / "services" / "feed_adapter" / "schemas.py")
        name = "aegis_feed_adapter_schemas"
        spec = importlib.util.spec_from_file_location(name, str(path))
        if spec is None or spec.loader is None:
            raise
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod  # required: pydantic resolves annotations via sys.modules
        spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Region + result primitives
# ---------------------------------------------------------------------------


@dataclass
class HUDRegion:
    """A rectangular HUD region expressed in relative (0..1) coordinates."""

    name: str
    x: float  # left   (0..1)
    y: float  # top    (0..1)
    w: float  # width  (0..1)
    h: float  # height (0..1)

    def __post_init__(self) -> None:
        for attr in ("x", "y", "w", "h"):
            v = getattr(self, attr)
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"HUDRegion.{attr} must be in 0..1, got {v}")
        if self.w <= 0 or self.h <= 0:
            raise ValueError("HUDRegion width/height must be positive")

    def to_box(self) -> DetectionBox:
        return DetectionBox(x=self.x, y=self.y, width=self.w, height=self.h,
                            label=self.name)

    def crop(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """Crop the region from a BGR frame; None if degenerate/out of range."""
        if frame is None or frame.ndim < 2:
            return None
        h, w = frame.shape[:2]
        x1 = int(round(self.x * w))
        y1 = int(round(self.y * h))
        x2 = int(round((self.x + self.w) * w))
        y2 = int(round((self.y + self.h) * h))
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 - x1 < 4 or y2 - y1 < 4:
            return None
        roi = frame[y1:y2, x1:x2]
        return roi if roi.size else None


class ReadStatus(str, Enum):
    OK = "ok"            # value read and validated
    UNKNOWN = "unknown"  # unreadable, invalid, or engine unavailable


@dataclass
class HUDRead:
    """One atomic read of a HUD region."""
    value: Optional[Any] = None
    status: ReadStatus = ReadStatus.UNKNOWN
    confidence: float = 0.0
    region: str = ""
    engine: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_known(self) -> bool:
        return self.status == ReadStatus.OK and self.value is not None


# ---------------------------------------------------------------------------
# OCR engines
# ---------------------------------------------------------------------------


class OCREngine(Protocol):
    """Minimal OCR contract used by the HUD readers."""

    name: str

    @property
    def available(self) -> bool:
        ...

    def read_text(self, roi: np.ndarray, whitelist: str) -> Optional[Tuple[str, float]]:
        """Return (text, confidence 0..1) or None when nothing readable."""
        ...


class TesseractEngine:
    """Production OCR engine: Tesseract via pytesseract (local, free)."""

    name = "tesseract"

    def __init__(self) -> None:
        self._pytesseract = None
        self._binary_ok = shutil.which("tesseract") is not None
        if self._binary_ok:
            try:
                import pytesseract  # type: ignore
                self._pytesseract = pytesseract
            except ImportError:
                self._binary_ok = False

    @property
    def available(self) -> bool:
        return self._binary_ok and self._pytesseract is not None

    def _variants(self, roi) -> List[np.ndarray]:
        import cv2
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        # Tight-crop to content: HUD regions are mostly empty padding, and
        # Tesseract struggles on sparse ROIs (e.g. a lone "0").
        _, fg = cv2.threshold(gray, 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if float(np.mean(fg)) > 127:
            fg = cv2.bitwise_not(fg)
        ys, xs = np.where(fg > 0)
        if len(xs) > 0:
            pad = 8
            x1, x2 = max(0, xs.min() - pad), min(gray.shape[1], xs.max() + pad)
            y1, y2 = max(0, ys.min() - pad), min(gray.shape[0], ys.max() + pad)
            if x2 - x1 >= 8 and y2 - y1 >= 8:
                gray = gray[y1:y2, x1:x2]
        # Upscale small HUD text to a comfortable OCR size.
        h = gray.shape[0]
        scale = max(1.0, 90.0 / max(1, h))
        if scale > 1.0:
            gray = cv2.resize(gray, None, fx=scale, fy=scale,
                              interpolation=cv2.INTER_CUBIC)
        # NOTE: no Gaussian blur here. Measured (2026-10-19): blurring AFTER
        # the tight crop collapses Tesseract's word confidence on clean HUD
        # text (e.g. "87": 62 -> 2) because the blur smears glyph energy
        # into the tiny remaining background margin and Otsu then picks a
        # bad threshold. Denoising real capture noise is a calibration task
        # for real footage, not something to guess at here.
        _, binary = cv2.threshold(gray, 0, 255,
                                  cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return [binary, cv2.bitwise_not(binary)]

    def read_text(self, roi: np.ndarray, whitelist: str) -> Optional[Tuple[str, float]]:
        if not self.available or roi is None or roi.size == 0:
            return None
        safe = re.sub(r"[^0-9a-zA-Z:./\- ]", "", whitelist) or "0123456789"
        cfg = f"--psm 7 -c tessedit_char_whitelist={safe}"
        for variant in self._variants(roi):
            try:
                data = self._pytesseract.image_to_data(
                    variant, config=cfg, output_type=self._pytesseract.Output.DICT)
            except Exception:
                continue
            texts, confs = [], []
            for txt, conf in zip(data.get("text", []), data.get("conf", [])):
                txt = (txt or "").strip()
                try:
                    c = float(conf)
                except (TypeError, ValueError):
                    c = -1.0
                if txt and c >= 0:
                    texts.append(txt)
                    confs.append(c)
            if texts:
                joined = "".join(texts)
                mean_conf = sum(confs) / len(confs) / 100.0
                # NOTE: a 0.0 mean here means Tesseract produced no confidence
                # estimate at all -- it stays 0.0 (UNKNOWN downstream), never
                # promoted to a passing score. Emitting a misread as OK would
                # violate the never-guess rule.
                return joined, max(0.0, min(1.0, mean_conf))
        return None


class TemplateDigitEngine:
    """
    OpenCV-only digit/colon recognizer (fallback + test engine).

    HUD counters are a fixed, high-contrast digit font -- the one case where
    template matching is legitimate and deterministic. Templates are rendered
    in-code from an OpenCV Hershey font at several stroke weights; each glyph
    found in the ROI is resized to template size and matched with normalized
    cross-correlation. Any glyph scoring below ``min_glyph_score`` fails the
    whole read (UNKNOWN) instead of guessing.
    """

    name = "template_digits"
    CHARS = "0123456789:"

    #: Expected enclosed-area ("hole") counts per character, used ONLY as an
    #: independent confirmation when template scores are too close to call
    #: (margin < min_margin). Digits not listed here have 0 holes. The check
    #: can only CONFIRM the template winner -- never promote a different
    #: character -- so it cannot turn a would-be UNKNOWN into a wrong read.
    _EXPECTED_HOLES = {"8": 2, "0": 1, "6": 1, "9": 1}
    #: Holes smaller than this fraction of the glyph bbox are ignored
    #: (e.g. the tiny unclosed triangle in Hershey "4" must not count).
    _MIN_HOLE_AREA_RATIO = 0.04
    #: Topology may only override the margin veto when the template winner
    #: already scores this strongly on its own. Below it, a close second
    #: means the glyph is genuinely ambiguous (e.g. a letter "b" scoring
    #: 0.56 as "6") and the read stays UNKNOWN. Calibrated: clean HUD
    #: digits score >= 0.70 in the synthetic suite; garbage glyphs < 0.60.
    _TOPO_CONFIRM_MIN_SCORE = 0.65

    def __init__(self, min_glyph_score: float = 0.45,
                 min_margin: float = 0.10) -> None:
        self.min_glyph_score = min_glyph_score
        # A glyph whose best and second-best template scores are closer than
        # this is ambiguous -> the whole read is UNKNOWN (never a guess).
        self.min_margin = min_margin
        self._templates: Dict[str, List[np.ndarray]] = {}
        self._built = False

    @property
    def available(self) -> bool:
        try:
            import cv2  # noqa: F401
            return True
        except ImportError:
            return False

    def _build_templates(self) -> None:
        import cv2
        self._templates = {}
        for ch in self.CHARS:
            variants = []
            for font_scale, thickness in ((3.0, 4), (3.0, 6), (3.0, 9),
                                         (3.0, 12), (2.0, 5), (2.5, 8)):
                canvas = np.full((120, 100), 255, dtype=np.uint8)
                (tw, th), _ = cv2.getTextSize(ch, cv2.FONT_HERSHEY_SIMPLEX,
                                             font_scale, thickness)
                org = ((100 - tw) // 2, (120 + th) // 2)
                cv2.putText(canvas, ch, org, cv2.FONT_HERSHEY_SIMPLEX,
                            font_scale, 0, thickness, cv2.LINE_AA)
                ys, xs = np.where(canvas < 128)
                if len(xs) == 0:
                    continue
                glyph = canvas[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
                glyph = cv2.resize(glyph, (36, 54), interpolation=cv2.INTER_AREA)
                variants.append(glyph)
            if variants:
                self._templates[ch] = variants
        self._built = True

    def _segment(self, binary: np.ndarray) -> List[np.ndarray]:
        """Split a light-on-dark binary ROI into glyph images (x-sorted)."""
        import cv2
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        h, w = binary.shape[:2]
        boxes = []
        for c in contours:
            x, y, bw, bh = cv2.boundingRect(c)
            area = bw * bh
            if area < 25 or bh < h * 0.06:
                continue
            if bw > w * 0.9 and bh > h * 0.9:
                continue  # the whole ROI, not a glyph
            boxes.append((x, y, bw, bh))
        if not boxes:
            return []
        # Merge x-overlapping intervals (handles the two dots of ":").
        boxes.sort(key=lambda b: b[0])
        merged: List[List[int]] = []
        for x, y, bw, bh in boxes:
            if merged and x <= merged[-1][0] + merged[-1][2] + 2:
                mx, my, mw, mh = merged[-1]
                x1, x2 = min(mx, x), max(mx + mw, x + bw)
                y1, y2 = min(my, y), max(my + mh, y + bh)
                merged[-1] = [x1, y1, x2 - x1, y2 - y1]
            else:
                merged.append([x, y, bw, bh])
        glyphs = []
        for x, y, bw, bh in merged:
            pad = 3
            x1, y1 = max(0, x - pad), max(0, y - pad)
            x2, y2 = min(w, x + bw + pad), min(h, y + bh + pad)
            glyphs.append(binary[y1:y2, x1:x2])
        return glyphs

    @staticmethod
    def _to_dark_on_light(glyph: np.ndarray) -> np.ndarray:
        """Normalize polarity using the bbox rim (always background)."""
        import cv2
        h, w = glyph.shape[:2]
        rim = np.concatenate([
            glyph[0, :], glyph[-1, :], glyph[:, 0], glyph[:, -1],
        ]).astype(np.float32)
        if float(np.mean(rim)) < 127:
            return cv2.bitwise_not(glyph)
        return glyph

    def _count_holes(self, glyph_norm: np.ndarray) -> int:
        """Count enclosed areas ("holes") in a normalized dark-on-light
        glyph (36x54). Holes smaller than _MIN_HOLE_AREA_RATIO of the glyph
        bbox are ignored as rendering artifacts."""
        import cv2
        inv = cv2.bitwise_not(glyph_norm)  # white glyph on black background
        contours, hier = cv2.findContours(
            inv, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if hier is None:
            return 0
        total = float(glyph_norm.shape[0] * glyph_norm.shape[1])
        n = 0
        for i, h in enumerate(hier[0]):
            if h[3] != -1:  # has a parent contour -> enclosed hole
                if cv2.contourArea(contours[i]) / total >= self._MIN_HOLE_AREA_RATIO:
                    n += 1
        return n

    def _match_glyph(self, glyph: np.ndarray, allowed: str) -> Optional[Tuple[str, float]]:
        import cv2
        glyph = self._to_dark_on_light(glyph)
        glyph = cv2.resize(glyph, (36, 54), interpolation=cv2.INTER_AREA)
        scored: List[Tuple[str, float]] = []
        for ch in allowed:
            tmpl_scores = [float(cv2.matchTemplate(glyph, tmpl, cv2.TM_CCOEFF_NORMED)[0][0])
                           for tmpl in self._templates.get(ch, [])]
            if tmpl_scores:
                scored.append((ch, max(tmpl_scores)))
        if not scored:
            return None
        scored.sort(key=lambda kv: kv[1], reverse=True)
        best_ch, best_score = scored[0]
        second_score = scored[1][1] if len(scored) > 1 else -1.0
        if best_score < self.min_glyph_score:
            return None
        if best_score - second_score < self.min_margin:
            # Too close on template scores alone (e.g. "8" vs "6" at small
            # sizes). An independent topological check -- the number of
            # enclosed areas -- may CONFIRM the winner (8 has 2, 6 has 1,
            # 5 has 0), but only when the winner already scores strongly on
            # its own: a weak winner with a close second (e.g. letter "b"
            # as "6" at 0.56) is genuinely ambiguous and stays UNKNOWN.
            # Topology can never promote the runner-up, so the worst case
            # stays UNKNOWN, never a wrong read.
            if (best_score >= self._TOPO_CONFIRM_MIN_SCORE
                    and self._count_holes(glyph) == self._EXPECTED_HOLES.get(best_ch, 0)):
                return best_ch, best_score
            return None  # ambiguous between two glyphs -> no guess
        return best_ch, best_score

    def read_text(self, roi: np.ndarray, whitelist: str) -> Optional[Tuple[str, float]]:
        if not self.available or roi is None or roi.size == 0:
            return None
        import cv2
        if not self._built:
            self._build_templates()
        allowed = "".join(ch for ch in self.CHARS if ch in whitelist) or "0123456789"
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        h = gray.shape[0]
        scale = max(1.0, 80.0 / max(1, h))
        if scale > 1.0:
            gray = cv2.resize(gray, None, fx=scale, fy=scale,
                              interpolation=cv2.INTER_CUBIC)
        _, binary = cv2.threshold(gray, 0, 255,
                                  cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        # Normalize to light glyphs on a dark background so contours are the
        # glyphs themselves (never the background rectangle).
        if float(np.mean(binary)) > 127:
            binary = cv2.bitwise_not(binary)
        glyphs = self._segment(binary)
        if not glyphs:
            return None
        out, scores = [], []
        for g in glyphs:
            m = self._match_glyph(g, allowed)
            if m is None:
                return None  # a glyph we cannot read -> no guess
            out.append(m[0])
            scores.append(m[1])
        return "".join(out), sum(scores) / len(scores)


def select_ocr_engine(prefer: str = "tesseract") -> OCREngine:
    """Pick the best available OCR engine; logs which one was chosen."""
    tess = TesseractEngine()
    if prefer == "tesseract" and tess.available:
        logger.info("hud_ocr_engine", engine="tesseract")
        return tess
    tmpl = TemplateDigitEngine()
    if tmpl.available:
        logger.warning("hud_ocr_engine",
                       engine="template_digits",
                       reason="tesseract binary not available; using OpenCV digit templates")
        return tmpl
    raise RuntimeError("No OCR engine available: install tesseract or opencv")

# ---------------------------------------------------------------------------
# Alive count reader
# ---------------------------------------------------------------------------


@dataclass
class AliveCountUpdate:
    """Emitted only when the validated alive count changes."""
    players: int
    confidence: float
    ts: float = field(default_factory=time.time)
    engine: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"type": "alive_count", "players": self.players,
                "confidence": round(self.confidence, 3), "ts": self.ts,
                "engine": self.engine}


class AliveCountReader(BaseDetector):
    """
    OCR the alive-counter HUD region; emit an update ONLY on change.

    Validation: the parsed value must be an integer in ``0..max_players``
    (default 100). Anything else -- empty ROI, OCR miss, out-of-range number
    -- yields ``HUDRead(status=UNKNOWN)`` and never an event.

    Anti-flicker: a new value must be read identically ``confirm_frames``
    times in a row before it is emitted (OCR on video frames jitters).
    """

    DEFAULT_REGION = HUDRegion(name="alive_counter", x=0.430, y=0.012,
                               w=0.140, h=0.055)

    def __init__(
        self,
        region: Optional[HUDRegion] = None,
        max_players: int = 100,
        min_confidence: float = 0.5,
        confirm_frames: int = 2,
        ocr: Optional[OCREngine] = None,
    ) -> None:
        super().__init__(name="alive_count_reader")
        self.region = region or self.DEFAULT_REGION
        self.max_players = max_players
        self.min_confidence = min_confidence
        self.confirm_frames = max(1, confirm_frames)
        self.ocr: OCREngine = ocr or select_ocr_engine()
        self._last_emitted: Optional[int] = None
        self._pending_value: Optional[int] = None
        self._pending_hits = 0
        self._log = logger.bind(reader=self.name, engine=self.ocr.name)

    # -- configuration ----------------------------------------------------
    def set_region(self, region: HUDRegion) -> None:
        self.region = region
        self.reset_state()

    def reset_state(self) -> None:
        self._last_emitted = None
        self._pending_value = None
        self._pending_hits = 0

    # -- reading ----------------------------------------------------------
    def read(self, frame: np.ndarray) -> HUDRead:
        """Read the current alive count; UNKNOWN when unreadable/invalid."""
        roi = self.region.crop(frame)
        if roi is None:
            return HUDRead(status=ReadStatus.UNKNOWN, region=self.region.name,
                           engine=self.ocr.name,
                           detail={"reason": "region_unavailable"})
        text_conf = self.ocr.read_text(roi, whitelist="0123456789")
        if text_conf is None:
            return HUDRead(status=ReadStatus.UNKNOWN, region=self.region.name,
                           engine=self.ocr.name, detail={"reason": "ocr_empty"})
        text, conf = text_conf
        digits = re.sub(r"\D", "", text)
        if not digits:
            return HUDRead(status=ReadStatus.UNKNOWN, region=self.region.name,
                           engine=self.ocr.name,
                           detail={"reason": "no_digits", "raw": text})
        try:
            value = int(digits)
        except ValueError:
            return HUDRead(status=ReadStatus.UNKNOWN, region=self.region.name,
                           engine=self.ocr.name,
                           detail={"reason": "not_an_int", "raw": text})
        if not (0 <= value <= self.max_players):
            return HUDRead(status=ReadStatus.UNKNOWN, region=self.region.name,
                           engine=self.ocr.name,
                           detail={"reason": "out_of_range", "raw": value})
        if conf < self.min_confidence:
            return HUDRead(status=ReadStatus.UNKNOWN, region=self.region.name,
                           engine=self.ocr.name,
                           detail={"reason": "low_confidence", "raw": value,
                                   "confidence": round(conf, 3)})
        return HUDRead(value=value, status=ReadStatus.OK, confidence=conf,
                       region=self.region.name, engine=self.ocr.name)

    def _confirm(self, read: HUDRead) -> Optional[AliveCountUpdate]:
        """
        Run the anti-flicker confirm logic on an already-read value.

        Split out of poll() so detect() can emit change updates from a
        single OCR pass (calling poll() after read() would OCR twice).
        Either poll() or detect() may be called per frame -- not both --
        since both advance the same confirm state machine.
        """
        if not read.is_known:
            self._pending_value = None
            self._pending_hits = 0
            return None
        value = int(read.value)
        if value == self._last_emitted:
            self._pending_value = None
            self._pending_hits = 0
            return None
        if self._pending_value != value:
            self._pending_value = value
            self._pending_hits = 1
        else:
            self._pending_hits += 1
        if self._pending_hits >= self.confirm_frames:
            self._last_emitted = value
            self._pending_value = None
            self._pending_hits = 0
            self._log.info("alive_count_changed", players=value,
                           confidence=round(read.confidence, 3))
            return AliveCountUpdate(players=value,
                                    confidence=read.confidence,
                                    engine=read.engine)
        return None

    def poll(self, frame: np.ndarray) -> Optional[AliveCountUpdate]:
        """
        Return an :class:`AliveCountUpdate` only when the validated count
        changes (after ``confirm_frames`` identical reads); else None.
        """
        return self._confirm(self.read(frame))

    # -- BaseDetector interface -------------------------------------------
    async def detect(self, frame: np.ndarray, **kwargs) -> DetectionResult:
        start = time.time()
        h, w = frame.shape[:2]
        read = self.read(frame)
        # Single OCR pass: run the confirm state machine on the value we
        # just read instead of calling poll() (which would read again).
        update = self._confirm(read)
        status = (DetectionStatus.SUCCESS if read.is_known
                  else DetectionStatus.NOT_FOUND)
        result = DetectionResult(
            detector_name=self.name,
            status=status,
            frame_width=w, frame_height=h,
            processing_time_ms=self._time_detection(start),
            extra_data={
                "alive": {
                    "players": read.value,
                    "status": read.status.value,
                    "confidence": round(read.confidence, 3),
                    "engine": read.engine,
                    "changed": update is not None,
                    "change": update.to_dict() if update else None,
                },
            },
        )
        self._last_result = result
        return result

    def get_detection_regions(self, frame_width: int,
                              frame_height: int) -> List[DetectionBox]:
        return [self.region.to_box()]


# ---------------------------------------------------------------------------
# Zone timer / phase reader
# ---------------------------------------------------------------------------


class ZoneTimerReader(BaseDetector):
    """
    OCR the zone timer region (``M:SS``) and emit a ``zone_update``
    :class:`~app.services.feed_adapter.schemas.NormalizedEvent` when a zone
    phase change is detected.

    Phase detection: BGMI's zone timer counts down to the next shrink and
    resets to a high value when a new phase begins. A *reset edge* -- the
    last confident read below ``reset_low_s`` followed by a confident read
    at/above ``reset_high_s`` -- increments the inferred phase and emits the
    event. The phase number is therefore *inferred from timer resets*, not
    read from the HUD; this is reported honestly in the event payload.

    Unreadable/invalid timer text -> UNKNOWN, no event, never a guess.
    """

    DEFAULT_REGION = HUDRegion(name="zone_timer", x=0.465, y=0.085,
                               w=0.070, h=0.045)
    MAX_TIMER_S = 900  # 15 minutes -- sanity ceiling

    def __init__(
        self,
        region: Optional[HUDRegion] = None,
        min_confidence: float = 0.5,
        reset_low_s: int = 45,
        reset_high_s: int = 90,
        ocr: Optional[OCREngine] = None,
    ) -> None:
        super().__init__(name="zone_timer_reader")
        self.region = region or self.DEFAULT_REGION
        self.min_confidence = min_confidence
        self.reset_low_s = reset_low_s
        self.reset_high_s = reset_high_s
        self.ocr: OCREngine = ocr or select_ocr_engine()
        self._phase = 0            # 0 = not yet established
        self._last_seconds: Optional[int] = None
        self._log = logger.bind(reader=self.name, engine=self.ocr.name)

    def set_region(self, region: HUDRegion) -> None:
        self.region = region
        self.reset_state()

    def reset_state(self) -> None:
        self._phase = 0
        self._last_seconds = None

    @staticmethod
    def parse_timer(text: str) -> Optional[int]:
        """Parse 'M:SS', 'MM:SS' or bare seconds -> seconds. None if invalid."""
        text = text.strip()
        m = re.fullmatch(r"(\d{1,2}):([0-5]\d)", text)
        if m:
            return int(m.group(1)) * 60 + int(m.group(2))
        m = re.fullmatch(r"(\d{1,3})", text)
        if m:
            return int(m.group(1))
        return None

    def read(self, frame: np.ndarray) -> HUDRead:
        """Read the zone timer in seconds; UNKNOWN when unreadable/invalid."""
        roi = self.region.crop(frame)
        if roi is None:
            return HUDRead(status=ReadStatus.UNKNOWN, region=self.region.name,
                           engine=self.ocr.name,
                           detail={"reason": "region_unavailable"})
        text_conf = self.ocr.read_text(roi, whitelist="0123456789:")
        if text_conf is None:
            return HUDRead(status=ReadStatus.UNKNOWN, region=self.region.name,
                           engine=self.ocr.name, detail={"reason": "ocr_empty"})
        text, conf = text_conf
        cleaned = text.replace(" ", "")
        colon_tokens = re.findall(r"\d{1,2}:\d{2}", cleaned)
        bare_tokens = re.findall(r"(?<![:\d])\d{1,3}(?![:\d])", cleaned)
        token = None
        if colon_tokens:
            # A colon-form timer is present: it must parse, else the read is
            # malformed -- never fall back to a bare digit from the same text.
            for cand in colon_tokens:
                if self.parse_timer(cand) is not None:
                    token = cand
                    break
            if token is None:
                return HUDRead(status=ReadStatus.UNKNOWN, region=self.region.name,
                               engine=self.ocr.name,
                               detail={"reason": "malformed_timer", "raw": text})
        else:
            for cand in bare_tokens:
                if self.parse_timer(cand) is not None:
                    token = cand
                    break
        if token is None:
            return HUDRead(status=ReadStatus.UNKNOWN, region=self.region.name,
                           engine=self.ocr.name,
                           detail={"reason": "no_timer_token", "raw": text})
        seconds = self.parse_timer(token)
        if seconds is None or not (0 <= seconds <= self.MAX_TIMER_S):
            return HUDRead(status=ReadStatus.UNKNOWN, region=self.region.name,
                           engine=self.ocr.name,
                           detail={"reason": "out_of_range", "raw": token})
        if conf < self.min_confidence:
            return HUDRead(status=ReadStatus.UNKNOWN, region=self.region.name,
                           engine=self.ocr.name,
                           detail={"reason": "low_confidence", "raw": token,
                                   "confidence": round(conf, 3)})
        return HUDRead(value=seconds, status=ReadStatus.OK, confidence=conf,
                       region=self.region.name, engine=self.ocr.name,
                       detail={"token": token})

    def _on_read(self, read: HUDRead,
                 match_id: Optional[str] = None):
        """
        Run the timer-reset edge detector on an already-read value.

        Returns a ``zone_update`` NormalizedEvent when a phase change is
        detected, else None. Split out of poll() so detect() can emit the
        event from a single OCR pass. Either poll() or detect() may be
        called per frame -- not both -- since both advance the same phase
        state machine.
        """
        schemas = _feed_schemas()

        if not read.is_known:
            return None
        seconds = int(read.value)

        if self._phase == 0:
            # First confident read establishes the baseline (phase 1).
            self._phase = 1
            self._last_seconds = seconds
            return None

        event = None
        prev = self._last_seconds
        # Reset edge: timer was low, now jumped back up -> new phase began.
        if (prev is not None and prev <= self.reset_low_s
                and seconds >= self.reset_high_s):
            self._phase += 1
            event = schemas.NormalizedEvent(
                event_type=schemas.FeedEventType.ZONE_UPDATE,
                match_id=match_id,
                payload={
                    "zone_phase": self._phase,
                    "timer_seconds": seconds,
                    "previous_timer_seconds": prev,
                    "phase_source": "inferred_from_timer_reset",
                    "confidence": round(read.confidence, 3),
                    "ocr_engine": read.engine,
                },
            )
            self._log.info("zone_phase_changed", phase=self._phase,
                           timer_seconds=seconds)
        self._last_seconds = seconds
        return event

    def poll(self, frame: np.ndarray, match_id: Optional[str] = None):
        """
        Return a ``zone_update`` NormalizedEvent when a phase change is
        detected (timer reset edge); otherwise None. Never emits on UNKNOWN.
        """
        return self._on_read(self.read(frame), match_id=match_id)

    # -- BaseDetector interface -------------------------------------------
    async def detect(self, frame: np.ndarray, **kwargs) -> DetectionResult:
        start = time.time()
        h, w = frame.shape[:2]
        read = self.read(frame)
        # Single OCR pass: run the reset-edge detector on the value we just
        # read. The event (if any) is embedded in extra_data so pipeline
        # consumers can forward it without a second OCR pass.
        event = self._on_read(read, match_id=kwargs.get("match_id"))
        status = (DetectionStatus.SUCCESS if read.is_known
                  else DetectionStatus.NOT_FOUND)
        result = DetectionResult(
            detector_name=self.name,
            status=status,
            frame_width=w, frame_height=h,
            processing_time_ms=self._time_detection(start),
            extra_data={
                "zone": {
                    "timer_seconds": read.value,
                    "status": read.status.value,
                    "confidence": round(read.confidence, 3),
                    "engine": read.engine,
                    "inferred_phase": self._phase,
                    "event": event.model_dump() if event is not None else None,
                },
            },
        )
        self._last_result = result
        return result

    def get_detection_regions(self, frame_width: int,
                              frame_height: int) -> List[DetectionBox]:
        return [self.region.to_box()]

# ---------------------------------------------------------------------------
# Team status reader
# ---------------------------------------------------------------------------


class TeamSlotStatus(str, Enum):
    ALIVE = "alive"
    KNOCKED = "knocked"
    DEAD = "dead"
    UNKNOWN = "unknown"


@dataclass
class TeamStatus:
    slots: List[TeamSlotStatus] = field(default_factory=list)
    confidences: List[float] = field(default_factory=list)
    ts: float = field(default_factory=time.time)
    engine: str = "color_threshold"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "team_status",
            "slots": [s.value for s in self.slots],
            "confidences": [round(c, 3) for c in self.confidences],
            "alive_count": sum(1 for s in self.slots
                               if s == TeamSlotStatus.ALIVE),
            "ts": self.ts,
        }


class TeamStatusReader(BaseDetector):
    """
    Classify each teammate card in the team bar as alive / knocked / dead /
    unknown.

    METHOD CHOICE -- color thresholding (not template matching). Reasons:

    1. **No calibrated templates exist.** Template matching needs reference
       crops captured from real BGMI footage at the target UI skin and
       resolution. This environment has no real BGMI footage; templates
       synthesized from fake art would match the fake art and nothing else,
       producing dishonest confidence on real frames.
    2. **Resolution independence.** Observer phones stream at ~360px width
       while the director PC renders 1080p. Relative-coordinate color
       sampling scales with the frame; templates do not without a
       per-resolution template bank.
    3. **UI-skin robustness.** BGMI reskins the lobby/team UI across seasons;
       the *semantics* (green/yellow HP fill = alive, red-orange banner =
       knocked, greyed card = dead) survive reskins far better than exact
       pixel patterns.
    4. **Graceful degradation.** When the layout shifts or the bar is
       occluded, color ratios simply fall below threshold -> ``unknown``.
       Template matching instead tends to return low-score false positives
       that are easy to misread as real.

    How it works: the team-bar region is split into ``slots`` equal cards.
    For each card, the bottom ``hp_strip_fraction`` is sampled in HSV:

    - a green/yellow BAND in the strip  -> ``alive``
    - a red-orange BAND in the strip    -> ``knocked``
    - card present AND the strip itself positively grey (filled,
      bright, desaturated, essentially free of HP colours) -> ``dead``
    - anything else                     -> ``unknown`` (never guessed)

    Band evidence is measured INSIDE the candidate band, never over
    the whole strip or card (Phase 29 D-1 root fix, measured
    2026-10-22 on synthetic frames): a partial HP fill *is* the band,
    so a fill's width fraction is its HP value, not a detection gate.
    A colour mask counts as a band only when its row projection is
    concentrated with a bottom-anchored peak, its band rows form a
    contiguous run, the coloured pixels inside those rows form solid
    runs (purity), and the fill fraction *within the band rows*
    clears a small floor. Scattered colour speckle fails the
    run/purity/concentration structure even at high density, and a
    card whose strip carries colour speckle is UNKNOWN -- DEAD is a
    positive identification of a grey strip, never the residue of
    failed alive/knocked tests.

    HSV ranges and thresholds are exposed as constructor arguments and
    MUST be calibrated against real footage; the defaults are
    measured on the synthetic frame builders in the test suite, not
    ground truth for real BGMI frames.
    """

    DEFAULT_REGION = HUDRegion(name="team_bar", x=0.015, y=0.800,
                               w=0.270, h=0.170)

    def __init__(
        self,
        region: Optional[HUDRegion] = None,
        slots: int = 4,
        hp_strip_fraction: float = 0.32,
        healthy_hsv: Tuple[Tuple[int, int, int], Tuple[int, int, int]] = (
            (35, 70, 70), (90, 255, 255)),     # green..yellow HP fill
        knocked_hsv: Tuple[Tuple[int, int, int], Tuple[int, int, int]] = (
            (0, 90, 90), (18, 255, 255)),      # red-orange knock banner
        healthy_min_ratio: float = 0.005,
        knocked_min_ratio: float = 0.005,
        presence_std_min: float = 8.0,
        presence_mean_min: float = 12.0,
        dead_fill_min_ratio: float = 0.25,
        min_confidence: float = 0.5,
        band_min_concentration: float = 2.0,
        band_peak_min_fraction: float = 0.30,
        band_min_run_fraction: float = 0.15,
        band_min_purity: float = 0.5,
        band_min_width_fraction: float = 0.005,
        dead_strip_sat_max: float = 10.0,
        dead_max_color_ratio: float = 0.005,
        dead_strip_mean_min: float = 70.0,
    ) -> None:
        super().__init__(name="team_status_reader")
        self.region = region or self.DEFAULT_REGION
        self.slots = max(1, slots)
        self.hp_strip_fraction = hp_strip_fraction
        self.healthy_hsv = healthy_hsv
        self.knocked_hsv = knocked_hsv
        # NOTE (D-1 fix): healthy_min_ratio / knocked_min_ratio are now
        # floors on the fill fraction INSIDE the candidate band rows,
        # not over the whole strip. Measured on the synthetic builders:
        # the smallest drawn band (hp 0.03) has band fill 0.008, real
        # bands at hp >= 0.05 have >= 0.031; 0.005 sits below both and
        # above "no band at all" (0.0). The structural band gates below
        # (concentration / run / purity) carry the anti-speckle load.
        self.healthy_min_ratio = healthy_min_ratio
        self.knocked_min_ratio = knocked_min_ratio
        self.presence_std_min = presence_std_min
        self.presence_mean_min = presence_mean_min
        self.dead_fill_min_ratio = dead_fill_min_ratio
        self.min_confidence = min_confidence
        # Band-structure gates (measured distributions in
        # tests/unit/test_team_status_hp_bands.py): real bands score
        # concentration 2.57-4.54, contiguous band-run 13-23 rows
        # (0.22-0.39 of the strip), purity 1.0, peak at 0.47-0.61 of
        # the strip. Colour speckle scores purity <= 0.26 (coloured
        # densities 0.02-0.40) and, at densities where its ratio could
        # matter, concentration <= 1.94.
        self.band_min_concentration = band_min_concentration
        self.band_peak_min_fraction = band_peak_min_fraction
        self.band_min_run_fraction = band_min_run_fraction
        self.band_min_purity = band_min_purity
        self.band_min_width_fraction = band_min_width_fraction
        # DEAD positive-test gates, all measured on the STRIP (never
        # the whole card, whose saturation dilutes the strip out):
        # synthetic dead strips are filled (fill 0.92-1.0), bright
        # (mean gray 95-106) and perfectly desaturated (sat 0.0,
        # HP-colour ratio 0.0). A live card's strip with no visible
        # band is dark (mean ~56) or carries colour ratio >= 0.019
        # (speckle floor) -- both fail DEAD and stay UNKNOWN.
        self.dead_strip_sat_max = dead_strip_sat_max
        self.dead_max_color_ratio = dead_max_color_ratio
        self.dead_strip_mean_min = dead_strip_mean_min
        self._last_emitted: Optional[Tuple[str, ...]] = None
        self._log = logger.bind(reader=self.name)

    def set_region(self, region: HUDRegion) -> None:
        self.region = region
        self._last_emitted = None

    def _classify_slot(self, card: np.ndarray) -> Tuple[TeamSlotStatus, float, Dict[str, Any]]:
        import cv2
        detail: Dict[str, Any] = {}
        gray = cv2.cvtColor(card, cv2.COLOR_BGR2GRAY)
        mean, std = float(np.mean(gray)), float(np.std(gray))
        detail.update({"mean": round(mean, 1), "std": round(std, 1)})

        if std < self.presence_std_min or mean < self.presence_mean_min:
            # No card content detectable -- do not guess.
            return TeamSlotStatus.UNKNOWN, 0.0, {**detail, "reason": "no_card"}

        h = card.shape[0]
        strip_y = int(h * (1.0 - self.hp_strip_fraction))
        strip = card[strip_y:h, :, :]
        hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)

        healthy_mask = cv2.inRange(hsv, np.array(self.healthy_hsv[0], np.uint8),
                                   np.array(self.healthy_hsv[1], np.uint8))
        knocked_mask = cv2.inRange(hsv, np.array(self.knocked_hsv[0], np.uint8),
                                   np.array(self.knocked_hsv[1], np.uint8))
        total = strip.shape[0] * strip.shape[1]
        healthy_ratio = float(np.count_nonzero(healthy_mask)) / total
        knocked_ratio = float(np.count_nonzero(knocked_mask)) / total
        detail.update({"healthy_ratio": round(healthy_ratio, 3),
                       "knocked_ratio": round(knocked_ratio, 3)})

        healthy = self._band_evidence(healthy_mask, self.healthy_min_ratio)
        knocked = self._band_evidence(knocked_mask, self.knocked_min_ratio)
        detail.update({
            "healthy_band_fill": round(healthy["band_fill"], 3),
            "healthy_band_purity": round(healthy["purity"], 3),
            "healthy_band_run": healthy["run_len"],
            "knocked_band_fill": round(knocked["band_fill"], 3),
            "knocked_band_purity": round(knocked["purity"], 3),
            "knocked_band_run": knocked["run_len"],
        })

        def _band_conf(band_fill: float) -> float:
            # Confidence scales with the fill evidence inside the band
            # (a 3% sliver is a real but weak read; a half-full band is
            # unambiguous). The structural gates have already passed.
            return min(1.0, 0.55 + 0.45 * (band_fill / 0.5))

        if healthy["is_band"]:
            return (TeamSlotStatus.ALIVE, _band_conf(healthy["band_fill"]),
                    detail)
        if knocked["is_band"]:
            return (TeamSlotStatus.KNOCKED, _band_conf(knocked["band_fill"]),
                    detail)

        # DEAD is a POSITIVE identification, never a fall-through: the
        # strip itself must be present (filled, bright grey) and
        # essentially free of colour -- low strip saturation AND a
        # near-zero HP-colour ratio, both measured on the strip. The
        # pre-fix whole-card test (fill_ratio >= 0.25 and card mean_sat
        # < 40) held for EVERY live card (measured fill 1.0, sat
        # 0.8-12 on live low-HP cards), which is exactly how live
        # players were read as dead. A card that fails this test --
        # dark strip, colour speckle, ambiguous tint -- is UNKNOWN.
        strip_gray = gray[strip_y:h, :]
        strip_fill_ratio = float(np.count_nonzero(strip_gray > 20)) / strip_gray.size
        strip_mean = float(np.mean(strip_gray))
        strip_mean_sat = float(np.mean(hsv[:, :, 1]))
        color_ratio = healthy_ratio + knocked_ratio
        fill_ratio = float(np.count_nonzero(gray > 20)) / gray.size
        mean_sat = float(np.mean(cv2.cvtColor(card, cv2.COLOR_BGR2HSV)[:, :, 1]))
        detail.update({
            "fill_ratio": round(fill_ratio, 3),
            "mean_sat": round(mean_sat, 1),
            "strip_fill_ratio": round(strip_fill_ratio, 3),
            "strip_mean": round(strip_mean, 1),
            "strip_mean_sat": round(strip_mean_sat, 1),
            "color_ratio": round(color_ratio, 3),
        })
        if (strip_fill_ratio >= self.dead_fill_min_ratio
                and strip_mean >= self.dead_strip_mean_min
                and strip_mean_sat <= self.dead_strip_sat_max
                and color_ratio <= self.dead_max_color_ratio):
            return TeamSlotStatus.DEAD, 0.6, detail
        return TeamSlotStatus.UNKNOWN, 0.0, {**detail, "reason": "ambiguous"}

    def _band_evidence(self, mask: np.ndarray,
                       min_fill: float) -> Dict[str, Any]:
        """Structural band evidence for one colour mask of the HP strip.

        A real HP/knock band is a horizontal band: its colour is
        vertically concentrated (row-projection peak-to-mean), its
        peak sits in the lower part of the strip, the rows around the
        peak that carry at least half the peak's pixels form ONE
        contiguous run, and inside those band rows the coloured
        pixels form solid horizontal runs (purity = mean over band
        rows of longest-run / row pixel count). The fill fraction is
        measured INSIDE the band rows only -- a partial fill is a
        narrow band, not a failed band, so the mask's width is never
        gated beyond a minimal anti-dot floor. Colour speckle spread
        over the strip fails concentration/run/purity (measured:
        purity <= 0.26 for coloured speckle at densities 0.02-0.40,
        vs 1.0 for every drawn real band).
        """
        import cv2
        strip_h, strip_w = mask.shape[:2]
        out: Dict[str, Any] = {"is_band": False, "band_fill": 0.0,
                               "purity": 0.0, "run_len": 0,
                               "concentration": 0.0}
        rows = np.count_nonzero(mask, axis=1).astype(np.float32)
        if rows.sum() == 0:
            return out
        peak_count = float(rows.max())
        peak_row = int(np.argmax(rows))
        concentration = peak_count / max(float(rows.mean()), 1e-6)
        out["concentration"] = concentration
        # Contiguous run of band rows (rows carrying >= half the peak
        # row's pixels) containing the peak row.
        row_floor = max(1.0, 0.5 * peak_count)
        lo = peak_row
        while lo > 0 and rows[lo - 1] >= row_floor:
            lo -= 1
        hi = peak_row
        while hi < strip_h - 1 and rows[hi + 1] >= row_floor:
            hi += 1
        run_len = hi - lo + 1
        out["run_len"] = run_len
        band = mask[lo:hi + 1, :]
        band_fill = float(np.count_nonzero(band)) / band.size if band.size else 0.0
        out["band_fill"] = band_fill
        # Purity: per band row, the longest solid run of colour as a
        # fraction of that row's coloured pixels (1.0 = one solid bar
        # segment; speckle rows scatter into isolated pixels).
        purities = []
        for r in range(band.shape[0]):
            count = int(rows[lo + r])
            if count <= 0:
                continue
            row = np.concatenate(([0], band[r] > 0, [0])).astype(np.int8)
            edges = np.diff(row)
            starts = np.where(edges == 1)[0]
            ends = np.where(edges == -1)[0]
            longest = int(np.max(ends - starts)) if len(starts) else 0
            purities.append(longest / count)
        purity = float(np.mean(purities)) if purities else 0.0
        out["purity"] = purity
        _, _, bw, _ = cv2.boundingRect(mask)
        min_run = max(2, int(np.ceil(self.band_min_run_fraction * strip_h)))
        out["is_band"] = bool(
            concentration >= self.band_min_concentration
            and peak_row >= self.band_peak_min_fraction * strip_h
            and run_len >= min_run
            and purity >= self.band_min_purity
            and bw >= max(1.0, self.band_min_width_fraction * strip_w)
            and band_fill >= min_fill
        )
        return out

    def read(self, frame: np.ndarray) -> TeamStatus:
        """Classify every slot; unreadable slots are UNKNOWN, never guessed."""
        bar = self.region.crop(frame)
        status = TeamStatus(engine="color_threshold")
        if bar is None:
            status.slots = [TeamSlotStatus.UNKNOWN] * self.slots
            status.confidences = [0.0] * self.slots
            return status
        h, w = bar.shape[:2]
        slot_w = w / self.slots
        for i in range(self.slots):
            x1, x2 = int(i * slot_w), int((i + 1) * slot_w)
            card = bar[:, x1:x2, :]
            if card.size == 0:
                status.slots.append(TeamSlotStatus.UNKNOWN)
                status.confidences.append(0.0)
                continue
            slot_status, conf, _ = self._classify_slot(card)
            if conf < self.min_confidence and slot_status != TeamSlotStatus.UNKNOWN:
                # Below confidence: downgrade to UNKNOWN rather than emit
                # a shaky classification.
                slot_status, conf = TeamSlotStatus.UNKNOWN, 0.0
            status.slots.append(slot_status)
            status.confidences.append(round(conf, 3))
        return status

    def _on_status(self, status: TeamStatus) -> Optional[Dict[str, Any]]:
        """
        Change detection on an already-read status. Returns the status dict
        when any slot changed, else None. Split out of poll() so detect()
        can report changes from a single read pass. Either poll() or
        detect() may be called per frame -- not both -- since both advance
        the same change state.

        Note: the first observation always emits (baseline), even when
        every slot is UNKNOWN -- this is the established W3 contract
        (test_blank_frame_all_unknown_no_crash). An all-UNKNOWN baseline
        invents no game facts; it honestly reports "no team bar observed".
        """
        key = tuple(s.value for s in status.slots)
        if key == self._last_emitted:
            return None
        self._last_emitted = key
        out = status.to_dict()
        self._log.info("team_status_changed", slots=out["slots"])
        return out

    def poll(self, frame: np.ndarray) -> Optional[Dict[str, Any]]:
        """Return the team-status dict only when any slot's state changes."""
        return self._on_status(self.read(frame))

    # -- BaseDetector interface -------------------------------------------
    async def detect(self, frame: np.ndarray, **kwargs) -> DetectionResult:
        start = time.time()
        h, w = frame.shape[:2]
        status = self.read(frame)
        # Single pass: change detection on the status we just read. The
        # change dict (if any) is embedded in extra_data so pipeline
        # consumers can forward it as an event.
        change = self._on_status(status)
        known = sum(1 for s in status.slots if s != TeamSlotStatus.UNKNOWN)
        det_status = (DetectionStatus.SUCCESS if known == self.slots
                      else DetectionStatus.LOW_CONFIDENCE if known > 0
                      else DetectionStatus.NOT_FOUND)
        team_dict = status.to_dict()
        team_dict["changed"] = change is not None
        team_dict["change"] = change
        result = DetectionResult(
            detector_name=self.name,
            status=det_status,
            frame_width=w, frame_height=h,
            processing_time_ms=self._time_detection(start),
            extra_data={"team": team_dict},
        )
        self._last_result = result
        return result

    def get_detection_regions(self, frame_width: int,
                              frame_height: int) -> List[DetectionBox]:
        return [self.region.to_box()]


# ---------------------------------------------------------------------------
# Pipeline facade
# ---------------------------------------------------------------------------


@dataclass
class HUDPipelineOutput:
    updates: List[Dict[str, Any]] = field(default_factory=list)
    events: List[Any] = field(default_factory=list)  # NormalizedEvent
    reads: Dict[str, Any] = field(default_factory=dict)


class HUDPipeline:
    """
    Runs all HUD readers over a frame.

    ``process`` returns updates (alive-count changes, team-status changes)
    and normalized events (zone phase changes). Nothing is emitted for
    unreadable regions.
    """

    def __init__(
        self,
        alive: Optional[AliveCountReader] = None,
        zone: Optional[ZoneTimerReader] = None,
        team: Optional[TeamStatusReader] = None,
    ) -> None:
        self.alive = alive or AliveCountReader()
        self.zone = zone or ZoneTimerReader()
        self.team = team or TeamStatusReader()
        self._log = logger.bind(pipeline="hud")

    def process(self, frame: np.ndarray,
                match_id: Optional[str] = None) -> HUDPipelineOutput:
        out = HUDPipelineOutput()
        alive_update = self.alive.poll(frame)
        if alive_update is not None:
            out.updates.append(alive_update.to_dict())
        zone_event = self.zone.poll(frame, match_id=match_id)
        if zone_event is not None:
            out.events.append(zone_event)
        team_update = self.team.poll(frame)
        if team_update is not None:
            out.updates.append(team_update)
        out.reads = {
            "alive": self.alive.read(frame).value,
            "zone_timer_s": self.zone.read(frame).value,
            "team": [s.value for s in self.team.read(frame).slots],
        }
        return out

    def reset(self) -> None:
        self.alive.reset_state()
        self.zone.reset_state()
        self.team._last_emitted = None


__all__ = [
    "HUDRegion",
    "ReadStatus",
    "HUDRead",
    "OCREngine",
    "TesseractEngine",
    "TemplateDigitEngine",
    "select_ocr_engine",
    "AliveCountReader",
    "AliveCountUpdate",
    "ZoneTimerReader",
    "TeamSlotStatus",
    "TeamStatus",
    "TeamStatusReader",
    "HUDPipeline",
    "HUDPipelineOutput",
]
