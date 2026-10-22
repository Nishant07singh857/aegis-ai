#!/usr/bin/env python3
"""
Phase 28 profile + A/B harness — killfeed parse cost, measured on REAL frames.

What it does (rerun-able, no production code modified by this script):

  1. STAGE PROFILE of the production monitor parse path
     ``read_killfeed_candidates`` (multi_feed_monitor.py) ->
     KillFeedDetector layout parser (killfeed.py) + IconMatcher
     (icon_matcher.py) + legacy OCR fallback. Stage timings are taken
     by wrapping the detector/matcher methods with perf_counter
     timers *inside this process only*.
     Frame sets (identical to Phase 26 harness):
       * entry frames: data/phase23_work/entries_day1/{8 harness ids}_frame.jpg
       * quiet frames: stills extracted from data/raw/bmsd2026_gf_day1.mp4,
         verified to yield ZERO candidates through the production path.
  2. MONITOR-REALISTIC quiet-sequence run: ONE detector sampled over a
     real VOD quiet sequence (background motion, no entries) — how many
     samples pay a full parse despite the band-hash caches.
  3. BAND-CHANGE FILTER evaluation (app.vision.band_change, Phase 28):
     ground-truth candidate keys per frame from the production path;
     consecutive-pair labels: MUST_TRUE (a new key appears), SKIP_OK
     (key set unchanged), EXPIRY (keys only removed). Plus synthetic
     stack composites built from REAL harvested entry strips pasted on
     REAL quiet frames (entry B appearing below entry A).
  4. A/B: run with ``--label before`` on the pre-cut code and
     ``--label after`` on the post-cut code; per-frame candidate output
     (key/player/victim/type/weapon/headshot) is recorded per label and
     the report gains an ``ab_comparison`` section (output identity +
     stage medians before/after).

Extracted sequence frames are cached under /tmp/phase28_cache so
reruns do not re-seek the VOD; ground-truth keys are ALWAYS recomputed
with the code under test (that is the point of the A/B).

Usage:
    /tmp/p26-venv/bin/python scripts/phase28_profile.py --label before
    /tmp/p26-venv/bin/python scripts/phase28_profile.py --label after
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

ENTRIES_DIR = REPO / "data" / "phase23_work" / "entries_day1"
VOD = REPO / "data" / "raw" / "bmsd2026_gf_day1.mp4"
CACHE = Path("/tmp/phase28_cache")
ENTRY_IDS = [
    "d1_e0011_t2815",
    "d1_e0022_t2925",
    "d1_e0030_t2995",
    "d1_e0050_t3530",
    "d1_e0066_t3788",
    "d1_e0069_t3802",
    "d1_e0085_t4136",
    "d1_e0086_t4138",
]
ENTRY_SEQ_IDS = ["d1_e0011_t2815", "d1_e0022_t2925", "d1_e0050_t3530", "d1_e0085_t4136"]
ENTRY_SEQ_OFFSETS = [-2.0, -1.0, -0.4, 0.0, 0.4, 1.0, 2.0, 4.0]
QUIET_WINDOW_STARTS = [120.0, 400.0, 900.0]
QUIET_SEQ_FRAMES = 10
QUIET_SEQ_STEP_S = 0.5


def pct(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return round(s[i] * 1000.0, 2)


def stats_ms(vals):
    if not vals:
        return {"n": 0, "median_ms": None, "p95_ms": None, "mean_ms": None}
    return {
        "n": len(vals),
        "median_ms": round(statistics.median(vals) * 1000.0, 2),
        "p95_ms": pct(vals, 0.95),
        "mean_ms": round(statistics.mean(vals) * 1000.0, 2),
    }


# ----------------------------------------------------------------------
# Timed wrappers (this process only)
# ----------------------------------------------------------------------
STAGE_TIMES: dict = defaultdict(list)
STAGE_CALLS: dict = defaultdict(int)
STAGE_CURRENT: dict = defaultdict(float)  # per-run accumulator (top-level stages)


def _wrap(cls, name, stage, is_static=False):
    orig = getattr(cls, name)
    fn = orig.__func__ if (is_static and hasattr(orig, "__func__")) else orig

    def wrapper(*a, **kw):
        t0 = time.perf_counter()
        try:
            return fn(*a, **kw)
        finally:
            dt = time.perf_counter() - t0
            STAGE_TIMES[stage].append(dt)
            STAGE_CALLS[stage] += 1
            STAGE_CURRENT[stage] += dt

    setattr(cls, name, staticmethod(wrapper) if is_static else wrapper)
    return orig


_TOP_LEVEL_STAGES = (
    "crop_region", "layout_total", "legacy_preprocess",
    "legacy_ocr_lines", "legacy_parse_text",
)


def install_timers():
    from app.vision.detectors.icon_matcher import IconMatcher
    from app.vision.detectors.killfeed import KillFeedDetector

    _wrap(KillFeedDetector, "_crop_region", "crop_region")
    _wrap(KillFeedDetector, "parse_layout_region", "layout_total")
    _wrap(KillFeedDetector, "_detect_bars", "detect_bars", is_static=True)
    _wrap(KillFeedDetector, "_parse_entry_strip", "strip_total")
    _wrap(KillFeedDetector, "_preprocess_row", "strip_preprocess_row")
    _wrap(KillFeedDetector, "_ocr_words", "strip_ocr_words")
    _wrap(KillFeedDetector, "_preprocess", "legacy_preprocess")
    _wrap(KillFeedDetector, "_ocr_lines", "legacy_ocr_lines")
    _wrap(KillFeedDetector, "parse_text", "legacy_parse_text")
    _wrap(IconMatcher, "match_candidates", "icon_match_candidates")
    _wrap(IconMatcher, "match_markers", "icon_match_markers")
    _wrap(IconMatcher, "_slide_best", "icon_slide_best")
    _wrap(IconMatcher, "_prepare", "icon_prepare")


def reset_stages():
    STAGE_TIMES.clear()
    STAGE_CALLS.clear()


def stage_snapshot():
    return {
        stage: {**stats_ms(vals), "calls": STAGE_CALLS[stage]}
        for stage, vals in sorted(STAGE_TIMES.items())
    }


# ----------------------------------------------------------------------
# Frame loading / extraction (cached)
# ----------------------------------------------------------------------
def load_entry_frames():
    out = []
    for eid in ENTRY_IDS:
        img = cv2.imread(str(ENTRIES_DIR / f"{eid}_frame.jpg"))
        assert img is not None, f"entry frame missing: {eid}"
        if img.shape[1] != 640:
            img = cv2.resize(img, (640, 360))
        out.append((eid, img))
    return out


def _vod_frame(cap, t_s):
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t_s * 1000.0))
    ok, frame = cap.read()
    if not ok or frame is None:
        return None
    if frame.shape[1] != 640:
        frame = cv2.resize(frame, (640, 360))
    return frame


def _cache_get(name):
    p = CACHE / f"{name}.png"
    if p.exists():
        img = cv2.imread(str(p))
        if img is not None:
            return img
    return None


def _cache_put(name, img):
    CACHE.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(CACHE / f"{name}.png"), img)


def extract_quiet_frames(det_verify):
    """Harness-identical quiet stills (verified zero candidates)."""
    cached = []
    for i in range(8):
        img = _cache_get(f"quiet_{i}")
        if img is not None:
            cached.append(img)
    if len(cached) == 8:
        return cached
    from app.vision.multi_feed_monitor import read_killfeed_candidates

    cap = cv2.VideoCapture(str(VOD))
    assert cap.isOpened()
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    quiet = []
    for idx in range(30, 900, 41):
        if len(quiet) >= 8:
            break
        cap.set(cv2.CAP_PROP_POS_FRAMES, min(total - 2, int(idx * fps)))
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        if frame.shape[1] != 640:
            frame = cv2.resize(frame, (640, 360))
        if read_killfeed_candidates(det_verify, frame, None, time.time()):
            continue
        _cache_put(f"quiet_{len(quiet)}", frame)
        quiet.append(frame)
    cap.release()
    assert len(quiet) >= 4, f"only {len(quiet)} quiet frames"
    return quiet


def extract_sequences():
    """Real VOD sequences: entry windows + quiet windows (frames only;
    ground truth is recomputed per run by the caller)."""
    seqs = {}
    cap = cv2.VideoCapture(str(VOD))
    assert cap.isOpened()
    for eid in ENTRY_SEQ_IDS:
        t0 = float(eid.rsplit("_t", 1)[1])
        frames = []
        for j, off in enumerate(ENTRY_SEQ_OFFSETS):
            name = f"seq_{eid}_{j}"
            img = _cache_get(name)
            if img is None:
                img = _vod_frame(cap, t0 + off)
                if img is not None:
                    _cache_put(name, img)
            if img is not None:
                frames.append((f"{eid}@{off:+.1f}s", img))
        seqs[f"entry_{eid}"] = frames
    for wi, start in enumerate(QUIET_WINDOW_STARTS):
        frames = []
        for j in range(QUIET_SEQ_FRAMES):
            name = f"seq_quiet{wi}_{j}"
            img = _cache_get(name)
            if img is None:
                img = _vod_frame(cap, start + j * QUIET_SEQ_STEP_S)
                if img is not None:
                    _cache_put(name, img)
            if img is not None:
                frames.append((f"quiet{wi}@{j * QUIET_SEQ_STEP_S:.1f}s", img))
        seqs[f"quiet_win{wi}"] = frames
    cap.release()
    return seqs


def build_composites(quiet_frames):
    """Synthetic stack cases from REAL harvested entry strips on REAL
    quiet frames. Returns {name: (frames list, pair_labels)} where
    pair_labels[i] describes transition frames[i] -> frames[i+1]."""
    strip_a = cv2.imread(str(ENTRIES_DIR / "d1_e0011_t2815_entry.png"))
    strip_b = cv2.imread(str(ENTRIES_DIR / "d1_e0022_t2925_entry.png"))
    assert strip_a is not None and strip_b is not None
    q1, q2 = quiet_frames[0], quiet_frames[1]

    def paste(base, strip, y):
        out = base.copy()
        h, w = strip.shape[:2]
        x = 6
        out[y:y + h, x:x + w] = strip
        return out

    y_top = int(0.26 * 360)
    pitch = 21
    f_q1 = q1
    f_q1a = paste(q1, strip_a, y_top)
    f_q2a = paste(q2, strip_a, y_top)          # same stack, other background
    f_q2ab = paste(f_q2a, strip_b, y_top + pitch)  # B appears below A
    frames = [("q1", f_q1), ("q1+A", f_q1a), ("q2+A", f_q2a), ("q2+A+B", f_q2ab)]
    labels = ["MUST_TRUE", "SKIP_OK", "MUST_TRUE"]
    return {"stack_composite": (frames, labels)}


# ----------------------------------------------------------------------
# Production-path helpers
# ----------------------------------------------------------------------
def fresh_detector(shared_matcher=None):
    from app.vision.detectors.killfeed import KillFeedDetector

    det = KillFeedDetector()
    if shared_matcher is not None:
        det._matcher = shared_matcher
        det._matcher_key = (
            str(det.config.get("icon_preprocess", "none")),
            float(det.config.get("icon_threshold", 0.55)),
            float(det.config.get("marker_threshold", 0.50)),
            int(det.config.get("icon_upscale", 4)),
        )
    return det


def candidate_output(det, img, now):
    """Production monitor path -> comparable output records."""
    from app.vision.multi_feed_monitor import read_killfeed_candidates

    cands = read_killfeed_candidates(det, img, None, now)
    recs = []
    for c in cands:
        ev = c.make_event(now)
        payload = getattr(ev, "payload", {}) or {}
        recs.append(
            {
                "key": c.key,
                "player": getattr(ev, "player", None),
                "victim": getattr(ev, "victim", None),
                "type": str(getattr(ev, "event_type", "")),
                "weapon": getattr(ev, "weapon", None),
                "headshot": bool(payload.get("headshot", False)),
            }
        )
    recs.sort(key=lambda r: r["key"])
    return recs


# ----------------------------------------------------------------------
# Main measurement
# ----------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, choices=["before", "after"])
    ap.add_argument(
        "--out", default=str(BACKEND / "scripts" / "phase28_profile_report.json")
    )
    args = ap.parse_args()

    install_timers()
    from app.vision.detectors.killfeed import KillFeedDetector

    warm = KillFeedDetector()
    t0 = time.perf_counter()
    matcher = warm._get_matcher()
    assert matcher is not None and matcher.available, "icon matcher unavailable"
    matcher_load_s = time.perf_counter() - t0

    run: dict = {"label": args.label, "matcher_load_s": round(matcher_load_s, 3)}

    entry_frames = load_entry_frames()
    det_verify = fresh_detector(matcher)
    quiet_frames = extract_quiet_frames(det_verify)

    # ---- 1. stage profile: entry frames (fresh detector per rep) ----
    for set_name, frames, reps in (
        ("entry", entry_frames, 2),
        ("quiet", [(f"quiet_{i}", im) for i, im in enumerate(quiet_frames)], 2),
    ):
        reset_stages()
        totals = []
        others = []
        outputs = {}
        for fid, img in frames:
            for rep in range(reps):
                det = fresh_detector(matcher)
                STAGE_CURRENT.clear()
                t = time.perf_counter()
                recs = candidate_output(det, img, 1000.0 + rep * 100.0)
                tt = time.perf_counter() - t
                totals.append(tt)
                accounted = sum(STAGE_CURRENT[s] for s in _TOP_LEVEL_STAGES)
                others.append(max(0.0, tt - accounted))
                outputs[fid] = recs
        other = others
        run[f"profile_{set_name}"] = {
            "total": stats_ms(totals),
            "stages": stage_snapshot(),
            "other_hash_glue_est": stats_ms(other),
            "outputs": outputs,
        }
        print(f"[profile:{set_name}] total median "
              f"{run[f'profile_{set_name}']['total']['median_ms']} ms "
              f"p95 {run[f'profile_{set_name}']['total']['p95_ms']} ms")

    # ---- 2. monitor-realistic quiet sequence (one detector) ---------
    seqs = extract_sequences()
    quiet_seq = seqs["quiet_win0"] + seqs["quiet_win1"]
    det = fresh_detector(matcher)
    reset_stages()
    per_sample = []
    full_parse = 0
    bars_only = 0
    cache_hit = 0
    now = 2000.0
    for fid, img in quiet_seq:
        before_calls = dict(STAGE_CALLS)
        t = time.perf_counter()
        candidate_output(det, img, now)
        per_sample.append(time.perf_counter() - t)
        now += 2.0
        d_bars = STAGE_CALLS.get("detect_bars", 0) - before_calls.get("detect_bars", 0)
        d_strip = STAGE_CALLS.get("strip_total", 0) - before_calls.get("strip_total", 0)
        d_legacy = STAGE_CALLS.get("legacy_ocr_lines", 0) - before_calls.get("legacy_ocr_lines", 0)
        if d_strip or d_legacy:
            full_parse += 1
        elif d_bars:
            bars_only += 1
        else:
            cache_hit += 1
    run["monitor_quiet_sequence"] = {
        "samples": len(per_sample),
        "per_sample_total": stats_ms(per_sample),
        "full_parse_samples": full_parse,
        "bars_only_samples": bars_only,
        "cache_hit_samples": cache_hit,
        "pct_samples_full_parse_on_quiet": round(100.0 * full_parse / max(1, len(per_sample)), 1),
    }
    print(f"[monitor-quiet] full-parse {full_parse}/{len(per_sample)} samples")

    # ---- 2b. sequence parse probe (one detector per entry sequence;
    # realistic sampling interval; exercises the Phase 28 row cache
    # across the frames an entry stays on screen) ----------------------
    seq_probe = {}
    for sname in [s for s in seqs if s.startswith("entry_")]:
        det_s = fresh_detector(matcher)
        per_frame = []
        now = 3000.0
        for fid, img in seqs[sname]:
            t = time.perf_counter()
            candidate_output(det_s, img, now)
            per_frame.append(round((time.perf_counter() - t) * 1000.0, 1))
            now += 2.0
        seq_probe[sname] = {
            "per_frame_ms": per_frame,
            "row_cache_stats": dict(
                getattr(det_s, "_row_cache_stats", {"hits": 0, "misses": 0})
            ),
        }
    run["sequence_parse_probe"] = seq_probe
    print(f"[seq-probe] row-cache hits/misses: "
          f"{sum(v['row_cache_stats']['hits'] for v in seq_probe.values())}/"
          f"{sum(v['row_cache_stats']['misses'] for v in seq_probe.values())}")

    # ---- 2c. icon template-cache paired A/B (interleaved, same
    # process — only meaningful where _resized_template exists) -------
    icon_ab = {"available": False}
    try:
        from app.vision.detectors.icon_matcher import IconMatcher

        if hasattr(IconMatcher, "_resized_template"):
            det_p = fresh_detector(matcher)
            img0 = entry_frames[0][1]
            region0 = det_p._crop_region(img0)
            bars0 = det_p._detect_bars(region0, 360)
            if bars0:
                bar = bars0[0]
                strip0 = region0[
                    max(0, bar["y"] - 5):bar["y"] + bar["h"] + 5,
                    max(0, bar["x"] - 8):bar["x"] + bar["w"] + 30,
                ]
                orig_resized = IconMatcher._resized_template

                def _bypass(self_m, family, idx, tmpl, scale):
                    import cv2 as _cv2

                    if scale == 1.0:
                        return tmpl
                    return _cv2.resize(
                        tmpl,
                        (max(2, int(tmpl.shape[1] * scale)),
                         max(2, int(tmpl.shape[0] * scale))),
                        interpolation=_cv2.INTER_CUBIC,
                    )

                # OFF: every call re-resizes every template (pre-cut
                # behaviour). ON: cache cleared ONCE, then repeated
                # calls reuse resized templates — the production case
                # (one matcher serves every strip of a feed's life).
                off_ms = []
                IconMatcher._resized_template = _bypass
                try:
                    for rep in range(6):
                        t = time.perf_counter()
                        matcher.match_candidates(strip0, top_n=4)
                        off_ms.append((time.perf_counter() - t) * 1000.0)
                finally:
                    IconMatcher._resized_template = orig_resized
                matcher._tmpl_cache.clear()
                on_all = []
                for rep in range(6):
                    t = time.perf_counter()
                    matcher.match_candidates(strip0, top_n=4)
                    on_all.append((time.perf_counter() - t) * 1000.0)
                icon_ab = {
                    "available": True,
                    "strip_shape": list(strip0.shape),
                    "cache_on_first_call_ms": round(on_all[0], 2),
                    "cache_on_warm_median_ms": round(statistics.median(on_all[1:]), 2),
                    "cache_off_median_ms": round(statistics.median(off_ms), 2),
                    "note": "interleaved in one process on the same real strip; "
                            "OFF = pre-cut behaviour (resize per call)",
                }
                print(f"[icon-ab] on_warm={icon_ab['cache_on_warm_median_ms']} ms off={icon_ab['cache_off_median_ms']} ms")
    except Exception as exc:  # probe must never kill the profile
        icon_ab = {"available": False, "error": str(exc)[:200]}
    run["icon_template_cache_ab"] = icon_ab

    # ---- 3. filter evaluation -----------------------------------------
    filter_section = {"available": False}
    try:
        from app.vision.band_change import BandChangeFilter

        filter_section = {"available": True}
        # ground truth keys per sequence frame (fresh detector, now jumps
        # defeat the negative cache; band cache is keyed by exact pixels)
        all_seqs = dict(seqs)
        comp = build_composites(quiet_frames)
        comp_frames, comp_labels = comp["stack_composite"]
        gt = {}
        row_cache_stats = {}
        for sname, frames in list(all_seqs.items()) + [("stack_composite", comp_frames)]:
            det_gt = fresh_detector(matcher)
            keys_per = []
            now = 5000.0
            for fid, img in frames:
                recs = candidate_output(det_gt, img, now)
                keys_per.append(sorted(r["key"] for r in recs))
                now += 100.0
            gt[sname] = keys_per
            row_cache_stats[sname] = dict(
                getattr(det_gt, "_row_cache_stats", {"hits": 0, "misses": 0})
            )
        filter_section["row_cache_stats_per_sequence"] = row_cache_stats
        filter_section["ground_truth_keys"] = {
            s: [len(k) for k in v] for s, v in gt.items()
        }
        filter_section["composite_gt_keys"] = gt["stack_composite"]

        # sweep thresholds on signature distances
        probe = BandChangeFilter()
        pair_rows = []  # (label, dist, row_jump)
        for sname, frames in list(all_seqs.items()) + [("stack_composite", comp_frames)]:
            sigs = [probe.signature(img) for _, img in frames]
            keys_per = gt[sname]
            for i in range(len(frames) - 1):
                if sname == "stack_composite":
                    label = comp_labels[i]
                else:
                    before, after = set(keys_per[i]), set(keys_per[i + 1])
                    if after - before:
                        label = "MUST_TRUE"
                    elif before - after:
                        label = "EXPIRY"
                    else:
                        label = "SKIP_OK"
                d = probe.distance(sigs[i], sigs[i + 1])
                pair_rows.append({"seq": sname, "i": i, "label": label, **d})
        must = [r for r in pair_rows if r["label"] == "MUST_TRUE"]
        skip = [r for r in pair_rows if r["label"] == "SKIP_OK"]
        expiry = [r for r in pair_rows if r["label"] == "EXPIRY"]
        sweep = []
        for thr in [0.02, 0.04, 0.05, 0.06, 0.08, 0.10, 0.13, 0.16, 0.20, 0.25, 0.30]:
            for rthr in [0.15, 0.25, 0.32, 0.35, 0.45]:
                fs = sum(1 for r in must if not (r["dist"] > thr or r["row_jump"] > rthr))
                sk = sum(1 for r in skip if not (r["dist"] > thr or r["row_jump"] > rthr))
                sweep.append({
                    "threshold": thr, "row_threshold": rthr,
                    "false_skips": fs, "must_true_n": len(must),
                    "skip_rate_on_skip_ok": round(sk / max(1, len(skip)), 3),
                    "skip_ok_n": len(skip),
                })
        filter_section["pairs"] = pair_rows
        filter_section["sweep"] = sweep
        filter_section["pair_counts"] = {
            "MUST_TRUE": len(must), "SKIP_OK": len(skip), "EXPIRY": len(expiry),
        }

        # shipped-filter behaviour at its default thresholds
        filt = BandChangeFilter()
        obs_cost = []
        decisions = []
        for sname, frames in list(all_seqs.items()) + [("stack_composite", comp_frames)]:
            filt.reset()
            for j, (fid, img) in enumerate(frames):
                t = time.perf_counter()
                changed = filt.observe(f"eval-{sname}", img)
                obs_cost.append(time.perf_counter() - t)
                decisions.append({"seq": sname, "i": j, "changed": bool(changed)})
        # evaluate decisions against pair labels
        dec_by = {(d["seq"], d["i"]): d["changed"] for d in decisions}
        false_skips = sum(
            1 for r in pair_rows
            if r["label"] == "MUST_TRUE" and not dec_by[(r["seq"], r["i"] + 1)]
        )
        skips_ok = sum(
            1 for r in pair_rows
            if r["label"] == "SKIP_OK" and not dec_by[(r["seq"], r["i"] + 1)]
        )
        filter_section["shipped"] = {
            "threshold": filt.threshold,
            "row_threshold": filt.row_threshold,
            "false_skips": false_skips,
            "must_true_n": len(must),
            "skip_rate_on_skip_ok": round(skips_ok / max(1, len(skip)), 3),
            "skip_ok_n": len(skip),
            "observe_cost": stats_ms(obs_cost),
            "decisions": decisions,
        }
        print(f"[filter] false_skips={false_skips}/{len(must)} "
              f"skip_rate={filter_section['shipped']['skip_rate_on_skip_ok']} "
              f"observe median {filter_section['shipped']['observe_cost']['median_ms']} ms")
    except ImportError as exc:
        filter_section = {"available": False, "error": str(exc)}
        print(f"[filter] not available: {exc}")
    run["filter"] = filter_section

    # ---- write / merge report ------------------------------------------
    out_path = Path(args.out)
    report = {}
    if out_path.exists():
        try:
            report = json.loads(out_path.read_text())
        except Exception:
            report = {}
    report.setdefault("meta", {
        "frames": "Phase 26 harness sets: entries_day1 (8 harness ids) + VOD quiet stills + VOD sequences",
        "vm_note": "same 2-core VM class as Phase 26 baseline (0.386s median changed-band parse)",
    })
    report.setdefault("runs", {})[args.label] = run
    if "before" in report["runs"] and "after" in report["runs"]:
        b, a = report["runs"]["before"], report["runs"]["after"]
        mismatches = []
        for sname in ("profile_entry", "profile_quiet"):
            for fid, recs in b[sname]["outputs"].items():
                if a[sname]["outputs"].get(fid) != recs:
                    mismatches.append({"set": sname, "frame": fid,
                                         "before": recs, "after": a[sname]["outputs"].get(fid)})
        ab = {"output_mismatches": mismatches, "stage_table": {}}
        stages = set(b["profile_entry"]["stages"]) | set(a["profile_entry"]["stages"]) \
            | set(b["profile_quiet"]["stages"]) | set(a["profile_quiet"]["stages"])
        for st in sorted(stages):
            ab["stage_table"][st] = {
                "entry_before_median_ms": b["profile_entry"]["stages"].get(st, {}).get("median_ms"),
                "entry_after_median_ms": a["profile_entry"]["stages"].get(st, {}).get("median_ms"),
                "quiet_before_median_ms": b["profile_quiet"]["stages"].get(st, {}).get("median_ms"),
                "quiet_after_median_ms": a["profile_quiet"]["stages"].get(st, {}).get("median_ms"),
            }
        for sname in ("profile_entry", "profile_quiet", "monitor_quiet_sequence"):
            ab[f"{sname}_total_before"] = b[sname].get("total", b[sname].get("per_sample_total"))
            ab[f"{sname}_total_after"] = a[sname].get("total", a[sname].get("per_sample_total"))
        report["ab_comparison"] = ab
        print(f"[ab] output mismatches: {len(mismatches)}")
    out_path.write_text(json.dumps(report, indent=2))
    print(f"[done] label={args.label} report -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
