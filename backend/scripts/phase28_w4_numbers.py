#!/usr/bin/env python3
"""
Phase 28 W4 — independent numbers on the FINAL Phase-28 tree.

Rerun-able, production code untouched. Three measurements:

  1. PER-SAMPLE COST (fresh detector per call — full-parse cost):
     ``read_killfeed_candidates`` called exactly as the monitor calls
     it (multi_feed_monitor.read_killfeed_candidates(det, img, None,
     now)). >= 30 samples each for quiet frames and entry frames,
     drawn from the SAME frame sets as the Phase 26 harness
     (entries_day1 harness ids; VOD stills verified quiet). A fresh
     detector per sample defeats the band-hash caches, so this is the
     cost of one REAL parse — comparable to the Phase 28 profile's
     profile_entry / profile_quiet totals and to Phase 26's
     0.386 s overall / ~420 ms quiet / ~1357-2079 ms entry numbers.
     The icon matcher is warmed once and shared (steady state: in the
     monitor each feed's matcher is built once, not per sample).

  2. PER-SAMPLE COST (single detector, repeated sampling — the
     monitor-realistic steady state): one detector per frame set,
     the same frame re-sampled with ``now`` advancing 2 s (the
     monitor's per-feed interval at 16 feeds). Shows the band-cache /
     negative-cache effect the fleet actually sees.

  3. MONITOR status() accounting: a real MultiFeedMonitorService on
     16 synthetic feeds backed by the same real frames.
       3a. Quiet fleet: drive run_tick() until 32 samples total
           (2 full fleet cycles); record status()["sampler"]
           samples_parsed vs samples_skipped + per-feed split +
           wall time per 16-feed cycle.
       3b. Entry hold: one feed held on a real entry frame, sampled
           via sample_feed_once() 6 times; record that feed's
           parsed/skipped split (pending-vote bypass must keep it
           parsing until the vote confirms) and whether an event was
           emitted, plus emit latency in samples.

Usage:
    /tmp/p26-venv/bin/python scripts/phase28_w4_numbers.py \
        --out scripts/phase28_w4_numbers.json
    AEGIS_MONITOR_BAND_FILTER=0 /tmp/p26-venv/bin/python \
        scripts/phase28_w4_numbers.py --out /tmp/w4_nofilter.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

import cv2  # noqa: E402

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


def stats_s(vals):
    if not vals:
        return {"n": 0, "median_s": None, "p95_s": None, "mean_s": None,
                "min_s": None, "max_s": None}
    s = sorted(vals)
    p95 = s[min(len(s) - 1, max(0, int(round(0.95 * (len(s) - 1)))))]
    return {
        "n": len(vals),
        "median_s": round(statistics.median(vals), 4),
        "p95_s": round(p95, 4),
        "mean_s": round(statistics.mean(vals), 4),
        "min_s": round(s[0], 4),
        "max_s": round(s[-1], 4),
    }


def load_frames():
    from app.vision.detectors.killfeed import KillFeedDetector
    from app.vision.multi_feed_monitor import read_killfeed_candidates

    det = KillFeedDetector()
    entries = []
    for eid in ENTRY_IDS:
        img = cv2.imread(str(ENTRIES_DIR / f"{eid}_frame.jpg"))
        assert img is not None, f"entry frame missing: {eid}"
        if img.shape[1] != 640:
            img = cv2.resize(img, (640, 360))
        entries.append((eid, img))

    cap = cv2.VideoCapture(str(VOD))
    assert cap.isOpened(), f"cannot open VOD {VOD}"
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
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
        if read_killfeed_candidates(det, frame, None, time.time()):
            continue
        quiet.append(frame)
    cap.release()
    assert len(quiet) >= 4, f"only {len(quiet)} verified-quiet frames"
    return entries, quiet


def fresh_detector(shared_matcher):
    from app.vision.detectors.killfeed import KillFeedDetector

    det = KillFeedDetector()
    det._matcher = shared_matcher
    det._matcher_key = (
        str(det.config.get("icon_preprocess", "none")),
        float(det.config.get("icon_threshold", 0.55)),
        float(det.config.get("marker_threshold", 0.50)),
        int(det.config.get("icon_upscale", 4)),
    )
    return det


def measure_fresh(entries, quiet, shared_matcher, reps=4):
    """Full-parse cost: fresh detector per sample."""
    from app.vision.multi_feed_monitor import read_killfeed_candidates

    out = {}
    for name, frames in (("entry", entries), ("quiet", [(f"quiet_{i}", im) for i, im in enumerate(quiet)])):
        times, cand_counts = [], []
        now = 10000.0
        for _fid, img in frames:
            for _ in range(reps):
                det = fresh_detector(shared_matcher)
                now += 100.0  # defeat any time-based state; detector is fresh anyway
                t0 = time.perf_counter()
                cands = read_killfeed_candidates(det, img, None, now)
                times.append(time.perf_counter() - t0)
                cand_counts.append(len(cands))
        out[name] = {
            **stats_s(times),
            "samples_with_candidates": sum(1 for c in cand_counts if c > 0),
            "total_candidates": sum(cand_counts),
        }
        print(f"[fresh:{name}] n={len(times)} median={out[name]['median_s']}s "
              f"p95={out[name]['p95_s']}s cand_samples={out[name]['samples_with_candidates']}")
    return out


def measure_repeat(entries, quiet, shared_matcher, repeats=8):
    """Monitor-realistic: ONE detector, same frame re-sampled."""
    from app.vision.multi_feed_monitor import read_killfeed_candidates

    out = {}
    for name, frames in (("entry", entries[:4]), ("quiet", [(f"quiet_{i}", im) for i, im in enumerate(quiet[:4])])):
        times = []
        now = 20000.0
        for _fid, img in frames:
            det = fresh_detector(shared_matcher)
            for _ in range(repeats):
                now += 2.0
                t0 = time.perf_counter()
                read_killfeed_candidates(det, img, None, now)
                times.append(time.perf_counter() - t0)
        out[name] = stats_s(times)
        # first-sample vs subsequent split is the cache story
        print(f"[repeat:{name}] n={len(times)} median={out[name]['median_s']}s p95={out[name]['p95_s']}s")
    return out


async def measure_monitor(entries, quiet):
    """Real MultiFeedMonitorService: status() parsed/skipped accounting."""
    from app.vision.capture.config import CaptureConfig
    from app.vision.capture.frame_grabber import CapturedFrame, FrameSource
    from app.vision.multi_feed_monitor import FeedSourceSpec, build_multi_feed_monitor

    entry_img = entries[0][1]
    state = {"mode": "quiet", "fid": 0}

    def make_provider(slot):
        def provider():
            state["fid"] += 1
            if state["mode"] == "entry" and slot == 5:
                img = entry_img
            else:
                img = quiet[(slot + state["fid"]) % len(quiet)]
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

    ingested = []

    async def ingest(ev):
        ingested.append(ev)
        return None

    cfg = CaptureConfig(backend="none")
    specs = [
        FeedSourceSpec(slot_number=n, source_id=f"aegis-observer-slot-{n:02d}",
                       frame_provider=make_provider(n))
        for n in range(1, 17)
    ]
    monitor = build_multi_feed_monitor(ingest=ingest, feeds=specs, config=cfg)
    # NOTE: start() is deliberately NOT called — it spawns the
    # background scheduler loop, whose ticks would race these manual
    # run_tick()/sample_feed_once() drives and contaminate the counts
    # (first W4 run: 63 framed samples where 32 were driven). Driving
    # the ticks directly exercises the identical sampler/parse/gate
    # code with deterministic accounting.
    result = {"sampler_at_start": monitor.status()["sampler"]}

    # 3a: quiet fleet — 2 full cycles = 32 samples via run_tick (batch 4/tick)
    t0 = time.perf_counter()
    ticks = 0
    total_samples = 0
    while total_samples < 32:
        n = await monitor.run_tick()
        total_samples += n
        ticks += 1
    wall = time.perf_counter() - t0
    st = monitor.status()
    result["quiet_fleet"] = {
        "ticks": ticks,
        "total_samples": total_samples,
        "wall_s_for_32_samples": round(wall, 3),
        "wall_s_per_16feed_cycle": round(wall / 2.0, 3),
        "sampler": st["sampler"],
        "per_feed": {
            sid: {"samples": rec.get("samples"),
                  "samples_parsed": rec.get("samples_parsed"),
                  "samples_skipped": rec.get("samples_skipped")}
            for sid, rec in st["feeds"].items()
        },
        "events_emitted": st["events_emitted"],
    }
    print(f"[monitor:quiet] 32 samples wall={wall:.2f}s "
          f"parsed={st['sampler']['samples_parsed']} skipped={st['sampler']['samples_skipped']}")

    # 3b: entry hold on feed 5 — pending bypass + emission
    state["mode"] = "entry"
    sid5 = "aegis-observer-slot-05"
    before = monitor.get_feed(sid5).sampler_stats()
    emit_at_sample = None
    for i in range(6):
        evs = await monitor.sample_feed_once(sid5)
        if evs and emit_at_sample is None:
            emit_at_sample = i + 1
    after = monitor.get_feed(sid5).sampler_stats()
    result["entry_hold_feed5"] = {
        "samples_taken": 6,
        "parsed_delta": after["samples_parsed"] - before["samples_parsed"],
        "skipped_delta": after["samples_skipped"] - before["samples_skipped"],
        "bypass_pending_delta": after["prefilter_bypass_pending"] - before["prefilter_bypass_pending"],
        "bypass_forced_delta": after["prefilter_bypass_forced"] - before["prefilter_bypass_forced"],
        "filter_errors_delta": after["prefilter_errors"] - before["prefilter_errors"],
        "emitted_at_sample": emit_at_sample,
        "ingested_total": len(ingested),
    }
    print(f"[monitor:entry] feed5 parsed+={result['entry_hold_feed5']['parsed_delta']} "
          f"skipped+={result['entry_hold_feed5']['skipped_delta']} emit_at={emit_at_sample}")
    await monitor.stop()
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(BACKEND / "scripts" / "phase28_w4_numbers.json"))
    args = ap.parse_args()

    from app.vision.detectors.killfeed import KillFeedDetector

    warm = KillFeedDetector()
    matcher = warm._get_matcher()
    assert matcher is not None and matcher.available, "icon matcher unavailable"

    entries, quiet = load_frames()
    print(f"[frames] entries={len(entries)} quiet={len(quiet)}")

    report = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "band_filter_env": __import__("os").getenv("AEGIS_MONITOR_BAND_FILTER", "(unset)"),
        "per_sample_fresh_detector": measure_fresh(entries, quiet, matcher, reps=4),
        "per_sample_repeat_single_detector": measure_repeat(entries, quiet, matcher, repeats=8),
    }
    report["monitor_status"] = asyncio.run(measure_monitor(entries, quiet))
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    out = Path(args.out)
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"[done] -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
