# backend/app/vision/band_change.py
"""
AEGIS AI - Killfeed band change pre-filter (Phase 28).

Purpose: the multi-feed monitor pays a FULL killfeed parse on almost
every sample of a quiet feed. Phase 26/28 measurements on real frames
(this VM class): the detector's exact-pixel band-hash cache almost
never hits on live feeds because the translucent killfeed band shows
moving gameplay behind it, so ~100% of quiet samples paid a full
parse (median ~0.49 s, mostly the legacy OCR fallback) for a band in
which nothing structural changed.

This filter answers ONE narrow question per feed, cheaply:

    "Does the killfeed band's ENTRY STRUCTURE look the same as the
    last time I looked?"  -> observe() returns False (skip is safe)
    "It changed / first sight / anything doubtful" -> True (parse)

It is a pure pre-filter: it keeps NO voting state, NO candidate
state, and makes NO claim about what is in the band — only whether
the band's structure moved since the previous observe() call for
that feed. The monitor owns voting (>=2 sightings) and must bypass
this filter for feeds with pending candidates and keep a periodic
forced full parse (see the Phase 28 integration note).

Method (structural signature, NOT an exact-byte hash):
  * Crop the same top-left band the KillFeedDetector scans
    (``DEFAULT_KILL_FEED_REGION``, Phase 23 geometry).
  * Grayscale + downscale to a fixed grid (54 x 50), then aggregate
    into row cells (~4 px of 360p frame height each — just under one
    entry row pitch of ~21 px there are ~5 cells per row).
  * Per row cell, five background-robust features:
      bright_frac  - fraction of pixels >= 130 (glyph strokes; dull
                     glyphs sit at ~120-210 grey, Phase 23)
      dark_frac    - fraction of pixels <= 90  (the dark entry bar)
      grad_energy  - mean horizontal gradient (glyph texture)
      left_bright  - bright_frac in the leftmost ~15% (logo column)
      bar_score    - 2 * min(bright_frac, dark_frac): high ONLY where
                     a dark bar and bright glyphs coexist in the same
                     row cell — the entry's structural fingerprint.
                     Plain gameplay rows are bright OR dark, rarely
                     both at once, so background scene cuts (which
                     move bright/dark fractions a lot) move bar_score
                     far less than an entry appearing does.
    A new entry is a dark bar + a row of bright glyphs appearing
    where plain gameplay was: it moves its row cells' bar_score
    abruptly, including when it stacks BELOW an entry that is still
    on screen (the row-profile is why this is per-row and not one
    global band hash).
  * Change score = weighted mean per-cell feature distance across
    the band PLUS the single largest per-row jump (max over row
    cells of |d bar_score| + 0.5*(|d bright| + |d dark|)). Either
    term over its threshold -> True.
  * ANY error, bad frame, or missing previous signature -> True
    (conservative: a wrong True costs one parse; a wrong False can
    cost a missed kill).

The signature is updated on EVERY observe() call: entries pop in
abruptly (no fade), so step-to-step comparison catches appearances
and expiries while slow background drift never accumulates into a
false trigger.

Measured on real harvested frames (backend/scripts/phase28_profile.py
-> phase28_profile_report.json): see that report's ``filter`` section
for the false-skip rate, quiet skip rate and observe() cost at the
shipped thresholds. Thresholds below are the values that sweep
selected (zero false-skips on the measured MUST_TRUE pairs, best
skip rate); they are constructor parameters so a future re-measure
can retune without touching callers.
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import structlog

try:  # same geometry the detector scans — never a private copy that can drift
    from .detectors.killfeed import DEFAULT_KILL_FEED_REGION
except Exception:  # pragma: no cover - repo-layout guard
    DEFAULT_KILL_FEED_REGION = (0.0, 0.24, 0.34, 0.52)

log = structlog.get_logger("aegis.vision.band_change")

_GRID_W = 54          # downscaled band width  (band is ~217 px at 360p)
_GRID_H = 50          # downscaled band height (band is ~101 px at 360p)
_ROWS = 25            # row cells over the band height (~4 px each at 360p)
_LEFT_COLS = 8        # leftmost columns of the grid (~15%: logo column)


class BandChangeFilter:
    """Per-feed killfeed-band structural change filter.

    ``observe(feed_key, frame) -> bool``: True = run the full parse;
    False = band structure unchanged since the last observe() for
    this feed, skipping is safe. Stateless across feeds except the
    last signature per feed_key. No voter/candidate state, by design.
    """

    #: Feature weights for the mean distance (bar_score counts most).
    _WEIGHTS = (0.5, 0.5, 0.5, 0.5, 1.0)

    def __init__(
        self,
        region=DEFAULT_KILL_FEED_REGION,
        threshold: float = 0.05,
        row_threshold: float = 0.32,
    ) -> None:
        self.region = tuple(float(v) for v in region)
        self.threshold = float(threshold)
        self.row_threshold = float(row_threshold)
        self._prev: Dict[str, np.ndarray] = {}

    # -- signature ----------------------------------------------------

    def signature(self, frame) -> Optional[np.ndarray]:
        """Structural signature of the band, shape (_ROWS, 5), or
        None when the frame/band cannot be read (caller treats None
        as 'changed')."""
        try:
            import cv2

            if frame is None or getattr(frame, "ndim", 0) < 2:
                return None
            h, w = frame.shape[:2]
            x1r, y1r, x2r, y2r = self.region
            x1, y1 = int(w * x1r), int(h * y1r)
            x2, y2 = int(w * x2r), int(h * y2r)
            if x2 - x1 < 16 or y2 - y1 < 16:
                return None
            band = frame[max(0, y1):y2, max(0, x1):x2]
            if band is None or band.size == 0:
                return None
            if band.ndim == 3:
                gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
            else:
                gray = band.astype(np.uint8)
            small = cv2.resize(gray, (_GRID_W, _GRID_H),
                               interpolation=cv2.INTER_AREA)
            f = small.astype(np.float32) / 255.0
            bright = (small >= 130).astype(np.float32)
            dark = (small <= 90).astype(np.float32)
            grad = np.zeros_like(f)
            grad[:, 1:] = np.abs(np.diff(f, axis=1))
            cell_h = _GRID_H // _ROWS
            sig = np.zeros((_ROWS, 5), dtype=np.float32)
            for r in range(_ROWS):
                rows = slice(r * cell_h, (r + 1) * cell_h)
                b = float(bright[rows].mean())
                d = float(dark[rows].mean())
                sig[r, 0] = b
                sig[r, 1] = d
                sig[r, 2] = float(grad[rows].mean())
                sig[r, 3] = float(bright[rows, :_LEFT_COLS].mean())
                sig[r, 4] = min(b, d) * 2.0
            return sig
        except Exception as exc:  # conservative: caller parses
            log.warning("band_signature_failed", error=str(exc)[:160])
            return None

    @staticmethod
    def distance(a: Optional[np.ndarray], b: Optional[np.ndarray]) -> dict:
        """Change score between two signatures.

        Returns {"dist": weighted mean per-cell feature distance,
                 "row_jump": largest single-row |d bar_score| +
                 0.5*(|d bright| + |d dark|)}.
        Missing signatures score as maximally changed.
        """
        if a is None or b is None or a.shape != b.shape:
            return {"dist": 1.0, "row_jump": 1.0}
        diff = np.abs(a.astype(np.float32) - b.astype(np.float32))
        w = np.asarray(BandChangeFilter._WEIGHTS, dtype=np.float32)
        dist = float((diff * w).sum() / (diff.shape[0] * float(w.sum())))
        row_jump = float((diff[:, 4] + 0.5 * (diff[:, 0] + diff[:, 1])).max())
        return {"dist": dist, "row_jump": row_jump}

    # -- public API -----------------------------------------------------

    def observe(self, feed_key: str, frame) -> bool:
        """True = band structure changed / unknown -> full parse.
        False = unchanged -> safe to skip. Doubt always resolves to
        True."""
        try:
            sig = self.signature(frame)
            if sig is None:
                return True
            prev = self._prev.get(feed_key)
            self._prev[feed_key] = sig
            if prev is None:
                return True
            d = self.distance(prev, sig)
            return bool(
                d["dist"] > self.threshold or d["row_jump"] > self.row_threshold
            )
        except Exception as exc:  # never let the filter suppress a parse
            log.warning("band_observe_failed", feed_key=feed_key,
                        error=str(exc)[:160])
            return True

    def reset(self, feed_key: Optional[str] = None) -> None:
        """Forget one feed's signature (next observe -> True), or all."""
        if feed_key is None:
            self._prev.clear()
        else:
            self._prev.pop(feed_key, None)


__all__ = ["BandChangeFilter"]
