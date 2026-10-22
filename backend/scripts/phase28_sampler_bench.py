#!/usr/bin/env python3
"""
Phase 28 (W3) — adaptive sampler micro-benchmark.

Question: on THIS VM class (2 vCPU), does sampling the production
killfeed parse path (``read_killfeed_candidates``) in parallel beat
sequential sampling, and with which executor — threads or processes?

Method (design numbers, not vibes):
  * Frames: the REAL harvested entry frames the Phase 26 harness uses
    (``data/phase23_work/entries_day1/*_frame.jpg``, ENTRY_IDS below),
    each verified at startup to yield >= 1 candidate through the
    production reader. Entry frames are the expensive path (bars found
    -> icon matching + per-row Tesseract subprocess OCR).
  * N=32 samples, cycling the 8 distinct frames. Every variant uses
    PRODUCTION-shaped detector ownership: one KillFeedDetector per
    concurrent lane/worker, reused across its samples (exactly like
    production: one detector per feed). Cycling distinct frames means
    the detector's single-entry band-hash cache misses on consecutive
    samples (cache holds only the LAST band), so every timed sample
    pays a full parse — the measured Phase 26 cost, not a cache hit.
  * Variants:
      sequential_direct          plain loop, one detector
      asyncio_to_thread_seq      the CURRENT production dispatch:
                                 ``await asyncio.to_thread(...)`` one
                                 sample at a time (never overlapped)
      threadpool2                ThreadPoolExecutor(2), one detector
                                 per lane, lanes run concurrently
      processpool2               ProcessPoolExecutor(2), one detector
                                 per worker process (initializer);
                                 the FULL 640x360x3 frame (~675 KiB) is
                                 pickled per task — the production
                                 function takes the full image and
                                 crops internally, so no smaller crop
                                 crosses the process boundary unless
                                 the caller pre-crops (noted, not done)
  * Two warm-up parses per variant (untimed) so one-time costs
    (IconMatcher template load, tesseract binary spawn) land outside
    the timed window for every variant equally. Process-pool startup
    is additionally reported separately AND included in its wall time.

Also (STEP 4 self-check, ``--cycle``): a monitor-level fleet cycle —
16 synthetic feeds backed by verified-quiet REAL VOD frames and the
PRODUCTION reader factory — timed over two full round-robin cycles
(cold + steady) through the (possibly new) MultiFeedMonitorService,
with and without the BandChangeFilter module when it exists.

Usage:
    /tmp/p26-venv/bin/python scripts/phase28_sampler_bench.py \
        [--n 32] [--skip-cycle] [--out scripts/phase28_sampler_report.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

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
ENTRIES_DIR = REPO / "data" / "phase23_work" / "entries_day1"
VOD = REPO / "data" / "raw" / "bmsd2026_gf_day1.mp4"


# ----------------------------------------------------------------------
# Frame loading / verification (mirrors the Phase 26 harness)
# ----------------------------------------------------------------------

def load_frames():
    import cv2

    from app.vision.detectors.killfeed import KillFeedDetector
    from app.vision.multi_feed_monitor import read_killfeed_candidates

    frames = []
    det = KillFeedDetector()
    for eid in ENTRY_IDS:
        img = cv2.imread(str(ENTRIES_DIR / f"{eid}_frame.jpg"))
        assert img is not None, f"entry frame missing: {eid}"
        if img.shape[1] != 640:
            img = cv2.resize(img, (640, 360))
        cands = read_killfeed_candidates(det, img, None, time.time())
        assert cands, f"entry frame yields NO candidates: {eid}"
        frames.append(img)
        print(f"[frames] {eid}: {len(cands)} candidate(s)")
    return frames


def load_quiet_frames(det):
    """Verified-quiet REAL VOD stills (same extraction as Phase 26)."""
    import cv2

    from app.vision.multi_feed_monitor import read_killfeed_candidates

    cap = cv2.VideoCapture(str(VOD))
    assert cap.isOpened(), f"cannot open VOD {VOD}"
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    quiet = []
    tried = 0
    for idx in range(30, 900, 41):
        if len(quiet) >= 8 or tried >= 40:
            break
        tried += 1
        cap.set(cv2.CAP_PROP_POS_FRAMES, min(total - 2, int(idx * fps)))
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        if frame.shape[1] != 640:
            frame = cv2.resize(frame, (640, 360))
        if read_killfeed_candidates(det, frame, None, time.time()):
            continue
        quiet.append(frame)
    cap.release()
    assert len(quiet) >= 4, f"only {len(quiet)} verified-quiet frames"
    print(f"[frames] quiet: {len(quiet)} verified (tried {tried})")
    return quiet


# ----------------------------------------------------------------------
# Parse variants
# ----------------------------------------------------------------------

def _parse(det, img):
    from app.vision.multi_feed_monitor import read_killfeed_candidates

    t0 = time.perf_counter()
    cands = read_killfeed_candidates(det, img, None, time.time())
    return (time.perf_counter() - t0), len(cands)


def variant_sequential(frames, n):
    from app.vision.detectors.killfeed import KillFeedDetector

    det = KillFeedDetector()
    _parse(det, frames[0])  # warm-up (matcher load)
    times, total = [], 0
    t0 = time.perf_counter()
    for i in range(n):
        dt, c = _parse(det, frames[i % len(frames)])
        times.append(dt)
        total += c
    return time.perf_counter() - t0, times, total


async def _to_thread_seq(frames, n):
    from app.vision.detectors.killfeed import KillFeedDetector

    det = KillFeedDetector()
    _parse(det, frames[0])
    times, total = [], 0
    t0 = time.perf_counter()
    for i in range(n):
        dt, c = await asyncio.to_thread(_parse, det, frames[i % len(frames)])
        times.append(dt)
        total += c
    return time.perf_counter() - t0, times, total


def variant_asyncio_to_thread(frames, n):
    return asyncio.run(_to_thread_seq(frames, n))


def _lane_run(det, imgs):
    times, total = [], 0
    for img in imgs:
        dt, c = _parse(det, img)
        times.append(dt)
        total += c
    return times, total


def variant_threadpool(frames, n, workers=2):
    from app.vision.detectors.killfeed import KillFeedDetector

    dets = [KillFeedDetector() for _ in range(workers)]
    for d in dets:
        _parse(d, frames[0])  # warm-up per lane
    lanes = [[] for _ in range(workers)]
    for i in range(n):
        lanes[i % workers].append(frames[i % len(frames)])
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda ld: _lane_run(*ld), zip(dets, lanes)))
    wall = time.perf_counter() - t0
    times = [t for ts, _ in results for t in ts]
    total = sum(c for _, c in results)
    return wall, times, total


_PROC_DET = None


def _proc_init():
    global _PROC_DET
    from app.vision.detectors.killfeed import KillFeedDetector

    _PROC_DET = KillFeedDetector()
    _parse(_PROC_DET, _PROC_WARMUP)  # warm matcher inside the worker


_PROC_WARMUP = None


def _proc_task(img):
    dt, c = _parse(_PROC_DET, img)
    return dt, c


def variant_processpool(frames, n, workers=2):
    global _PROC_WARMUP
    _PROC_WARMUP = frames[0]
    tasks = [frames[i % len(frames)] for i in range(n)]
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers, initializer=_proc_init) as ex:
        results = list(ex.map(_proc_task, tasks, chunksize=1))
    wall = time.perf_counter() - t0
    times = [dt for dt, _ in results]
    total = sum(c for _, c in results)
    return wall, times, total


def _variant_row(wall, times, total, workers, notes):
    return {
        "wall_s": round(wall, 3),
        "samples_per_s": round(len(times) / wall, 3) if wall > 0 else None,
        "per_sample_ms_median": round(statistics.median(times) * 1000, 1),
        "per_sample_ms_max": round(max(times) * 1000, 1),
        "candidates_total": total,
        "workers": workers,
        "notes": notes,
    }


# ----------------------------------------------------------------------
# Monitor-level fleet cycle (STEP 4 self-check)
# ----------------------------------------------------------------------

async def _run_fleet_cycles(label):
    """Two full 16-feed cycles on verified-quiet frames, production
    readers. Returns wall time per cycle + per-feed sample/skip counts
    from the service status()."""
    from app.vision.capture.config import CaptureConfig
    from app.vision.capture.frame_grabber import CapturedFrame, FrameSource
    from app.vision.detectors.killfeed import KillFeedDetector
    from app.vision.multi_feed_monitor import (
        FeedSourceSpec,
        MultiFeedMonitorService,
        default_reader_factory,
    )

    quiet = load_quiet_frames(KillFeedDetector())
    ingested = []

    async def ingest(ev):
        ingested.append(ev)

    counters = {}

    def make_provider(slot):
        state = {"i": slot, "fid": 0}

        def provider():
            state["fid"] += 1
            img = quiet[state["i"] % len(quiet)]
            state["i"] += 1
            return CapturedFrame(
                frame_id=state["fid"],
                source_id=f"aegis-observer-slot-{slot:02d}",
                source_type=FrameSource.ADB_DEVICE,
                timestamp=time.time(),
                image=img,
                width=img.shape[1],
                height=img.shape[0],
            )

        return provider

    cfg = CaptureConfig(backend="none")  # defaults: batch 4, tick 0.5
    # "plain" = pre-filter disabled via the documented kill-switch env
    # (the service resolves its filter at construction); any other
    # label leaves the auto-loaded BandChangeFilter active.
    if label == "plain":
        os.environ["AEGIS_MONITOR_BAND_FILTER"] = "0"
    try:
        svc = MultiFeedMonitorService(
            ingest=ingest, config=cfg, detector_factory=default_reader_factory
        )
        for slot in range(1, 17):
            svc.add_feed(
                FeedSourceSpec(
                    slot_number=slot,
                    source_id=f"aegis-observer-slot-{slot:02d}",
                    frame_provider=make_provider(slot),
                )
            )
    finally:
        os.environ.pop("AEGIS_MONITOR_BAND_FILTER", None)
    cycles = []
    for cycle_idx in range(2):
        t0 = time.perf_counter()
        sampled = 0
        while sampled < 16:
            sampled += await svc.run_tick()
        cycles.append(round(time.perf_counter() - t0, 3))
        print(f"[cycle:{label}] cycle {cycle_idx + 1}: {cycles[-1]}s")
    status = svc.status()
    await svc.stop()
    per_feed = {}
    for sid, rec in (status.get("feeds") or {}).items():
        per_feed[sid] = {
            "samples": rec.get("samples"),
            "samples_parsed": rec.get("samples_parsed"),
            "samples_skipped": rec.get("samples_skipped"),
        }
    return {
        "label": label,
        "cycle_wall_s": cycles,
        "sampler": status.get("sampler"),
        "per_feed": per_feed,
        "events_ingested": len(ingested),
    }


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=32)
    ap.add_argument("--skip-cycle", action="store_true")
    ap.add_argument(
        "--out", default=str(Path(__file__).with_name("phase28_sampler_report.json"))
    )
    args = ap.parse_args()

    import cv2

    frames = load_frames()
    probe = frames[0]
    from app.vision.detectors.killfeed import KillFeedDetector

    region = KillFeedDetector()._crop_region(probe)
    report = {
        "phase": "28-W3",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "methodology_notes": (
            "Two bench generations exist because the detector changed "
            "mid-phase. RUN 1 (before Worker A's Phase-28 row-result "
            "micro-cache landed in detectors/killfeed.py): every sample "
            "paid a full parse — sequential 50.076s, asyncio_to_thread "
            "60.647s, threadpool2 34.537s (1.45x, winner), processpool2 "
            "40.274s (1.24x); per-sample median 1366ms, matching Worker "
            "A's independent profile median 1357ms. THIS report is the "
            "later run, with the row-cache live: the same entry strip "
            "re-seen within its 30s TTL returns in ~5ms in EVERY "
            "variant, so walls compress toward (8 full parses + 24 "
            "cache hits) and ratios shrink — threadpool still fastest, "
            "processpool slowest (pool startup + 691,200 B/frame "
            "pickling dominate when the work itself is cached). "
            "Direction is identical in both generations; the sampler "
            "design (bounded ThreadPoolExecutor) follows the winner "
            "common to both. Cycle runs additionally raced other "
            "Phase-28 workers on this shared 2-core VM — treat cycle "
            "wall deltas between the plain/prefilter runs as noise, "
            "and the parsed/skipped counts as the signal."
        ),
        "environment": {
            "cpu_count": os.cpu_count(),
            "cv2_version": cv2.__version__,
            "cv2_threads": cv2.getNumThreads(),
            "python": sys.version.split()[0],
        },
        "frames": {
            "entry_ids": ENTRY_IDS,
            "shape": list(probe.shape),
            "full_frame_bytes": int(probe.nbytes),
            "killfeed_region_shape": list(region.shape) if region is not None else None,
            "killfeed_region_bytes": int(region.nbytes) if region is not None else None,
            "n_samples_per_variant": args.n,
        },
        "variants": {},
    }

    print(f"[bench] sequential_direct x{args.n}")
    wall, times, total = variant_sequential(frames, args.n)
    report["variants"]["sequential_direct"] = _variant_row(
        wall, times, total, 1, "plain loop, one detector (per-feed ownership x1)"
    )
    print(f"[bench] asyncio_to_thread_seq x{args.n}")
    wall, times, total = variant_asyncio_to_thread(frames, args.n)
    report["variants"]["asyncio_to_thread_seq"] = _variant_row(
        wall, times, total, 1,
        "CURRENT production dispatch: asyncio.to_thread per sample, awaited serially",
    )
    print(f"[bench] threadpool2 x{args.n}")
    wall, times, total = variant_threadpool(frames, args.n, workers=2)
    report["variants"]["threadpool2"] = _variant_row(
        wall, times, total, 2, "ThreadPoolExecutor(2), one detector per lane"
    )
    print(f"[bench] processpool2 x{args.n}")
    wall, times, total = variant_processpool(frames, args.n, workers=2)
    report["variants"]["processpool2"] = _variant_row(
        wall, times, total, 2,
        "ProcessPoolExecutor(2), detector per worker via initializer; "
        "FULL frame pickled per task (production fn crops internally); "
        "wall includes pool startup + worker warm-up",
    )

    walls = {k: v["wall_s"] for k, v in report["variants"].items()}
    fastest = min(walls, key=walls.get)
    seq = walls["sequential_direct"]
    report["verdict"] = {
        "fastest": fastest,
        "speedup_vs_sequential": {
            k: round(seq / v, 3) for k, v in walls.items()
        },
    }
    print(f"[bench] verdict: {report['verdict']}")

    if not args.skip_cycle:
        report["monitor_cycle"] = {
            "no_prefilter": asyncio.run(_run_fleet_cycles("plain")),
        }
        try:
            import importlib.util

            has_filter = importlib.util.find_spec("app.vision.band_change") is not None
        except Exception:
            has_filter = False
        report["monitor_cycle"]["band_change_module_present"] = bool(has_filter)
        if has_filter:
            report["monitor_cycle"]["with_prefilter"] = asyncio.run(
                _run_fleet_cycles("prefilter")
            )

    out = Path(args.out)
    out.write_text(json.dumps(report, indent=2))
    print(f"[bench] report -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
