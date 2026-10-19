# backend/app/ml/datasets/selftest.py
"""Self-test for the Phase 15 W1 data flywheel.

Generates SYNTHETIC data only (no real footage, no GPU, no network):

1. Builds a tiny synthetic mp4 + timestamps.csv (frames carry their index
   encoded in the top-left pixel block so we can prove the labeler sampled
   the *right* frames).
2. Builds a synthetic NormalizedEvent-shaped events.jsonl containing
   in-window events, one out-of-window event, one non-label event, and two
   malformed lines.
3. Runs :func:`build_dataset` twice and asserts manifest correctness,
   skip accounting, and version incrementing.
4. Runs :func:`record_match` on the synthetic ``"test"`` source and asserts
   the mp4 + timestamps.csv are coherent.
5. Runs the recorder CLI end-to-end via subprocess.

Run from ``backend/``::

    python -m app.ml.datasets.selftest
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile

import numpy as np

from .auto_labeler import (
    MANIFEST_COLUMNS,
    build_dataset,
    join_events_to_frames,
    load_events,
    load_timestamps,
)
from .recorder import record_match

FPS = 10.0
N_FRAMES = 100  # 10 seconds of video
W, H = 160, 90
T0 = 1_750_000_000.0  # fixed synthetic epoch base (deterministic)


def _synthetic_frame(i: int) -> np.ndarray:
    """Solid-ish frame with the index encoded in the top-left 8x8 block."""
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    frame[:, :] = (i % 64, (i * 2) % 64, (i * 3) % 64)  # gentle gradient base
    frame[0:8, 0:8] = (i % 256, (i * 3) % 256, (i * 7) % 256)
    return frame


def _write_synthetic_video(path: str) -> None:
    import cv2

    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    assert writer.isOpened(), "VideoWriter failed to open in self-test"
    for i in range(N_FRAMES):
        writer.write(_synthetic_frame(i))
    writer.release()


def _write_timestamps(path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["frame_idx", "epoch_ts"])
        for i in range(N_FRAMES):
            w.writerow([i, f"{T0 + i / FPS:.6f}"])


def _write_events(path: str) -> None:
    lines = [
        # in-window label events
        {"event_id": "e1", "event_type": "kill", "ts": T0 + 2.0, "match_id": "M1",
         "player": "NinjaBoy", "team": "K9", "victim": "Taurus",
         "victim_team": "8Bit", "weapon": "M416",
         "payload": {"alive_count": 60}},
        {"event_id": "e2", "event_type": "knock", "ts": T0 + 5.05, "match_id": "M1",
         "player": "Sarang", "team": "Epig", "victim": "Lobster",
         "victim_team": "Velt", "weapon": "Kar98k", "payload": {}},
        {"event_id": "e3", "event_type": "team_wipe", "ts": T0 + 9.9, "match_id": "M1",
         "player": "Areeb", "team": "Myth", "victim_team": "K9", "payload": {}},
        {"event_id": "e4", "event_type": "zone_update", "ts": T0 + 0.05, "match_id": "M1",
         "payload": {"zone_phase": 3}},
        {"event_id": "e5", "event_type": "zone_update", "ts": T0 + 8.0, "match_id": "M1",
         "payload": {}},
        # out-of-window label event -> must be skipped, counted
        {"event_id": "e6", "event_type": "kill", "ts": T0 + 50.0, "match_id": "M1",
         "player": "Ghost", "victim": "Phantom", "payload": {}},
        # non-label event inside the video range -> skipped, counted
        {"event_id": "e7", "event_type": "commentary", "ts": T0 + 4.0, "match_id": "M1",
         "payload": {"text": "what a rotate"}},
        # malformed lines -> counted as bad
        "THIS IS NOT JSON",
        json.dumps({"event_type": "kill", "player": "NoTs"}),  # missing ts
    ]
    with open(path, "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line if isinstance(line, str) else json.dumps(line))
            fh.write("\n")


def _expected_window(center_ts: float, window_s: float = 2.0):
    return [i for i in range(N_FRAMES) if abs((T0 + i / FPS) - center_ts) <= window_s]


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(f"SELFTEST FAILED: {msg}")
    print(f"  ok: {msg}")


def test_labeler(tmp: str) -> None:
    print("== auto_labeler on synthetic data ==")
    video = os.path.join(tmp, "match_M1.mp4")
    ts_csv = os.path.join(tmp, "timestamps.csv")
    events_jsonl = os.path.join(tmp, "events.jsonl")
    _write_synthetic_video(video)
    _write_timestamps(ts_csv)
    _write_events(events_jsonl)

    timestamps = load_timestamps(ts_csv)
    _check(len(timestamps) == N_FRAMES, f"loaded {N_FRAMES} timestamps")
    _check(timestamps[0] == (0, T0), "first timestamp exact")

    events, n_bad = load_events(events_jsonl)
    _check(n_bad == 2, f"2 malformed lines counted as bad (got {n_bad})")
    _check(len(events) == 7, f"7 parseable events loaded (got {len(events)})")

    labeled, skips = join_events_to_frames(events, timestamps, window_s=2.0)
    _check(len(labeled) == 5, f"5 events joined to frames (got {len(labeled)})")
    _check(skips.get("no_video_window") == 1, "out-of-window kill skipped+counted")
    _check(skips.get("non_label_event") == 1, "commentary skipped as non-label")

    report = build_dataset(
        video_path=video,
        timestamps_path=ts_csv,
        events_path=events_jsonl,
        out_root=tmp,
        collector="selftest",
        window_s=2.0,
    )
    _check(report.version == 1, "first build -> datasets/v1")
    _check(report.n_bad_lines == 2, "report carries bad-line count")
    _check(report.n_skipped == 2, "report carries skip count")

    ddir = report.dataset_dir
    manifest_path = os.path.join(ddir, "manifest.csv")
    frames_dir = os.path.join(ddir, "frames")
    with open(manifest_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    _check(
        list(rows[0].keys())[:9]
        == ["frame_path", "ts", "label_type", "killer", "victim", "team",
            "weapon", "alive_count", "zone_phase"],
        "manifest columns match the documented contract",
    )

    # Expected per-event frame sets (window ±2s at 10fps).
    expected = {
        "e1": _expected_window(T0 + 2.0),    # frames 0..40
        "e2": _expected_window(T0 + 5.05),    # frames 31..70
        "e3": _expected_window(T0 + 9.9),    # frames 79..99 (clipped at end)
        "e4": _expected_window(T0 + 0.05),    # frames 0..20 (clipped at start)
        "e5": _expected_window(T0 + 8.0),    # frames 60..99
    }
    _check(sum(map(len, expected.values())) == len(rows),
           f"manifest row count == sum of expected windows ({len(rows)})")

    by_event: dict = {}
    for r in rows:
        by_event.setdefault(r["event_id"], []).append(r)

    # Labels come ONLY from the real events — field-by-field check.
    e1 = by_event["e1"][0]
    _check(e1["label_type"] == "kill" and e1["killer"] == "NinjaBoy"
           and e1["victim"] == "Taurus" and e1["team"] == "K9"
           and e1["weapon"] == "M416" and e1["alive_count"] == "60",
           "kill row carries the event's real fields, nothing invented")
    e2 = by_event["e2"][0]
    _check(e2["label_type"] == "knock" and e2["killer"] == "Sarang"
           and e2["victim"] == "Lobster", "knock row correct")
    e3 = by_event["e3"][0]
    _check(e3["label_type"] == "team_wipe" and e3["victim"] == ""
           and e3["killer"] == "Areeb", "team_wipe row correct (no victim)")
    _check(by_event["e4"][0]["zone_phase"] == "3",
           "explicit zone_phase from payload preserved")
    _check(by_event["e5"][0]["zone_phase"] == "2",
           "missing zone_phase derived as 2nd zone_update (from logged events)")
    _check(all(r["label_type"] in {"kill", "knock", "team_wipe", "zone_update"}
               for r in rows),
           "no label type exists that wasn't in the event log")

    # Frame indices + timestamps in the manifest match the join exactly.
    for eid, idxs in expected.items():
        got = sorted(int(r["frame_idx"]) for r in by_event[eid])
        _check(got == idxs, f"event {eid}: manifest frame indices == window {idxs[0]}..{idxs[-1]}")
        for r in by_event[eid]:
            _check(abs(float(r["ts"]) - (T0 + int(r["frame_idx"]) / FPS)) < 1e-6,
                   f"event {eid}: manifest ts == timestamps.csv ts")
            _check(abs(float(r["ts"]) - next(e["ts"] for e in events if e["event_id"] == eid)) <= 2.0,
                   f"event {eid}: manifest ts within ±2s of event ts")
            fp = os.path.join(ddir, r["frame_path"])
            _check(os.path.isfile(fp), f"frame file exists: {r['frame_path']}")

    # Prove we sampled the RIGHT frames: decode one JPEG and check the
    # index encoded in its top-left block (tolerant: mp4+JPEG are lossy).
    import cv2

    probe = by_event["e2"][0]
    img = cv2.imread(os.path.join(ddir, probe["frame_path"]))
    _check(img is not None and img.shape[:2] == (H, W), "sampled JPEG decodes at full size")
    i = int(probe["frame_idx"])
    want = np.array([i % 256, (i * 3) % 256, (i * 7) % 256], dtype=float)
    gotmean = img[0:8, 0:8].reshape(-1, 3).mean(axis=0)
    _check(float(np.abs(gotmean - want).max()) < 40,
           f"decoded frame content matches encoded frame index {i} (lossy-tolerant)")

    # Dataset card + stats are rendered from real numbers.
    with open(os.path.join(ddir, "dataset_card.md"), encoding="utf-8") as fh:
        card = fh.read()
    _check(str(len(rows)) in card and "selftest" in card and "{{" not in card,
           "dataset_card.md rendered with real stats, no unfilled placeholders")
    with open(os.path.join(ddir, "stats.json"), encoding="utf-8") as fh:
        stats = json.load(fh)
    _check(stats["n_frames"] == len(rows) and stats["n_skipped"] == 2
           and stats["skip_reasons"]["no_video_window"] == 1,
           "stats.json consistent with manifest")
    _check(os.path.isfile(os.path.join(ddir, "events.jsonl"))
           and os.path.isfile(os.path.join(ddir, "timestamps.csv")),
           "inputs copied into the version dir for reproducibility")

    # Versioning: a second build must become v2, not overwrite v1.
    report2 = build_dataset(video, ts_csv, events_jsonl, tmp, collector="selftest")
    _check(report2.version == 2 and os.path.isdir(report2.dataset_dir),
           "second build -> datasets/v2 (v1 untouched)")
    _check(os.path.isfile(os.path.join(report.dataset_dir, "manifest.csv")),
           "v1 still intact after v2 build")


def test_labeler_cli(tmp: str) -> None:
    print("== auto_labeler CLI ==")
    video = os.path.join(tmp, "match_M1.mp4")  # written by test_labeler
    out = os.path.join(tmp, "cli-data")
    cmd = [sys.executable, "-m", "app.ml.datasets.auto_labeler",
           "--video", video,
           "--timestamps", os.path.join(tmp, "timestamps.csv"),
           "--events", os.path.join(tmp, "events.jsonl"),
           "--out", out,
           "--collector", "cli-test"]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=".",
                          timeout=180)
    _check(proc.returncode == 0, f"CLI exits 0 (stderr: {proc.stderr.strip()[-200:]})")
    ddir = os.path.join(out, "datasets", "v1")
    _check(os.path.isfile(os.path.join(ddir, "manifest.csv")),
           "CLI wrote datasets/v1/manifest.csv")
    _check(os.path.isfile(os.path.join(ddir, "dataset_card.md")),
           "CLI wrote datasets/v1/dataset_card.md")
    print("  " + proc.stdout.strip().splitlines()[0])


def test_recorder(tmp: str) -> None:
    print("== recorder on synthetic 'test' source ==")
    out = os.path.join(tmp, "rec")
    result = record_match("SELFTEST/01", out, fps=FPS, frame_source="test",
                         duration=1.0, frame_size=(W, H))
    _check(os.path.isfile(result.video_path), "mp4 written")
    _check(os.path.isfile(result.timestamps_path), "timestamps.csv written")
    _check("SELFTEST_01" in result.video_path, "match id sanitized for filename")

    with open(result.timestamps_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    _check([r["frame_idx"] for r in rows]
           == [str(i) for i in range(len(rows))], "frame_idx sequential from 0")
    tss = [float(r["epoch_ts"]) for r in rows]
    _check(all(b >= a for a, b in zip(tss, tss[1:])), "timestamps monotonic")
    _check(8 <= len(rows) <= 12, f"~10 frames recorded at 10fps/1s (got {len(rows)})")

    import cv2

    cap = cv2.VideoCapture(result.video_path)
    n = 0
    while True:
        ok, _ = cap.read()
        if not ok:
            break
        n += 1
    cap.release()
    _check(n == len(rows), f"mp4 frame count ({n}) == timestamps rows ({len(rows)})")
    print(f"  recorded {n} frames at {result.avg_fps:.1f} avg fps")


def test_cli(tmp: str) -> None:
    print("== recorder CLI ==")
    out = os.path.join(tmp, "cli")
    cmd = [sys.executable, "-m", "app.ml.datasets.recorder",
           "--match-id", "CLI_TEST", "--out", out, "--fps", "10",
           "--duration", "0.5", "--source", "test",
           "--width", "160", "--height", "90"]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=".",
                          timeout=120)
    _check(proc.returncode == 0, f"CLI exits 0 (stderr: {proc.stderr.strip()[-200:]})")
    _check(os.path.isfile(os.path.join(out, "match_CLI_TEST.mp4")),
           "CLI wrote match_CLI_TEST.mp4")
    _check(os.path.isfile(os.path.join(out, "timestamps.csv")),
           "CLI wrote timestamps.csv")


def main() -> int:
    print("AEGIS data-flywheel self-test (synthetic data only)")
    with tempfile.TemporaryDirectory(prefix="aegis_flywheel_") as tmp:
        test_labeler(tmp)
        test_labeler_cli(tmp)
        test_recorder(tmp)
        test_cli(tmp)
    print("\nALL SELF-TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
