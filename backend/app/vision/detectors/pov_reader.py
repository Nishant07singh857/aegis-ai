"""
AEGIS AI - POV State Reader (Phase 29, W1)
==========================================

Per-frame raw read of WHO is being spectated on one observer feed and
what state the spectated team's four players are in. The director
switches BETWEEN feeds; this reader answers the question nobody read
before: INSIDE one feed, which player is the camera on?

Signals (two INDEPENDENT identity signals, by design):

  (a) Highlight / selection marker -- the team-bar card of the
      spectated player is rendered highlighted in spectator mode.
      Detected by reusing ``TeamStatusReader``'s team-bar region and
      comparing each card's border brightness: the highlighted card
      carries a bright selection frame the other cards do not.
  (b) Nameplate OCR -- the spectated player's name is rendered in a
      dedicated on-screen nameplate region; read with an injectable
      OCR engine (default: hud.py's ``TesseractEngine``). The digit
      template engine is deliberately NOT used as a fallback here --
      it cannot read names, and an engine that cannot read the region
      must yield None, not noise.

Per-card status + HP fraction:
  Status comes from ``TeamStatusReader`` UNCHANGED (never re-built).
  AUDIT NOTE: TeamStatusReader does NOT report an HP fraction -- it
  classifies the strip by color ratios only. This reader therefore
  measures the fill fraction itself from the same strip the status
  reader samples: the HP/knock band is left-anchored, so the fraction
  is the rightmost filled column over the strip width, using the
  status reader's own HSV ranges. When the band cannot be found the
  fraction is None -- never derived from the status.

Known limits (honest, measured or structural):
  * TeamStatusReader's band check no longer gates on the HP band's
    width (the D-1 root fix in hud.py): band evidence is measured
    inside the band rows (concentration / contiguous run / purity),
    so on the synthetic builders a live card classifies correctly
    down to ~3% HP fill, and a sub-band fill (<= ~2%, where the
    drawn bar leaves no pixels in the HSV band) reads UNKNOWN there
    instead of the pre-fix DEAD. This reader inherits that floor;
    real-footage calibration of the floor is a scrim task.
  * The nameplate region and the highlight marker are best-guess
    spectator-layout placements (same status as every other
    HUDRegion default in hud.py): they MUST be confirmed/calibrated
    against a real BGMI spectator frame in a scrim before production
    identity decisions rely on them.
  * HP fraction assumes a left-anchored horizontal fill (the BGMI
    team-bar convention); a right-anchored skin would read inverted
    and must be caught at calibration time.

Output is the RAW read (``POVRawRead``): roster binding, two-signal
agreement and HP velocity live in
``app.services.observer_pilot.state.POVStateTracker``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import numpy as np
import structlog

from .hud import (
    HUDRegion,
    OCREngine,
    TeamSlotStatus,
    TeamStatusReader,
    TesseractEngine,
)

logger = structlog.get_logger("aegis.vision.pov_reader")


def _load_pov_status():
    """POVPlayerStatus from observer_pilot.types WITHOUT importing the
    heavy ``app.services`` package __init__ (same decoupled pattern as
    hud.py's _feed_schemas): canonical package import first, file-path
    fallback. types.py itself is stdlib-only."""
    try:
        from app.services.observer_pilot.types import POVPlayerStatus

        return POVPlayerStatus
    except ImportError:
        import importlib.util
        import pathlib
        import sys

        path = (pathlib.Path(__file__).resolve().parent.parent.parent
                / "services" / "observer_pilot" / "types.py")
        name = "aegis_observer_pilot_types"
        if name in sys.modules:
            return sys.modules[name].POVPlayerStatus
        spec = importlib.util.spec_from_file_location(name, str(path))
        if spec is None or spec.loader is None:
            raise
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod.POVPlayerStatus


POVPlayerStatus = _load_pov_status()

_NAME_WHITELIST = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-. "
)

_STATUS_MAP = {
    TeamSlotStatus.ALIVE: POVPlayerStatus.ALIVE,
    TeamSlotStatus.KNOCKED: POVPlayerStatus.KNOCKED,
    TeamSlotStatus.DEAD: POVPlayerStatus.DEAD,
    TeamSlotStatus.UNKNOWN: POVPlayerStatus.UNKNOWN,
}


@dataclass
class RawCardPOV:
    """One team-bar card's raw read (no roster identity yet)."""

    index: int
    status: POVPlayerStatus = POVPlayerStatus.UNKNOWN
    confidence: float = 0.0
    hp_fraction: Optional[float] = None


@dataclass
class POVRawRead:
    """One frame's complete raw POV read."""

    cards: Tuple[RawCardPOV, ...] = ()
    highlight_index: Optional[int] = None
    highlight_scores: Tuple[float, ...] = ()
    nameplate_text: Optional[str] = None
    nameplate_confidence: float = 0.0
    observed_at: float = field(default_factory=time.time)
    detail: Dict[str, object] = field(default_factory=dict)


class POVReader:
    """Reads spectated identity + per-card state from one frame.

    All collaborators are injectable: ``team_reader`` (a real
    TeamStatusReader by default), ``ocr`` (any OCREngine; tests pass a
    fake), and ``nameplate_region`` (HUDRegion pattern, calibrated
    like every other HUD region).
    """

    #: Best-guess BGMI spectator nameplate placement (bottom-centre,
    #: above the spectated player's own HP bar). MUST be confirmed
    #: against real spectator footage -- see module docstring.
    DEFAULT_NAMEPLATE_REGION = HUDRegion(
        name="spectated_nameplate", x=0.40, y=0.895, w=0.20, h=0.055
    )

    def __init__(
        self,
        team_reader: Optional[TeamStatusReader] = None,
        ocr: Optional[OCREngine] = None,
        nameplate_region: Optional[HUDRegion] = None,
        min_name_confidence: float = 0.3,
        highlight_margin: float = 18.0,
        highlight_min_score: float = 110.0,
    ) -> None:
        self.team_reader = team_reader or TeamStatusReader()
        # Default OCR: Tesseract only. When the binary is missing the
        # engine reports unavailable and the nameplate read is None --
        # the highlight signal still works on its own.
        self.ocr: OCREngine = ocr or TesseractEngine()
        self.nameplate_region = nameplate_region or self.DEFAULT_NAMEPLATE_REGION
        self.min_name_confidence = float(min_name_confidence)
        self.highlight_margin = float(highlight_margin)
        self.highlight_min_score = float(highlight_min_score)
        self._log = logger.bind(reader="pov_reader", ocr=self.ocr.name)

    # -- configuration -------------------------------------------------

    def set_nameplate_region(self, region: HUDRegion) -> None:
        self.nameplate_region = region

    # -- main read -------------------------------------------------------

    def read(self, frame: np.ndarray, now: Optional[float] = None) -> POVRawRead:
        """Read one frame. Never raises; unreadable parts come back
        UNKNOWN / None, never guessed."""
        ts = time.time() if now is None else float(now)
        out = POVRawRead(observed_at=ts)
        if frame is None or getattr(frame, "ndim", 0) < 2 or frame.size == 0:
            out.detail["reason"] = "no_frame"
            return out

        # 1) Per-card status from the EXISTING TeamStatusReader.
        try:
            team_status = self.team_reader.read(frame)
            statuses = list(team_status.slots)
            confidences = list(team_status.confidences)
        except Exception as exc:  # a broken status read must not kill POV
            self._log.warning("pov_team_status_failed", error=str(exc)[:200])
            statuses = [TeamSlotStatus.UNKNOWN] * self.team_reader.slots
            confidences = [0.0] * self.team_reader.slots

        # 2) Card crops (same region/split as TeamStatusReader) for the
        #    highlight marker + HP fill measurement.
        cards = self._card_crops(frame)

        highlight_scores = tuple(self._highlight_score(c) for c in cards)
        out.highlight_scores = highlight_scores
        out.highlight_index = self._pick_highlight(highlight_scores)

        raw_cards = []
        for i in range(self.team_reader.slots):
            status = (
                _STATUS_MAP.get(statuses[i], POVPlayerStatus.UNKNOWN)
                if i < len(statuses)
                else POVPlayerStatus.UNKNOWN
            )
            conf = float(confidences[i]) if i < len(confidences) else 0.0
            hp_fraction = None
            if i < len(cards) and status in (
                POVPlayerStatus.ALIVE,
                POVPlayerStatus.KNOCKED,
            ):
                hp_fraction = self._hp_fraction(cards[i], status)
            raw_cards.append(
                RawCardPOV(index=i, status=status, confidence=conf,
                           hp_fraction=hp_fraction)
            )
        out.cards = tuple(raw_cards)

        # 3) Nameplate OCR (independent identity signal).
        text, conf = self._read_nameplate(frame)
        out.nameplate_confidence = conf
        if text:
            out.nameplate_text = text
        return out

    # -- card geometry (mirrors TeamStatusReader.read's split) ---------

    def _card_crops(self, frame: np.ndarray):
        bar = self.team_reader.region.crop(frame)
        if bar is None:
            return []
        w = bar.shape[1]
        slot_w = w / self.team_reader.slots
        crops = []
        for i in range(self.team_reader.slots):
            x1, x2 = int(i * slot_w), int((i + 1) * slot_w)
            card = bar[:, x1:x2, :]
            if card.size:
                crops.append(card)
        return crops

    # -- highlight marker --------------------------------------------------

    @staticmethod
    def _highlight_score(card: np.ndarray) -> float:
        """Mean grayscale brightness of the card's border ring.

        A selection frame is drawn ON the card border; interior
        brightness varies with card content (names, avatars), so only
        the outermost ~3 px ring is scored."""
        import cv2

        gray = cv2.cvtColor(card, cv2.COLOR_BGR2GRAY)
        t = 3
        ring = np.concatenate([
            gray[:t, :].ravel(), gray[-t:, :].ravel(),
            gray[:, :t].ravel(), gray[:, -t:].ravel(),
        ])
        return float(np.mean(ring)) if ring.size else 0.0

    def _pick_highlight(self, scores: Tuple[float, ...]) -> Optional[int]:
        """The highlighted card, or None when no card stands out.

        Both gates must pass: the winner clears an absolute brightness
        floor AND beats the runner-up by a margin. A uniformly bright
        or uniformly dark bar therefore yields None (UNKNOWN), never
        an argmax guess."""
        if not scores:
            return None
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        best = order[0]
        if scores[best] < self.highlight_min_score:
            return None
        if len(order) > 1 and scores[best] - scores[order[1]] < self.highlight_margin:
            return None
        return best

    # -- HP fill fraction ---------------------------------------------------

    def _hp_fraction(self, card: np.ndarray,
                     status: POVPlayerStatus) -> Optional[float]:
        """Left-anchored fill fraction of the card's HP/knock band.

        Uses the SAME strip and HSV ranges as TeamStatusReader, so the
        fraction describes exactly the band that produced the status.
        Returns None when no band columns are found."""
        import cv2

        h = card.shape[0]
        strip_y = int(h * (1.0 - self.team_reader.hp_strip_fraction))
        strip = card[strip_y:h, :, :]
        if strip.size == 0:
            return None
        hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
        lo, hi = (
            self.team_reader.healthy_hsv
            if status == POVPlayerStatus.ALIVE
            else self.team_reader.knocked_hsv
        )
        mask = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
        # A column counts as filled when the band occupies a real slice
        # of it (not a stray pixel of background in the same hue).
        min_col_px = max(2, int(0.10 * mask.shape[0]))
        col_counts = np.count_nonzero(mask, axis=0)
        filled = np.nonzero(col_counts >= min_col_px)[0]
        if filled.size == 0:
            return None
        frac = float(filled.max() + 1) / float(mask.shape[1])
        return round(min(1.0, max(0.0, frac)), 3)

    # -- nameplate OCR --------------------------------------------------------

    def _read_nameplate(self, frame: np.ndarray):
        """(text, confidence) or (None, 0.0). Text is returned only
        when the engine is available and clears the confidence floor."""
        if not getattr(self.ocr, "available", False):
            return None, 0.0
        roi = self.nameplate_region.crop(frame)
        if roi is None:
            return None, 0.0
        try:
            result = self.ocr.read_text(roi, whitelist=_NAME_WHITELIST)
        except Exception as exc:
            self._log.warning("pov_nameplate_ocr_failed", error=str(exc)[:200])
            return None, 0.0
        if not result:
            return None, 0.0
        text, conf = result
        text = (text or "").strip()
        if not text or float(conf) < self.min_name_confidence:
            return None, float(conf or 0.0)
        return text, float(conf)


__all__ = ["POVReader", "POVRawRead", "RawCardPOV"]
