#!/usr/bin/env python3
"""
Phase 26 proof harness — 16-feed monitoring, LIVE path, real everything.

Runs the REAL MultiFeedMonitorService with PRODUCTION readers
(KillFeedDetector layout parser incl. OCR + icon matching), the REAL
VisionFeedAdapter.ingest, the REAL EventBus (fakeredis transport — the
VM has no Redis server; bus code, streams and pub/sub semantics are
the production ones), the REAL VisionIntelligenceService and the REAL
LiveDirector. Frames are REAL harvested VOD frames only:

  * entry frames: data/phase23_work/entries_day1/*_frame.jpg (real
    killfeed entries from the BMSD Day-1 VOD), each verified at
    startup to yield >= 1 candidate through the production
    read_killfeed_candidates path;
  * quiet frames: stills extracted from data/raw VODs, each verified
    to yield ZERO candidates.

NO stubbing, patching or bypassing of the detector/monitor/adapter/
bus/consumers. The only substitutions are data inputs the production
system reads from stores this VM does not have: the slot->team map
(supplied through the production StaticSlotResolver — the DB's role)
and the Redis transport (fakeredis — the server's role).

Usage:
    /tmp/p26-venv/bin/python scripts/phase26_harness.py \
        --out scripts/phase26_harness_report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

# --- fakeredis transport patch: MUST precede app imports -------------
import fakeredis.aioredis
import redis.asyncio as _redis_asyncio

_FAKE_SERVER = fakeredis.FakeServer()


def _fake_from_url(url, **kwargs):  # noqa: ANN001, ANN202
    return fakeredis.aioredis.FakeRedis(server=_FAKE_SERVER)


_redis_asyncio.Redis.from_url = classmethod(
    lambda cls, url, **kw: _fake_from_url(url, **kw)
)

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

# Real entry frames VERIFIED to parse through the production
# full-frame path (read_killfeed_candidates on the detector's own
# _crop_region band). Found by scanning entries_day1 with the real
# reader: ~9% of harvested 360p frames pass the detector's honesty
# gates end-to-end (Phase 23 measured 18.3% at strip level) — the
# harness measures the live path's latency/attribution for events
# the detector can see; the 360p detection rate itself is Phase
# 23's documented metric, not this harness's claim. The startup
# verification below re-asserts parseability on every run.
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
# Frame rig
# ----------------------------------------------------------------------
class FeedRig:
    """One synthetic observer feed backed by real frames."""

    def __init__(self, slot: int, quiet_frames):
        self.slot = slot
        self.source_id = f"aegis-observer-slot-{slot:02d}"
        self._quiet = quiet_frames
        self._qi = slot % len(quiet_frames)
        self._entry_img = None
        self._frozen_frame = None
        self._fid = 0

    def show_entry(self, img) -> None:
        self._entry_img = img

    def show_quiet(self) -> None:
        self._entry_img = None

    def freeze(self) -> None:
        self._frozen_frame = self._make(self._current_img())

    def unfreeze(self) -> None:
        self._frozen_frame = None

    def _current_img(self):
        if self._entry_img is not None:
            return self._entry_img
        return self._quiet[self._qi % len(self._quiet)]

    def _make(self, img):
        from app.vision.capture.frame_grabber import CapturedFrame, FrameSource

        self._fid += 1
        return CapturedFrame(
            frame_id=self._fid,
            source_id=self.source_id,
            source_type=FrameSource.ADB_DEVICE,
            timestamp=time.time(),
            image=img,
            width=img.shape[1],
            height=img.shape[0],
        )

    def provider(self):
        if self._frozen_frame is not None:
            return self._frozen_frame
        if self._entry_img is None:
            self._qi += 1  # rotate quiet content so the feed is alive
        return self._make(self._current_img())


def verify_frames():
    """Load + verify entry and quiet frames through the REAL reader."""
    from app.vision.detectors.killfeed import KillFeedDetector
    from app.vision.multi_feed_monitor import read_killfeed_candidates

    det = KillFeedDetector()
    entries = []
    for eid in ENTRY_IDS:
        img = cv2.imread(str(ENTRIES_DIR / f"{eid}_frame.jpg"))
        assert img is not None, f"entry frame missing: {eid}"
        if img.shape[1] != 640:
            img = cv2.resize(img, (640, 360))
        cands = read_killfeed_candidates(det, img, None, time.time())
        assert cands, f"entry frame yields NO candidates (unusable): {eid}"
        probe_ev = cands[0].make_event(time.time())
        entries.append(
            {
                "id": eid,
                "img": img,
                "key": cands[0].key,
                "event_type": str(getattr(probe_ev, "event_type", "?")),
                "candidates": len(cands),
            }
        )
        print(f"[frames] entry {eid}: {len(cands)} candidate(s), key={cands[0].key[:12]}…")

    cap = cv2.VideoCapture(str(VOD))
    assert cap.isOpened(), f"cannot open VOD {VOD}"
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    quiet = []
    tried = 0
    for idx in range(30, 900, 41):  # spread across the VOD
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
            continue  # has a killfeed entry — not quiet
        quiet.append(frame)
    cap.release()
    assert len(quiet) >= 4, f"only {len(quiet)} verified-quiet frames found"
    print(f"[frames] quiet: {len(quiet)} verified (tried {tried})")
    return entries, quiet


# ----------------------------------------------------------------------
# Harness
# ----------------------------------------------------------------------
async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/phase26_harness_report.json")
    args = ap.parse_args()

    report: dict = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    entries, quiet = verify_frames()
    report["frames"] = {
        "entries": [
            {"id": e["id"], "key": e["key"], "event_type": e["event_type"]}
            for e in entries
        ],
        "quiet_verified": len(quiet),
    }
    by_key = {e["key"]: e for e in entries}

    from app.core.event_bus import EventBus
    from app.services.feed_adapter.vision_adapter import VisionFeedAdapter
    from app.services.live_director import LiveDirector
    from app.services.vision_intelligence.attribution import (
        StaticSlotResolver,
        TeamAttribution,
    )
    from app.services.vision_intelligence.service import VisionIntelligenceService
    from app.vision.capture.config import CaptureConfig
    from app.vision.multi_feed_monitor import (
        FeedSourceSpec,
        build_multi_feed_monitor,
    )

    bus = EventBus()
    await bus.connect()
    assert bus._running, "event bus failed to connect over fakeredis"
    report["bus_running"] = True

    adapter = VisionFeedAdapter(bus)
    ingest_log: list = []  # (t_mono, slot, dedup_key, event_type)

    async def ingest_shim(ev):  # instrumentation: record, then REAL ingest
        payload = ev.payload or {}
        kf = payload.get("killfeed") or {}
        ingest_log.append(
            (
                time.monotonic(),
                payload.get("observer_slot"),
                payload.get("dedup_key") or kf.get("dedup_key"),
                str(ev.event_type),
            )
        )
        return await adapter.ingest(ev)

    bus_log: list = []  # (t_mono, event_type, dedup_key)

    async def bus_probe(event):  # noqa: ANN001
        data = getattr(event, "data", None) or {}
        bus_log.append(
            (time.monotonic(), getattr(event, "event_type", "?"), data.get("dedup_key"))
        )

    bus.subscribe("phase26-probe", bus_probe, ["kill_event", "knock_event"])

    resolver = StaticSlotResolver(
        {
            n: TeamAttribution(
                slot_number=n, team_id=f"team-{n:02d}", team_name=f"Team {n:02d}"
            )
            for n in range(1, 17)
        }
    )
    vi = VisionIntelligenceService(bus, slot_resolver=resolver)
    vi.start()
    from app.services.director_service import director_service as _ds

    ld = LiveDirector(
        bus=bus, director_service=_ds, slot_resolver=resolver, phase_provider=None
    )
    ld.start()

    def vi_counts():
        stats = vi.snapshot()["stats"]
        return stats["received"], stats["attributed"], dict(stats["by_type"])

    def ld_fingerprint() -> str:
        st = ld.status()
        return json.dumps(
            {"stats": st["stats"], "engagement": st["engagement"]}, sort_keys=True
        )

    def vi_kill_knock_total():
        _, _, by_type = vi_counts()
        return by_type.get("kill_event", 0) + by_type.get("knock_event", 0)

    rigs = {n: FeedRig(n, quiet) for n in range(1, 17)}

    def spec(rig: FeedRig) -> FeedSourceSpec:
        return FeedSourceSpec(
            slot_number=rig.slot, source_id=rig.source_id, frame_provider=rig.provider
        )

    def feed_samples(monitor, source_id):
        feeds = monitor.status()["feeds"]
        rec = feeds.get(source_id) if isinstance(feeds, dict) else None
        return None if rec is None else rec.get("samples")

    async def run_injection(monitor, rig: FeedRig, entry, hold_s=45.0):
        """Swap one feed to a real entry frame; hold until the monitor
        emits (or the deadline) — the fleet cycle on this VM is long
        (measured per-sample parse cost 0.4-2.5 s on changed bands,
        sequential sampler), so the harness measures the TRUE latency
        instead of assuming the documented budget."""
        vi_before = vi_counts()
        kk_before = vi_kill_knock_total()
        ld_before = ld_fingerprint()
        mark = len(ingest_log)
        samples_before = feed_samples(monitor, rig.source_id)
        t0 = time.monotonic()
        rig.show_entry(entry["img"])
        t_emit = t_bus = t_vi = t_dir = None
        vi_after = None
        deadline = t0 + hold_s
        while time.monotonic() < deadline:
            now = time.monotonic()
            if t_emit is None:
                for rec in ingest_log[mark:]:
                    if rec[2] == entry["key"]:
                        t_emit = rec[0]
                        break
            if t_emit is not None and t_bus is None:
                for rec in bus_log:
                    if rec[0] >= t_emit - 0.05 and rec[2] == entry["key"]:
                        t_bus = rec[0]
                        break
            if t_emit is not None and t_vi is None:
                if vi_kill_knock_total() > kk_before:
                    t_vi = time.monotonic()
                    vi_after = vi_counts()
            if t_emit is not None and t_dir is None:
                if ld_fingerprint() != ld_before:
                    t_dir = time.monotonic()
            if t_emit is not None and t_vi is not None:
                break
            await asyncio.sleep(0.025)
        rig.show_quiet()
        samples_after = feed_samples(monitor, rig.source_id)
        slot_seen = None
        for rec in ingest_log[mark:]:
            if rec[2] == entry["key"]:
                slot_seen = rec[1]
                break
        return {
            "feed": rig.slot,
            "entry": entry["id"],
            "t_emit_s": None if t_emit is None else round(t_emit - t0, 3),
            "t_bus_s": None if t_bus is None else round(t_bus - t0, 3),
            "t_vi_s": None if t_vi is None else round(t_vi - t0, 3),
            "t_director_s": None if t_dir is None else round(t_dir - t0, 3),
            "slot_on_event": slot_seen,
            "vi_attributed_delta": (
                None if vi_after is None else vi_after[1] - vi_before[1]
            ),
            "vi_kill_knock_delta": vi_kill_knock_total() - kk_before,
            "feed_samples_during_hold": (
                None
                if samples_before is None or samples_after is None
                else samples_after - samples_before
            ),
        }

    # ---------------- Run A: production default config, 16 feeds -----
    cfg_default = CaptureConfig(backend="none")
    report["default_config"] = {
        "batch": cfg_default.monitor_batch_size,
        "tick_s": cfg_default.monitor_tick_s,
        "theory_interval_16": cfg_default.monitor_effective_interval_s(16),
    }
    monitor = build_multi_feed_monitor(
        ingest=ingest_shim, feeds=[spec(rigs[n]) for n in range(1, 17)],
        config=cfg_default,
    )
    await monitor.start()
    # P0: quiet window at 16 feeds + fleet-cycle evidence (samples/feed)
    mark_i, mark_b = len(ingest_log), len(bus_log)
    vi0 = vi_counts()
    await asyncio.sleep(20.0)
    vi1 = vi_counts()
    feeds_now = monitor.status()["feeds"]
    samples_per_feed = (
        {sid: rec.get("samples") for sid, rec in feeds_now.items()}
        if isinstance(feeds_now, dict)
        else {}
    )
    report["P0_quiet_20s"] = {
        "ingest_events": len(ingest_log) - mark_i,
        "bus_kill_knock": len(bus_log) - mark_b,
        "vi_received_delta": vi1[0] - vi0[0],
        "samples_per_feed_in_20s": samples_per_feed,
        "pass": (len(ingest_log) - mark_i) == 0 and vi1[0] - vi0[0] == 0,
    }
    print("[P0]", report["P0_quiet_20s"])

    # P1: latency across feeds incl. 13-16. 4 distinct entries rotated
    # with >= 35 s reuse spacing (sequential steps ~11 s apart) so the
    # system's documented 30 s dedup semantics never come into play.
    plan = [13, 3, 16, 7, 14, 15]
    injections = []
    last_used = {}
    for i, slot in enumerate(plan):
        entry = entries[i % len(entries)]
        gap = time.monotonic() - last_used.get(entry["key"], -1e9)
        if gap < 36.0:
            await asyncio.sleep(36.0 - gap)
        last_used[entry["key"]] = time.monotonic()
        rec = await run_injection(monitor, rigs[slot], entry)
        injections.append(rec)
        print(f"[P1] feed {slot:2d} {entry['id']}: emit={rec['t_emit_s']} "
              f"vi={rec['t_vi_s']} dir={rec['t_director_s']} slot={rec['slot_on_event']} "
              f"samples={rec['feed_samples_during_hold']}")
        await asyncio.sleep(3.0)
    emits = [r["t_emit_s"] for r in injections if r["t_emit_s"] is not None]
    report["P1_default_latency"] = {
        "injections": injections,
        "emitted": len(emits),
        "median": round(statistics.median(emits), 3) if emits else None,
        "max": max(emits) if emits else None,
        "slot_violations": sum(
            1 for r in injections if r["slot_on_event"] not in (None, r["feed"])
        ),
    }

    # P3: dedup — same entry on 3 feeds simultaneously
    entry = entries[1]
    gap = time.monotonic() - last_used.get(entry["key"], -1e9)
    if gap < 36.0:
        await asyncio.sleep(36.0 - gap)
    mark = len(ingest_log)
    for s in (3, 7, 11):
        rigs[s].show_entry(entry["img"])
    t_p3 = time.monotonic()
    while time.monotonic() - t_p3 < 50.0:
        if any(r[2] == entry["key"] for r in ingest_log[mark:]):
            await asyncio.sleep(6.0)  # let rival feeds' votes resolve too
            break
        await asyncio.sleep(0.25)
    for s in (3, 7, 11):
        rigs[s].show_quiet()
    hits = [r for r in ingest_log[mark:] if r[2] == entry["key"]]
    st = monitor.status()
    report["P3_dedup_3_feeds"] = {
        "emissions_for_key": len(hits),
        "emitting_slots": [h[1] for h in hits],
        "duplicates_suppressed_total": st["cross_feed"]["duplicates_suppressed"],
        "pass": len(hits) == 1,
    }
    print("[P3]", report["P3_dedup_3_feeds"])

    # P4: frozen feed 7 + live injection on feed 8
    rigs[7].freeze()
    await asyncio.sleep(20.0)
    entry = entries[2]
    gap = time.monotonic() - last_used.get(entry["key"], -1e9)
    if gap < 36.0:
        await asyncio.sleep(36.0 - gap)
    last_used[entry["key"]] = time.monotonic()
    rec8 = await run_injection(monitor, rigs[8], entry)
    feeds_h = monitor.status()["feeds"]
    health = (
        {rec.get("observer_slot"): rec for rec in feeds_h.values()}
        if isinstance(feeds_h, dict)
        else {}
    )
    rigs[7].unfreeze()
    report["P4_frozen_feed"] = {
        "feed7_health_state": health.get(7, {}).get("state"),
        "feed8_injection": rec8,
        "monitor_running": monitor.status()["running"],
        # Probe intent: the frozen feed is flagged UNHEALTHY and the
        # fleet is unaffected. Which unhealthy label fires depends on
        # how the freeze presents: identical frame_id + pixels reads
        # as no_signal (no new frames) or stale_frames (frames arrive
        # but never change) — both are the tracker's designed states
        # for a dead feed; run4 of this harness observed stale_frames,
        # run5 observed no_signal. "ok" is the only failing state.
        "pass": (
            health.get(7, {}).get("state") in ("stale_frames", "no_signal")
            and rec8["t_emit_s"] is not None
        ),
    }
    print("[P4]", report["P4_frozen_feed"]["feed7_health_state"], rec8)
    await monitor.stop()

    # ---------------- Run B: batch 4 comparison ----------------------
    cfg_b4 = CaptureConfig(backend="none", monitor_batch_size=4)
    monitor_b = build_multi_feed_monitor(
        ingest=ingest_shim, feeds=[spec(rigs[n]) for n in range(1, 17)], config=cfg_b4
    )
    await monitor_b.start()
    await asyncio.sleep(3.0)
    inj_b = []
    for i, slot in enumerate([16, 13]):
        entry = entries[(i + 1) % len(entries)]
        gap = time.monotonic() - last_used.get(entry["key"], -1e9)
        if gap < 36.0:
            await asyncio.sleep(36.0 - gap)
        last_used[entry["key"]] = time.monotonic()
        rec = await run_injection(monitor_b, rigs[slot], entry, hold_s=60.0)
        inj_b.append(rec)
        print(f"[P2-batch4] feed {slot:2d}: emit={rec['t_emit_s']}")
        await asyncio.sleep(3.0)
    emits_b = [r["t_emit_s"] for r in inj_b if r["t_emit_s"] is not None]
    report["P2_batch4_latency"] = {
        "injections": inj_b,
        "emitted": len(emits_b),
        "median": round(statistics.median(emits_b), 3) if emits_b else None,
        "max": max(emits_b) if emits_b else None,
    }
    await monitor_b.stop()

    # ---------------- Run C: join / leave ----------------------------
    monitor_c = build_multi_feed_monitor(
        ingest=ingest_shim,
        feeds=[spec(rigs[n]) for n in range(1, 13)],
        config=cfg_default,
    )
    await monitor_c.start()
    await asyncio.sleep(4.0)
    for n in range(13, 17):
        monitor_c.add_feed(spec(rigs[n]))
    await asyncio.sleep(2.0)
    entry = entries[3]
    gap = time.monotonic() - last_used.get(entry["key"], -1e9)
    if gap < 36.0:
        await asyncio.sleep(36.0 - gap)
    rec15 = await run_injection(monitor_c, rigs[15], entry)
    feeds_before = monitor_c.status()["feeds_total"]
    monitor_c.remove_feed(rigs[15].source_id)
    await asyncio.sleep(2.5)
    feeds_after = monitor_c.status()["feeds_total"]
    report["P5_join_leave"] = {
        "feeds_total_after_join": feeds_before,
        "feed15_injection": rec15,
        "feeds_total_after_remove": feeds_after,
        "pass": (
            feeds_before == 16
            and rec15["t_emit_s"] is not None
            and rec15["slot_on_event"] == 15
            and feeds_after == 15
        ),
    }
    print("[P5]", report["P5_join_leave"])
    await monitor_c.stop()

    ld.stop()
    vi.stop()
    await bus.disconnect()

    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    out = Path(args.out)
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "frames"}, indent=2))
    print(f"[done] report -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
