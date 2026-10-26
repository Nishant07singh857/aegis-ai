# backend/app/vision/detectors/icon_matcher.py
"""
AEGIS AI - Kill-feed icon template matcher (Phase 23, W2).

Matches REAL weapon / marker glyphs in kill-feed entry crops against the
W1 template library (``backend/app/vision/icons/`` — 28 raw crops harvested
from real VOD frames, see ``manifest.json`` and
``data/phase23_work/LAYOUT_REPORT.md``).

Method: multi-scale normalised cross-correlation (``cv2.matchTemplate``
with ``TM_CCOEFF_NORMED``). NCC is invariant to linear brightness/contrast
shifts, which is what "dull" broadcast icons need: the glyphs sit at
~120-210 grey on a ~30-70 grey bar, never pure white (LAYOUT_REPORT §6).

Honesty rules (hard):
  * Classification uses ONLY the identified families: ar_a, ar_b,
    smg_compact, sniper_bolt, dmr_long, frag_grenade, pistol_t (weapon
    slot) and marker_knock / marker_headshot (marker slot).
  * ``unknown_*`` templates COMPETE in matching (a dome/wheel/burst glyph
    must be able to WIN) but are never emitted as a weapon: if the best
    match is an ``unknown_*`` family, or the best score is below the
    configured threshold, the weapon is ``None`` (honest unknown).
  * Phase 32 STRICT MARGIN GATE (abstention ON, owner decision): even
    an identified family above threshold emits NO weapon unless its
    score beats the best other family by >= 0.15 (``MARGIN_THRESHOLD``,
    ``gated_weapon``). Below that the weapon is ``None`` with
    ``weapon_abstained=True``. This is the single choke point for
    production weapon labels — the kill-feed parser and the
    multi-feed monitor both read ``IconMatch.weapon``, so no
    downstream consumer can bypass the gate to a raw label.
  * NEVER a specific gun model. At 360p an M416 and an M416-like profile
    are indistinguishable; families map to CLASS-level weapon strings
    only ("AR", "SMG", "Sniper", "DMR", "Frag Grenade", "Pistol") and
    the payload carries ``weapon_granularity="class"``.

Dull-icon preprocessing (config-switchable so W5 can A/B it):
  ``icon_preprocess`` in {"bin", "clahe", "histeq", "none"} is applied
  to the INPUT crop and identically to the templates at load time,
  followed by an upscale (default 4x, INTER_CUBIC) before correlation.
  "bin" is adaptive binarisation (threshold = max(85, median + 30),
  the W1 harvester's lo-threshold rule): it matches glyph SHAPE only,
  which is what survives 4:2:0 dullness. Default = "none" (raw),
  picked on the HARVEST-VOD labelled entries only
  (labels_day1/day2/pmgc1, whole-entry crops, this matcher):
  family top-1 none 0.64 (all 138) / 0.59 (116 non-template-source)
  vs bin 0.46 / 0.41 and clahe 0.62 / 0.55. An earlier draft of this
  module reported bin ahead on a slot-crop eval that could not be
  reproduced on whole entries; the shipped default follows the
  reproducible eval, and W5 A/Bs every mode on the held-out set
  (never touched by this tuning).

No cv2 at import time beyond a lazy import inside methods, so importing
this module never breaks a minimal environment; a matcher that cannot
load its library simply reports ``available == False`` and returns
no-match results (weapon stays None — never a guess).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import structlog

log = structlog.get_logger("aegis.vision.icon_matcher")

#: Directory holding manifest.json + template PNGs (W1 library).
DEFAULT_ICONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icons"
)

#: Identified weapon-slot families -> CLASS-level weapon id (the
#: NormalizedEvent.weapon contract; lowercase ids match the replay
#: highlight triggers and analytics weapon tables).
#: (No model names — indistinguishable at 360p, see module docstring.)
FAMILY_TO_CLASS: Dict[str, str] = {
    "ar_a": "ar",
    "ar_b": "ar",
    "smg_compact": "smg",
    "sniper_bolt": "sniper",
    "dmr_long": "dmr",
    "frag_grenade": "frag_grenade",
    "pistol_t": "pistol",
}

#: Marker-slot families.
MARKER_FAMILIES = ("marker_knock", "marker_headshot")

#: Families that may WIN a match but never become a weapon label.
def _is_unknown(family: str) -> bool:
    return family.startswith("unknown_")


#: Phase 32 — strict margin gate (owner decision, abstention ON).
#: A weapon label ships ONLY when the best family's NCC score beats
#: the best OTHER family by at least this margin. Below it the label
#: is UNKNOWN (abstained), never a guessed gun name. Diagnostic on
#: held-out real 360p entries (Phase 23 W5): margin >= 0.15 ->
#: 79% precision at 24/357 coverage, vs ~48.2% class accuracy
#: ungated. Coverage loss is the EXPECTED trade-off; loosening this
#: gate to buy coverage back is not a silent option — it re-ships
#: the ~48% labels this gate exists to stop.
MARGIN_THRESHOLD = 0.15


def gated_weapon(
    family: Optional[str],
    score: float,
    second_score: float,
    *,
    threshold: float = 0.55,
    margin_threshold: float = MARGIN_THRESHOLD,
) -> Optional[str]:
    """Pure margin-gate decision for one candidate (Phase 32).

    Returns the class-level weapon iff the family is identified, the
    score clears ``threshold``, AND ``score - second_score`` clears
    ``margin_threshold`` (boundary inclusive: margin == threshold
    is confident). Anything else -> None (UNKNOWN/abstain). Pure so
    the gate is unit-testable without cv2 or the template library;
    :meth:`IconMatcher.match_candidates` applies exactly this.
    """
    if family is None or family not in FAMILY_TO_CLASS:
        return None
    if float(score) < float(threshold):
        return None
    if float(score) - float(second_score) < float(margin_threshold):
        return None
    return FAMILY_TO_CLASS[family]


@dataclass
class IconMatch:
    """Result of one template-matching pass over a crop."""

    family: Optional[str] = None        # best family overall (may be unknown_*)
    score: float = 0.0                   # NCC score of the best match
    second_score: float = 0.0            # best score of any OTHER family
    location: Optional[Tuple[int, int, int, int]] = None  # (x, y, w, h) in crop px
    weapon: Optional[str] = None         # class string iff identified + >= threshold + margin gate
    weapon_family: Optional[str] = None  # family iff weapon is not None
    #: Phase 32: True iff an identified family cleared the score
    #: threshold but the MARGIN gate abstained (margin < threshold).
    #: Distinct from unknown-glyph / low-score nulls, so downstream
    #: can count gate coverage honestly (labelled vs abstained).
    weapon_abstained: bool = False

    @property
    def margin(self) -> float:
        return self.score - self.second_score


@dataclass
class MarkerMatch:
    """Marker-slot check result."""

    knock: bool = False
    headshot: bool = False
    knock_score: float = 0.0
    headshot_score: float = 0.0


class IconMatcher:
    """Template matcher over the W1 kill-feed icon library.

    Parameters
    ----------
    icons_dir:
        Library directory (manifest.json + PNGs). Defaults to the
        packaged ``app/vision/icons``.
    preprocess:
        "bin" | "clahe" | "histeq" | "none" — normalisation applied to
        templates (at load) and to every query crop (at match).
    threshold:
        Minimum NCC score for a weapon family to be emitted. Below it
        (or when an unknown_* family wins) the weapon is None.
    marker_threshold:
        Minimum NCC score for a marker glyph to count as present.
    upscale:
        Integer upscale factor applied before correlation (2-4x per the
        Phase 23 brief; dull tiny glyphs correlate more stably upscaled).
    scales:
        Template scale jitter searched per template (native 360p scale
        is ~1.0; the jitter absorbs resampling differences when the
        production capture resolution differs from 360p).
    """

    def __init__(
        self,
        icons_dir: Optional[str] = None,
        preprocess: str = "none",
        threshold: float = 0.55,
        marker_threshold: float = 0.50,
        upscale: int = 4,
        scales: Tuple[float, ...] = (0.85, 1.0, 1.15),
        margin_threshold: float = MARGIN_THRESHOLD,
    ) -> None:
        self.icons_dir = icons_dir or DEFAULT_ICONS_DIR
        mode = str(preprocess or "none").strip().lower()
        self.preprocess = mode if mode in ("bin", "clahe", "histeq", "none") else "none"
        self.threshold = float(threshold)
        self.marker_threshold = float(marker_threshold)
        #: Phase 32 strict margin gate (default ON at 0.15). Appended
        #: last so existing positional construction is unaffected.
        self.margin_threshold = float(margin_threshold)
        self.upscale = max(1, int(upscale))
        self.scales = tuple(float(s) for s in scales) or (1.0,)
        self._templates: Dict[str, list] = {}   # family -> [prepared arrays]
        self._raw_sizes: Dict[str, Tuple[int, int]] = {}
        self._loaded = False
        self._available = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Library loading / preprocessing
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        self._ensure_loaded()
        return self._available

    def families(self) -> List[str]:
        self._ensure_loaded()
        return sorted(self._templates.keys())

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            try:
                import cv2  # noqa: F401  (availability probe)
            except Exception as exc:
                log.warning("icon_matcher_no_cv2", error=str(exc))
                return
            manifest_path = os.path.join(self.icons_dir, "manifest.json")
            try:
                with open(manifest_path, "r", encoding="utf-8") as fh:
                    manifest = json.load(fh)
            except Exception as exc:
                log.warning("icon_matcher_no_manifest", path=manifest_path,
                            error=str(exc))
                return
            loaded = 0
            for entry in manifest.get("templates", []):
                family = str(entry.get("weapon_id") or "")
                fname = str(entry.get("file") or "")
                if not family or not fname:
                    continue
                path = os.path.join(self.icons_dir, fname)
                arr = self._read_gray(path)
                if arr is None:
                    log.warning("icon_template_missing", path=path)
                    continue
                self._raw_sizes[fname] = (int(arr.shape[1]), int(arr.shape[0]))
                self._templates.setdefault(family, []).append(
                    self._prepare(arr)
                )
                loaded += 1
            self._available = loaded > 0 and bool(self._templates)
            log.info(
                "icon_matcher_loaded",
                templates=loaded,
                families=sorted(self._templates.keys()),
                preprocess=self.preprocess,
                threshold=self.threshold,
            )

    @staticmethod
    def _read_gray(path: str):
        import cv2
        import numpy as np

        arr = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if arr is None or arr.size == 0:
            return None
        return arr.astype(np.uint8)

    def _prepare(self, gray):
        """Contrast-normalise (config) + upscale one grayscale image."""
        import cv2
        import numpy as np

        out = gray
        if self.preprocess == "bin":
            # Adaptive binarisation (W1 harvester lo-threshold rule):
            # glyphs are the pixels clearly above the bar's median.
            thr = max(85, int(np.median(out)) + 30)
            out = np.where(out > thr, 255, 0).astype(np.uint8)
        elif self.preprocess == "clahe":
            # Small tiles: templates are ~7-13 px tall; the default 8x8
            # grid would be sub-pixel. 2x2 keeps CLAHE local but sane.
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(2, 2))
            out = clahe.apply(out)
        elif self.preprocess == "histeq":
            out = cv2.equalizeHist(out)
        if self.upscale != 1:
            out = cv2.resize(
                out,
                (max(2, out.shape[1] * self.upscale),
                 max(2, out.shape[0] * self.upscale)),
                interpolation=cv2.INTER_CUBIC,
            )
        return out

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def _effective_scales(self, query_h: int) -> Tuple[float, ...]:
        """Scale set for a query crop, anchored when the crop is
        clearly above native 360p scale.

        Templates are native-360p glyphs; the configured jitter
        (+/-15%) absorbs resampling noise at that scale. But the live
        pipeline preprocesses frames to 960x540 (1.5x) and device
        captures run 720p+ (2x+): glyphs there are proportionally
        larger than any jitter. A kill-feed strip is ~24 px tall at
        native scale, so a prepared query much taller than that
        implies a uniform frame scale of roughly ``query_h / (24 *
        upscale)``; search that scale (with the same jitter) as well.
        Slot-sized crops (near glyph height) get no anchor — their
        scale is already native.
        """
        scales = list(self.scales)
        native_prepared = 24.0 * max(1, self.upscale)
        if query_h > native_prepared * 1.3:
            base = query_h / native_prepared
            if base <= 4.5:
                for s in self.scales:
                    scales.append(base * s)
        return tuple(sorted(set(round(s, 3) for s in scales)))

    def _slide_best(self, query, families: List[str]):
        """Best NCC per family: {family: (score, (x, y, w, h) in query px)}.

        Coordinates are reported in the ORIGINAL (pre-upscale) crop
        space so callers can split OCR tokens around the icon slot.

        Phase 28 note: a resized-template cache was measured here and
        removed — interleaved A/B on a real strip: warm cache median
        251.6 ms vs no-cache 233.1 ms per match_candidates call (VM
        noise band, no gain): cv2.matchTemplate sliding dominates
        this cost, the template resizes are only ~5% of it.
        """
        import cv2

        results: Dict[str, Tuple[float, Optional[Tuple[int, int, int, int]]]] = {}
        qh, qw = query.shape[:2]
        scales = self._effective_scales(qh)
        for family in families:
            best_score = -1.0
            best_loc: Optional[Tuple[int, int, int, int]] = None
            for tmpl in self._templates.get(family, []):
                tmpl_scales = scales
                # Adaptive fit: a tight glyph crop can be slightly
                # SMALLER than the template (different padding) — add
                # a just-fits scale instead of skipping the template.
                fit = min(qw / max(1, tmpl.shape[1]), qh / max(1, tmpl.shape[0]))
                if fit < min(scales):
                    tmpl_scales = scales + (round(max(0.3, fit * 0.98), 3),)
                for scale in tmpl_scales:
                    if scale == 1.0:
                        t = tmpl
                    else:
                        t = cv2.resize(
                            tmpl,
                            (max(2, int(tmpl.shape[1] * scale)),
                             max(2, int(tmpl.shape[0] * scale))),
                            interpolation=cv2.INTER_CUBIC,
                        )
                    if t.shape[0] > qh or t.shape[1] > qw:
                        continue
                    res = cv2.matchTemplate(query, t, cv2.TM_CCOEFF_NORMED)
                    _, mx, _, ml = cv2.minMaxLoc(res)
                    if float(mx) > best_score:
                        best_score = float(mx)
                        ux, uy = int(ml[0]), int(ml[1])
                        best_loc = (
                            int(round(ux / self.upscale)),
                            int(round(uy / self.upscale)),
                            int(round(t.shape[1] / self.upscale)),
                            int(round(t.shape[0] / self.upscale)),
                        )
            results[family] = (best_score, best_loc)
        return results

    def match(self, crop) -> IconMatch:
        """Match a weapon-slot (or whole-entry) crop against the library.

        Returns an IconMatch whose ``weapon`` is set ONLY when the best
        family is an identified weapon family AND its score clears the
        threshold. An ``unknown_*`` win or a low score yields
        ``weapon=None`` — the raw best family/score are still reported
        for diagnostics.
        """
        ranked = self.match_candidates(crop, top_n=1)
        return ranked[0] if ranked else IconMatch()

    def match_candidates(self, crop, top_n: int = 4) -> List[IconMatch]:
        """Ranked per-family matches for a crop, best first.

        The layout parser walks this list: a higher-scored match whose
        LOCATION is structurally impossible (e.g. it overlaps an OCR
        word — a team logo mimicking the wheel glyph) is rejected and
        the next candidate is tried, instead of failing the whole row.
        Each candidate's ``weapon`` follows the same honesty rule as
        :meth:`match` (identified family + score >= threshold + the
        Phase 32 margin gate). NOTE on the margin of a non-top
        candidate: its best OTHER family is the top family, so its
        margin is negative and it can never carry a weapon — only
        the top candidate can be confident, and only when it beats
        the runner-up by >= margin_threshold. A structurally-rejected
        top candidate therefore abstains the weapon rather than
        promoting a close runner-up into a guessed gun name; the
        entry (names, knock) still ships, only the weapon is UNKNOWN.
        """
        self._ensure_loaded()
        if not self._available or crop is None:
            return []
        gray = self._to_gray(crop)
        if gray is None or gray.size == 0:
            return []
        query = self._prepare(gray)
        families = [f for f in self._templates if f not in MARKER_FAMILIES]
        if not families:
            return []
        per_family = self._slide_best(query, families)
        ranked = sorted(per_family.items(), key=lambda kv: -kv[1][0])
        out: List[IconMatch] = []
        for rank, (family, (score, loc)) in enumerate(ranked[: max(1, top_n)]):
            # Best score of any OTHER family: the runner-up for the
            # top candidate, the top score for every other candidate
            # (previously every candidate wrongly shared ranked[1]).
            if rank == 0:
                second = ranked[1][1][0] if len(ranked) > 1 else 0.0
            else:
                second = ranked[0][1][0]
            cand = IconMatch(
                family=family,
                score=max(0.0, score),
                second_score=max(0.0, second),
                location=loc,
            )
            cand.weapon = gated_weapon(
                family,
                score,
                second,
                threshold=self.threshold,
                margin_threshold=self.margin_threshold,
            )
            if cand.weapon is not None:
                cand.weapon_family = family
            elif (
                family in FAMILY_TO_CLASS
                and score >= self.threshold
                and score - second < self.margin_threshold
            ):
                # Identified + above score threshold, but the margin
                # gate abstained: record it (not a low-score null).
                cand.weapon_abstained = True
            out.append(cand)
        return out

    def match_markers(self, crop) -> MarkerMatch:
        """Check a marker-slot crop for the knock / headshot glyphs.

        Window-slide matching with two guards (Phase 23 W2 merged
        validation — each guard kills a measured failure mode):

        * LOCATION GATES: a family's window match counts only when
          (a) it is scale-coherent — its width matches the family's
          native width at the window's glyph scale (a native-width
          match inside a 3x window is letter strokes, not a glyph),
          and (b) the bright pixels inside its location are PALE
          (saturation <= 150) — on a plain kill the window holds the
          victim logo + name fragments, which raw-match the knock
          template at ~0.74 median NCC, and the saturated colour
          logo (GodLike's gold ring is the documented headshot
          confusable) is vetoed by (b). Tight per-blob matching is
          NOT used as the decider: blob extraction fragments dull
          glyphs, and a fragment of the knock figure argmax-matches
          the headshot ring.
        * SAME-GLYPH SUPPRESSION: when both families clear the
          threshold at (nearly) the same location, one glyph is
          casting both matches (the knock figure and the headshot
          ring cross-match at 0.71-0.88 on dull crops) — only the
          stronger counts. Two REAL markers sit side by side, so
          distinct locations keep both flags.

        No qualifying match = "no marker" (plain kill) — never an
        error.
        """
        import numpy as np

        self._ensure_loaded()
        out = MarkerMatch()
        if not self._available or crop is None:
            return out
        gray = self._to_gray(crop)
        if gray is None or gray.size == 0:
            return out
        families = [f for f in MARKER_FAMILIES if f in self._templates]
        if not families:
            return out
        query = self._prepare(gray)
        per_family = self._slide_best(query, families)
        if "marker_knock" in per_family:
            out.knock_score = max(0.0, per_family["marker_knock"][0])
        if "marker_headshot" in per_family:
            out.headshot_score = max(0.0, per_family["marker_headshot"][0])

        # Bright-pixel saturation inside each match location: white
        # glyphs are pale; a match sitting on a saturated colour logo
        # (GodLike's gold ring) is vetoed. (Blob-overlap gating was
        # tried first and dropped: at 3x the dull ring's bright core
        # fragments into sub-filter slivers, so the blob never forms
        # even when the window match is 0.99.)
        import cv2

        lo_thr = max(85, int(np.median(gray)) + 30)
        hsv = None
        if crop is not None and getattr(crop, "ndim", 0) == 3:
            try:
                hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            except Exception:
                hsv = None

        def _loc_saturation(loc) -> float:
            if hsv is None:
                return 0.0
            lx, ly, lw, lh = loc
            x1, y1 = max(0, lx), max(0, ly)
            x2 = min(gray.shape[1], lx + lw)
            y2 = min(gray.shape[0], ly + lh)
            if x2 <= x1 or y2 <= y1:
                return 0.0
            bright = gray[y1:y2, x1:x2] > lo_thr
            if not bright.any():
                return 0.0
            return float(hsv[y1:y2, x1:x2][:, :, 1][bright].mean())

        def _gated(family: str) -> bool:
            score, loc = per_family.get(family, (0.0, None))
            if score < self.marker_threshold or loc is None:
                return False
            lx, ly, lw, lh = loc
            # Scale coherence: a real marker sits at the window's
            # glyph scale (window height ~ one entry strip). A match
            # at native template width inside a 3x window is letter
            # strokes, not a glyph — the sniper-strip knock shadow.
            tmpls = self._templates.get(family, [])
            if tmpls:
                native_w = float(
                    np.median([t.shape[1] for t in tmpls])
                ) / max(1, self.upscale)
                glyph_scale = max(float(gray.shape[0]), 24.0) / 24.0
                expected_w = native_w * glyph_scale
                if expected_w > 0 and not (0.55 <= lw / expected_w <= 1.8):
                    return False
            if _loc_saturation(loc) > 150.0:
                return False  # colour logo/chip, not a white glyph
            return True

        out.knock = _gated("marker_knock")
        out.headshot = _gated("marker_headshot")
        if out.knock and out.headshot:
            k_loc = per_family["marker_knock"][1]
            h_loc = per_family["marker_headshot"][1]
            if k_loc is not None and h_loc is not None:
                kx, _, kw, _ = k_loc
                hx, _, hw, _ = h_loc
                overlap = max(0, min(kx + kw, hx + hw) - max(kx, hx))
                min_w = min(kw, hw)
                centre_dist = abs((kx + kw / 2.0) - (hx + hw / 2.0))
                lo = min(out.knock_score, out.headshot_score)
                hi = max(out.knock_score, out.headshot_score)
                same_glyph = overlap > 0.5 * min_w or (
                    centre_dist < 0.75 * min_w and hi > 0 and lo / hi > 0.8
                )
                if same_glyph:
                    if out.knock_score >= out.headshot_score:
                        out.headshot = False
                    else:
                        out.knock = False
        return out

    def _marker_blobs(self, crop_bgr, gray):
        """Glyph blobs in a marker window.

        Returns [(tight crop, bright-pixel saturation, (x1, y1, x2, y2))].
        Lo-threshold components (gray > max(85, median + 30), the W1
        rule) filtered to glyph-sized boxes; sizes scale with the
        window height (~24 px per entry strip at 360p).
        """
        import cv2
        import numpy as np

        # Window height ~ strip height (24 px at 360p); a glyph-sized
        # crop passed directly must not shrink the size window below
        # the glyph itself, hence the 0.85 floor.
        s = min(2.5, max(0.85, float(gray.shape[0]) / 24.0))
        lo_thr = max(85, int(np.median(gray)) + 30)
        mask = (gray > lo_thr).astype(np.uint8) * 255
        # The closing kernel scales with s like every other constant
        # here — at 3x capture a fixed (2,3) kernel leaves thin glyph
        # strokes (headshot ring arcs) fragmented below the size floor.
        close_k = (max(2, int(2 * s)), max(3, int(3 * s)))
        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones(close_k, np.uint8))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(closed, 8)
        hsv = None
        if crop_bgr is not None and getattr(crop_bgr, "ndim", 0) == 3:
            try:
                hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
            except Exception:
                hsv = None
        blobs = []
        for i in range(1, n):
            x, y, w, h, area = (int(v) for v in stats[i])
            if h < 4 * s or area < 6 * s * s or w < 5 * s or w > 24 * s:
                continue
            x1, y1 = max(0, x - 2), max(0, y - 2)
            x2 = min(gray.shape[1], x + w + 2)
            y2 = min(gray.shape[0], y + h + 2)
            blob = crop_bgr[y1:y2, x1:x2] if crop_bgr is not None else gray[y1:y2, x1:x2]
            if blob is None or blob.size == 0:
                continue
            # Saturation of the BRIGHT (glyph-stroke) pixels only,
            # never of the surrounding bar/background: white markers
            # are pale, colour logos are saturated.
            sat = 0.0
            if hsv is not None:
                bright_px = gray[y1:y2, x1:x2] > lo_thr
                if bright_px.any():
                    sat = float(hsv[y1:y2, x1:x2][:, :, 1][bright_px].mean())
            blobs.append((blob, sat, (x1, y1, x2, y2)))
        return blobs

    @staticmethod
    def _to_gray(crop):
        import cv2
        import numpy as np

        if crop is None:
            return None
        arr = np.asarray(crop)
        if arr.size == 0:
            return None
        if arr.ndim == 3:
            return cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
        if arr.ndim == 2:
            return arr.astype(np.uint8)
        return None


__all__ = [
    "DEFAULT_ICONS_DIR",
    "FAMILY_TO_CLASS",
    "MARKER_FAMILIES",
    "MARGIN_THRESHOLD",
    "gated_weapon",
    "IconMatch",
    "IconMatcher",
    "MarkerMatch",
]
