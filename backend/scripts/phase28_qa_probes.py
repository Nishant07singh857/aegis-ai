#!/usr/bin/env python3
"""
Phase 28 INDEPENDENT QA probes (Worker E / W5 — author wrote NO
production code in this phase).

Verifies, with fresh measurements on REAL harvested frames (Phase 26
harness frame set; composites/synthetics are LABELLED as such):

  W0  director default mode = semi_auto / env override / explicit wins
  W1  BandChangeFilter: false-skip on the measured design (GT pairs
      recomputed HERE with the production parser), observe() fail-open
  W2  parse costs (quiet / entry medians) + parser output identity vs
      the Phase 28 profile report's before/after outputs, and a direct
      legacy-structure-gate on/off A/B over real frames
  A   entry appearing BELOW an existing entry (labelled composites of
      real frame rows / real strips) — filter decision + forced-parse
  B   back-to-back entries on one feed (vote accounting, no starvation)
  C   background-motion quiet feed, 30 samples -> ZERO false events
  D   entry expiry -> no phantom emission; filter expiry catches
  E   low-contrast new row (LABELLED synthetic) + the real marginal
      MUST_TRUE pair; worst-case samples-to-detect via force_parse_every
  F   parallel dedup on 3 feeds + round-robin fairness over 16 feeds
  G   band-filter kill-switch env + observe() raising -> fail-open parse
  H   sampler sequential fallback via env

Writes scripts/phase28_qa_report.json. No production code is touched.

Usage:
    /tmp/p26-venv/bin/python scripts/phase28_qa_probes.py
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import phase28_profile as prof  # noqa: E402  (frame loaders only)
from app.vision.capture.frame_grabber import CapturedFrame, FrameSource  # noqa: E402

REPORT: dict = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def section(name, data):
    REPORT[name] = data
    print(f"[{name}] {json.dumps(data, default=str)[:500]}")


# ----------------------------------------------------------------------
# Frame providers (QA scaffolding only)
# ----------------------------------------------------------------------
class ScriptedProvider:
    """Returns CapturedFrames from a scripted list of images; the test
    advances .idx / swaps .frames to script appearances/expiries."""

    def __init__(self, source_id, frames):
        self.source_id = source_id
        self.frames = list(frames)
        self.idx = 0
        self._fid = 0
        self.cycle = False

    def __call__(self):
        img = self.frames[self.idx % len(self.frames)]
        if self.cycle:
            self.idx += 1
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


def make_service(ingest, specs, config=None):
    from app.vision.capture.config import CaptureConfig
    from app.vision.multi_feed_monitor import build_multi_feed_monitor

    return build_multi_feed_monitor(
        ingest=ingest,
        feeds=specs,
        config=config or CaptureConfig(backend="none"),
    )


def spec(slot, provider):
    from app.vision.multi_feed_monitor import FeedSourceSpec

    return FeedSourceSpec(
        slot_number=slot,
        source_id=f"aegis-observer-slot-{slot:02d}",
        frame_provider=provider,
    )


class Collector:
    def __init__(self):
        self.events = []

    async def __call__(self, ev):
        self.events.append(ev)

    def kill_knock(self):
        out = []
        for ev in self.events:
            et = str(getattr(ev, "event_type", ""))
            if "KILL" in et or "KNOCK" in et or et in ("kill", "knock"):
                payload = getattr(ev, "payload", {}) or {}
                out.append(
                    {
                        "type": et,
                        "slot": payload.get("observer_slot"),
                        "key": payload.get("dedup_key"),
                    }
                )
        return out


# ----------------------------------------------------------------------
# W0 — director default mode (independent of Worker A's test file)
# ----------------------------------------------------------------------
async def probe_w0():
    from app.services.director_service import (
        DEFAULT_DIRECTOR_MODE,
        DirectorService,
        default_director_mode,
    )

    res = {"constant": DEFAULT_DIRECTOR_MODE}
    env = "AEGIS_DIRECTOR_DEFAULT_MODE"
    saved = os.environ.pop(env, None)
    try:
        res["no_env_fresh"] = DirectorService().snapshot()["mode"]
        res["no_env_helper"] = default_director_mode()
        os.environ[env] = "full_auto"
        res["env_full_auto"] = DirectorService().snapshot()["mode"]
        os.environ[env] = "auto"  # legacy alias
        res["env_legacy_auto"] = DirectorService().snapshot()["mode"]
        os.environ[env] = "manual"
        res["env_manual"] = DirectorService().snapshot()["mode"]
        os.environ[env] = "turbo-nonsense"
        res["env_invalid"] = DirectorService().snapshot()["mode"]
        del os.environ[env]

        async def explicit():
            svc = DirectorService()
            await svc.set_mode("full_auto", updated_by="qa-probe")
            os.environ[env] = "manual"  # env changes AFTER explicit set
            fresh = DirectorService()
            return svc.snapshot()["mode"], fresh.snapshot()["mode"]

        mine, fresh = await explicit()
        res["explicit_after_env_change"] = mine
        res["fresh_gets_env_default"] = fresh
        del os.environ[env]

        from app.services.replay.queue import ReplayQueue

        q_unset = ReplayQueue(replay_service=None, director_provider=lambda: {})
        res["replay_fallback_unset"] = q_unset.director_mode()
        q_set = ReplayQueue(
            replay_service=None,
            director_provider=lambda: {"mode": "manual"},
        )
        res["replay_fallback_set"] = q_set.director_mode()
    finally:
        os.environ.pop(env, None)
        if saved is not None:
            os.environ[env] = saved
    res["pass"] = (
        res["constant"] == "semi_auto"
        and res["no_env_fresh"] == "semi_auto"
        and res["no_env_helper"] == "semi_auto"
        and res["env_full_auto"] == "full_auto"
        and res["env_legacy_auto"] == "full_auto"
        and res["env_manual"] == "manual"
        and res["env_invalid"] == "semi_auto"
        and res["explicit_after_env_change"] == "full_auto"
        and res["fresh_gets_env_default"] == "manual"
        and res["replay_fallback_unset"] == "semi_auto"
        and res["replay_fallback_set"] == "manual"
    )
    section("W0_director_default_mode", res)


# ----------------------------------------------------------------------
# Frame sets + ground truth (shared)
# ----------------------------------------------------------------------
def load_all_frames():
    from app.vision.detectors.killfeed import KillFeedDetector

    warm = KillFeedDetector()
    matcher = warm._get_matcher()
    assert matcher is not None and matcher.available, "icon matcher unavailable"
    entry_frames = prof.load_entry_frames()
    det_verify = prof.fresh_detector(matcher)
    quiet_frames = prof.extract_quiet_frames(det_verify)
    seqs = prof.extract_sequences()
    return matcher, entry_frames, quiet_frames, seqs


def gt_keys_per_frame(matcher, frames):
    det = prof.fresh_detector(matcher)
    keys = []
    now = 5000.0
    for img in frames:
        recs = prof.candidate_output(det, img, now)
        keys.append(sorted(r["key"] for r in recs))
        now += 100.0
    return keys


# ----------------------------------------------------------------------
# W1 — band filter on the measured design + fail-open
# ----------------------------------------------------------------------
def probe_w1(matcher, quiet_frames, seqs):
    from app.vision.band_change import BandChangeFilter

    comp_frames, comp_labels = prof.build_composites(quiet_frames)[
        "stack_composite"
    ]
    all_seqs = {k: [im for _, im in v] for k, v in seqs.items()}
    all_seqs["stack_composite"] = [im for _, im in comp_frames]

    pair_rows = []
    decisions = {}
    obs_costs = []
    for sname, frames in all_seqs.items():
        keys_per = gt_keys_per_frame(matcher, frames)
        filt = BandChangeFilter()
        dec = []
        for img in frames:
            t = time.perf_counter()
            dec.append(bool(filt.observe(f"qa-{sname}", img)))
            obs_costs.append(time.perf_counter() - t)
        decisions[sname] = dec
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
            pair_rows.append(
                {"seq": sname, "i": i, "label": label, "caught": dec[i + 1]}
            )

    must = [r for r in pair_rows if r["label"] == "MUST_TRUE"]
    skip = [r for r in pair_rows if r["label"] == "SKIP_OK"]
    exp = [r for r in pair_rows if r["label"] == "EXPIRY"]
    res = {
        "filter_defaults": {
            "threshold": BandChangeFilter().threshold,
            "row_threshold": BandChangeFilter().row_threshold,
        },
        "pair_counts": {
            "MUST_TRUE": len(must),
            "SKIP_OK": len(skip),
            "EXPIRY": len(exp),
        },
        "false_skips": sum(1 for r in must if not r["caught"]),
        "must_true_pairs": must,
        "expiry_caught": sum(1 for r in exp if r["caught"]),
        "expiry_pairs": exp,
        "skip_rate_on_skip_ok": round(
            sum(1 for r in skip if not r["caught"]) / max(1, len(skip)), 3
        ),
        "observe_cost_median_ms": round(statistics.median(obs_costs) * 1000, 2),
    }

    # fail-open battery: garbage inputs must ALL return True, never raise
    filt = BandChangeFilter()
    bad_inputs = {
        "none": None,
        "string": "not a frame",
        "int": 12345,
        "object": object(),
        "empty_array": np.zeros((0, 0, 3), dtype=np.uint8),
        "tiny_array": np.zeros((4, 4), dtype=np.uint8),
        "1d_array": np.zeros((10,), dtype=np.uint8),
        "float_frame": np.random.rand(360, 640, 3).astype(np.float64),
    }
    fail_open = {}
    for name, bad in bad_inputs.items():
        try:
            fail_open[name] = bool(filt.observe("qa-bad", bad))
        except Exception as exc:  # noqa: BLE001
            fail_open[name] = f"RAISED: {exc!r}"
    res["fail_open"] = fail_open
    res["fail_open_pass"] = all(v is True for v in fail_open.values())
    res["pass"] = res["false_skips"] == 0 and res["fail_open_pass"]
    section("W1_band_filter", res)
    return {"all_seqs": all_seqs, "decisions": decisions}


# ----------------------------------------------------------------------
# W2 — parse costs + output identity + legacy-gate A/B
# ----------------------------------------------------------------------
def probe_w2(matcher, entry_frames, quiet_frames):
    profile = json.loads(
        (BACKEND / "scripts" / "phase28_profile_report.json").read_text()
    )

    def time_set(frames, reps=2):
        totals, outputs = [], {}
        for fid, img in frames:
            for rep in range(reps):
                det = prof.fresh_detector(matcher)
                t = time.perf_counter()
                recs = prof.candidate_output(det, img, 1000.0 + rep * 100.0)
                totals.append(time.perf_counter() - t)
                outputs[fid] = recs
        return totals, outputs

    entry_totals, entry_out = time_set(entry_frames)
    quiet_totals, quiet_out = time_set(
        [(f"quiet_{i}", im) for i, im in enumerate(quiet_frames)]
    )
    res = {
        "entry_median_ms": round(statistics.median(entry_totals) * 1000, 1),
        "entry_n": len(entry_totals),
        "quiet_median_ms": round(statistics.median(quiet_totals) * 1000, 1),
        "quiet_n": len(quiet_totals),
    }
    mismatches = []
    for run_label in ("before", "after"):
        ref = profile["runs"][run_label]
        for fid, recs in ref["profile_entry"]["outputs"].items():
            if entry_out.get(fid) != recs:
                mismatches.append({"vs": run_label, "set": "entry", "frame": fid})
        for fid, recs in ref["profile_quiet"]["outputs"].items():
            if quiet_out.get(fid) != recs:
                mismatches.append({"vs": run_label, "set": "quiet", "frame": fid})
    res["output_mismatches_vs_profile_report"] = mismatches

    # Direct legacy-gate A/B: gate ON (shipped) vs OFF, fresh detector
    # per frame, over entry stills + quiet stills. Outputs must match.
    gate_diffs = []
    for fid, img in entry_frames + [
        (f"quiet_{i}", im) for i, im in enumerate(quiet_frames)
    ]:
        outs = {}
        for gate in (True, False):
            det = prof.fresh_detector(matcher)
            det.config["legacy_structure_gate"] = gate
            outs[gate] = prof.candidate_output(det, img, 7000.0)
        if outs[True] != outs[False]:
            gate_diffs.append(
                {"frame": fid, "gate_on": outs[True], "gate_off": outs[False]}
            )
    res["legacy_gate_ab_diffs"] = gate_diffs
    res["pass"] = not mismatches and not gate_diffs
    section("W2_parse_costs_and_identity", res)


# ----------------------------------------------------------------------
# Monitor-level probes (real FeedMonitor / service, real readers)
# ----------------------------------------------------------------------
async def drive_samples(service, source_id, n, fm=None):
    """Drive n samples through the service path; return raw per-sample
    event lists via the FeedMonitor for counter inspection."""
    out = []
    for _ in range(n):
        evs = await service.sample_feed_once(source_id)
        out.append(evs)
    return out


def probe_a(entry_frames, quiet_frames, seqs):
    """A: entry below an existing entry.

    Two labelled composites:
      * stack_strips — W1's construction (real *_entry.png strips pasted
        on real quiet frames; the production parser reads NO candidate
        from these per the profile GT, so this level tests the FILTER).
      * stack_rows — REAL full-frame rows: entry row of one real entry
        frame pasted at the second-row position of another real entry
        frame (native scale, native pixels). Parser GT recomputed here.
    Plus the filter decision on each appearance transition and the
    monitor-level forced-parse behaviour while the stacked frame is
    held on a feed.
    """
    from app.vision.band_change import BandChangeFilter
    from app.vision.detectors.killfeed import DEFAULT_KILL_FEED_REGION

    res = {}
    by_id = dict(entry_frames)
    base = by_id["d1_e0085_t4136"].copy()       # 1 real entry (top row)
    donor = by_id["d1_e0011_t2815"]             # another real entry frame
    x1 = int(640 * DEFAULT_KILL_FEED_REGION[0])
    x2 = int(640 * DEFAULT_KILL_FEED_REGION[2])
    row_h = 21
    y_top = int(0.26 * 360)
    stacked = base.copy()
    stacked[y_top + row_h:y_top + 2 * row_h, x1:x2] = donor[
        y_top:y_top + row_h, x1:x2
    ]
    res["stack_rows_label"] = (
        "LABELLED COMPOSITE: real entry row of d1_e0011 pasted below the "
        "real entry of d1_e0085, native pixels/scale"
    )
    # parser GT on base vs stacked (fresh detector, shared matcher set
    # up by caller through candidate_output on fresh detectors)
    res["stack_rows_gt"] = "computed_in_filter_section"

    filt = BandChangeFilter()
    seq_imgs = [quiet_frames[0], base, base, stacked, stacked]
    dec = [bool(filt.observe("qa-stack", im)) for im in seq_imgs]
    res["filter_decisions_quiet_base_base_stacked_stacked"] = dec
    res["appearance_transition_caught_by_filter"] = dec[3]

    # W1 strip composite decisions (same construction as profile)
    comp_frames, comp_labels = prof.build_composites(quiet_frames)[
        "stack_composite"
    ]
    filt2 = BandChangeFilter()
    dec2 = [bool(filt2.observe("qa-strip", im)) for _, im in comp_frames]
    res["strip_composite_labels"] = comp_labels
    res["strip_composite_decisions"] = dec2
    res["strip_B_appearance_caught"] = dec2[3]
    section("A_filter_level", res)
    return {"base": base, "stacked": stacked}


async def probe_a_monitor(base, stacked):
    """Hold base (1 entry) then stacked (2 entries) on a real feed."""
    coll = Collector()
    prov = ScriptedProvider("aegis-observer-slot-05", [base])
    svc = make_service(coll, [spec(5, prov)])
    sid = prov.source_id
    fm = svc.get_feed(sid)
    await drive_samples(svc, sid, 5)
    st1 = fm.sampler_stats()
    kk1 = len(coll.kill_knock())
    prov.frames = [stacked]
    await drive_samples(svc, sid, 8)
    st2 = fm.sampler_stats()
    res = {
        "phase1_base": {"sampler": st1, "kill_knock_emissions": kk1},
        "phase2_stacked": {
            "parsed_delta": st2["samples_parsed"] - st1["samples_parsed"],
            "skipped_delta": st2["samples_skipped"] - st1["samples_skipped"],
            "kill_knock_emissions_total": len(coll.kill_knock()),
            "emissions": coll.kill_knock(),
        },
    }
    section("A_monitor_level", res)


async def probe_b(entry_frames):
    """B: back-to-back entries on one feed."""
    by_id = dict(entry_frames)
    coll = Collector()
    prov = ScriptedProvider(
        "aegis-observer-slot-02", [by_id["d1_e0011_t2815"]]
    )
    svc = make_service(coll, [spec(2, prov)])
    sid = prov.source_id
    await drive_samples(svc, sid, 5)
    after1 = coll.kill_knock()
    prov.frames = [by_id["d1_e0022_t2925"]]  # immediate switch, no gap
    emit_at = None
    for i in range(5):
        await drive_samples(svc, sid, 1)
        if len(coll.kill_knock()) > len(after1):
            emit_at = i + 1
            break
    res = {
        "entry1_emissions_after_5_samples": after1,
        "entry2_samples_to_emit_after_switch": emit_at,
        "total_emissions": coll.kill_knock(),
        "pass": (
            len(after1) == 1
            and emit_at is not None
            and len(coll.kill_knock()) == 2
            and coll.kill_knock()[0]["key"] != coll.kill_knock()[1]["key"]
        ),
    }
    section("B_back_to_back", res)


async def probe_c(seqs):
    """C: background-motion quiet feed, 30 real VOD frames, 0 events."""
    frames = []
    for w in ("quiet_win0", "quiet_win1", "quiet_win2"):
        frames += [im for _, im in seqs[w]]
    coll = Collector()
    prov = ScriptedProvider("aegis-observer-slot-09", frames)
    prov.cycle = True
    svc = make_service(coll, [spec(9, prov)])
    sid = prov.source_id
    fm = svc.get_feed(sid)
    raw_kk = 0
    for k in range(30):
        evs = fm.sample_once(2_000_000.0 + 2.0 * k)
        for ev in evs:
            et = str(getattr(ev, "event_type", ""))
            if "KILL" in et or "KNOCK" in et:
                raw_kk += 1
    res = {
        "samples": 30,
        "raw_kill_knock_events_from_sample_once": raw_kk,
        "sampler": fm.sampler_stats(),
        "pass": raw_kk == 0,
    }
    section("C_quiet_background_motion", res)


async def probe_d(entry_frames, quiet_frames):
    """D: entry appears then expires — no phantom emission."""
    by_id = dict(entry_frames)
    coll = Collector()
    prov = ScriptedProvider(
        "aegis-observer-slot-04", [by_id["d1_e0030_t2995"]]
    )
    svc = make_service(coll, [spec(4, prov)])
    sid = prov.source_id
    fm = svc.get_feed(sid)
    await drive_samples(svc, sid, 4)
    during = len(coll.kill_knock())
    prov.frames = [quiet_frames[3]]
    prov.cycle = False
    await drive_samples(svc, sid, 8)
    res = {
        "emissions_while_entry_held": during,
        "emissions_after_8_quiet_samples": len(coll.kill_knock()),
        "voter_pending_unconfirmed_after_expiry": (
            fm.voter.has_pending_unconfirmed()
        ),
        "sampler": fm.sampler_stats(),
        "pass": during == 1 and len(coll.kill_knock()) == 1,
    }
    section("D_entry_expiry", res)


def probe_e_filter(base, stacked):
    """E (filter level): low-contrast new row — LABELLED SYNTHETIC:
    the added row is alpha-blended toward the base frame's own row
    (0.5 / 0.35 opacity). Plus forced-parse worst-case bound, measured
    at monitor level in probe_e_monitor."""
    from app.vision.band_change import BandChangeFilter
    from app.vision.detectors.killfeed import DEFAULT_KILL_FEED_REGION

    x1 = int(640 * DEFAULT_KILL_FEED_REGION[0])
    x2 = int(640 * DEFAULT_KILL_FEED_REGION[2])
    y_top = int(0.26 * 360)
    row = slice(y_top + 21, y_top + 42)
    out = {}
    for alpha in (0.5, 0.35):
        faded = base.copy()
        blended = (
            alpha * stacked[row, x1:x2].astype(np.float32)
            + (1 - alpha) * base[row, x1:x2].astype(np.float32)
        )
        faded[row, x1:x2] = blended.astype(np.uint8)
        filt = BandChangeFilter()
        filt.observe("qa-e", base)
        filt.observe("qa-e", base)
        out[f"alpha_{alpha}"] = bool(filt.observe("qa-e", faded))
    section("E_low_contrast_filter", {
        "label": "SYNTHETIC: added real row alpha-blended toward base",
        "filter_says_changed": out,
    })
    return out


async def probe_e_monitor(base):
    """E (monitor level): a STATIC held frame (filter will skip) —
    forced parse must land within force_parse_every framed samples.
    Worst-case samples-to-parse measured directly from counters."""
    coll = Collector()
    prov = ScriptedProvider("aegis-observer-slot-06", [base])
    svc = make_service(coll, [spec(6, prov)])
    sid = prov.source_id
    fm = svc.get_feed(sid)
    gaps = []
    last_parsed = 0
    since_parse = 0
    for _ in range(12):
        await drive_samples(svc, sid, 1)
        st = fm.sampler_stats()
        if st["samples_parsed"] > last_parsed:
            gaps.append(since_parse)
            last_parsed = st["samples_parsed"]
            since_parse = 0
        else:
            since_parse += 1
    st = fm.sampler_stats()
    res = {
        "force_parse_every_config": fm._force_parse_every,
        "samples": 12,
        "parsed": st["samples_parsed"],
        "skipped": st["samples_skipped"],
        "bypass_forced": st["prefilter_bypass_forced"],
        "max_consecutive_skips_observed": max(gaps) if gaps else None,
        "pass": st["samples_parsed"] >= 4 and (not gaps or max(gaps) <= 3),
    }
    section("E_forced_parse_bound", res)


async def probe_f(entry_frames, quiet_frames):
    """F: same entry on 3 feeds at once + 13 quiet feeds, driven by
    run_tick (the real scheduler, parallel sampler)."""
    from phase26_harness import FeedRig

    by_id = dict(entry_frames)
    coll = Collector()
    rigs = {}
    specs = []
    for n in range(1, 17):
        rig = FeedRig(n, quiet_frames)
        if n in (3, 7, 11):
            rig.show_entry(by_id["d1_e0022_t2925"])
        rigs[n] = rig
        specs.append(spec(n, rig.provider))
    svc = make_service(coll, specs)
    t0 = time.monotonic()
    ticks = 0
    # Fixed 24 ticks (6 samples/feed at batch 4) so the fairness check
    # has enough samples; rival feeds confirm in ticks ~6-7 and hit the
    # cross-feed gate well inside this window.
    while ticks < 24 and time.monotonic() - t0 < 45.0:
        await svc.run_tick()
        ticks += 1
    status = svc.status()
    feeds = status["feeds"]
    samples = {
        sid: rec.get("samples") for sid, rec in feeds.items()
    } if isinstance(feeds, dict) else {}
    kk = coll.kill_knock()
    res = {
        "ticks": ticks,
        "elapsed_s": round(time.monotonic() - t0, 1),
        "emissions_for_shared_entry": kk,
        "duplicates_suppressed_total": status["cross_feed"][
            "duplicates_suppressed"
        ],
        "samples_per_feed": samples,
        "min_samples_any_feed": min(samples.values()) if samples else None,
        "sampler": status["sampler"],
        "pass": (
            len(kk) == 1
            and kk[0]["slot"] in (3, 7, 11)
            and (min(samples.values()) if samples else 0) >= 4
        ),
        "winner_is_rr_first_of_the_three": (kk[0]["slot"] == 3) if kk else None,
    }
    section("F_parallel_dedup_fairness", res)


async def probe_g(entry_frames, quiet_frames):
    """G: kill-switch env + observe() raising -> fail-open."""
    from app.vision.multi_feed_monitor import _load_band_filter

    res = {}
    # 1) kill-switch
    os.environ["AEGIS_MONITOR_BAND_FILTER"] = "0"
    try:
        res["load_with_kill_switch"] = _load_band_filter()
        coll = Collector()
        prov = ScriptedProvider(
            "aegis-observer-slot-08", [quiet_frames[0]]
        )
        svc = make_service(coll, [spec(8, prov)])
        sid = prov.source_id
        fm = svc.get_feed(sid)
        res["service_filter_is_none"] = fm._band_filter is None
        await drive_samples(svc, sid, 5)
        st = fm.sampler_stats()
        res["kill_switch_stats"] = st
        res["kill_switch_pass"] = st["samples_skipped"] == 0 and st[
            "samples_parsed"
        ] == 5
    finally:
        del os.environ["AEGIS_MONITOR_BAND_FILTER"]
    res["load_without_kill_switch"] = type(_load_band_filter()).__name__

    # 2) observe() raises -> parse still happens (fail-open)
    from app.vision.capture.config import CaptureConfig
    from app.vision.multi_feed_monitor import (
        FeedMonitor,
        default_reader_factory,
    )

    class RaisingFilter:
        def observe(self, key, frame):
            raise RuntimeError("qa-injected filter failure")

    coll2 = Collector()
    svc2 = make_service(coll2, [])
    prov2 = ScriptedProvider(
        "aegis-observer-slot-10", [dict(entry_frames)["d1_e0011_t2815"]]
    )
    fm2 = FeedMonitor(
        spec(10, prov2),
        default_reader_factory(10, prov2.source_id),
        svc2.health,
        CaptureConfig(backend="none"),
        band_filter=RaisingFilter(),
        force_parse_every=3,
    )
    errors_before = 0
    evs_total = []
    for k in range(4):
        evs_total += fm2.sample_once(3_000_000.0 + 2.0 * k)
    st2 = fm2.sampler_stats()
    kk = [
        e for e in evs_total
        if "KILL" in str(getattr(e, "event_type", ""))
        or "KNOCK" in str(getattr(e, "event_type", ""))
    ]
    res["raising_filter_stats"] = st2
    res["raising_filter_raw_kill_knock_events"] = len(kk)
    res["raising_filter_pass"] = (
        st2["samples_parsed"] == 4
        and st2["prefilter_errors"] >= 1
        and len(kk) == 1
    )
    res["pass"] = bool(
        res.get("kill_switch_pass") and res.get("raising_filter_pass")
        and res["load_with_kill_switch"] is None
    )
    section("G_kill_switch_and_fail_open", res)


async def probe_h(entry_frames):
    """H: AEGIS_MONITOR_SAMPLER_MODE=sequential -> serial path, same
    result (same key emitted) as the default-mode probe B entry 1."""
    from app.vision.capture.config import CaptureConfig
    from app.vision.multi_feed_monitor import (
        resolve_sampler_mode,
        resolve_sampler_workers,
    )

    cfg = CaptureConfig(backend="none")
    res = {"default_mode": resolve_sampler_mode(cfg),
           "default_workers_16": resolve_sampler_workers(cfg, 16)}
    os.environ["AEGIS_MONITOR_SAMPLER_MODE"] = "sequential"
    try:
        res["env_mode"] = resolve_sampler_mode(cfg)
        res["env_workers_16"] = resolve_sampler_workers(cfg, 16)
        coll = Collector()
        prov = ScriptedProvider(
            "aegis-observer-slot-12", [dict(entry_frames)["d1_e0011_t2815"]]
        )
        svc = make_service(coll, [spec(12, prov)], config=cfg)
        sid = prov.source_id
        res["service_sampler_status"] = svc.status()["sampler"]
        await drive_samples(svc, sid, 5)
        res["emissions"] = coll.kill_knock()
    finally:
        del os.environ["AEGIS_MONITOR_SAMPLER_MODE"]
    res["pass"] = (
        res["env_mode"] == "sequential"
        and res["env_workers_16"] == 1
        and len(res["emissions"]) == 1
        and res["emissions"][0]["slot"] == 12
    )
    section("H_sampler_sequential_fallback", res)


# ----------------------------------------------------------------------
async def main() -> int:
    await probe_w0()
    matcher, entry_frames, quiet_frames, seqs = load_all_frames()

    w1 = probe_w1(matcher, quiet_frames, seqs)

    # GT for the real-row stack (probe A) — parser view of base/stacked
    a_frames = probe_a(entry_frames, quiet_frames, seqs)
    det = prof.fresh_detector(matcher)
    gt_base = prof.candidate_output(det, a_frames["base"], 9000.0)
    det = prof.fresh_detector(matcher)
    gt_stacked = prof.candidate_output(det, a_frames["stacked"], 9000.0)
    section("A_stack_rows_parser_gt", {
        "base_keys": [r["key"][:12] for r in gt_base],
        "stacked_keys": [r["key"][:12] for r in gt_stacked],
        "base_n": len(gt_base),
        "stacked_n": len(gt_stacked),
    })

    probe_w2(matcher, entry_frames, quiet_frames)
    await probe_a_monitor(a_frames["base"], a_frames["stacked"])
    await probe_b(entry_frames)
    await probe_c(seqs)
    await probe_d(entry_frames, quiet_frames)
    probe_e_filter(a_frames["base"], a_frames["stacked"])
    await probe_e_monitor(a_frames["base"])
    await probe_f(entry_frames, quiet_frames)
    await probe_g(entry_frames, quiet_frames)
    await probe_h(entry_frames)

    REPORT["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    out = BACKEND / "scripts" / "phase28_qa_report.json"
    out.write_text(json.dumps(REPORT, indent=2, default=str))
    print(f"[done] QA report -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
