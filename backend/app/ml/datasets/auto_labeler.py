# backend/app/ml/datasets/auto_labeler.py
"""AEGIS AI - Automatic dataset labeler (Phase 15, W1: data flywheel).

Joins a recorded match video (``timestamps.csv`` from
:mod:`app.ml.datasets.recorder`) with spotter/feed events
(``NormalizedEvent``-shaped JSONL: ``event_type, ts, player, victim, team,
weapon, ...``) by timestamp and emits a versioned labeled dataset::

    datasets/v1/
        frames/            # JPEGs sampled ±window seconds around each event
        manifest.csv       # frame_path, ts, label_type, killer, victim, ...
        dataset_card.md    # who collected, when, how many samples, label source
        stats.json         # run statistics (incl. skipped events + reasons)
        events.jsonl       # copy of the input events (reproducibility)
        timestamps.csv     # copy of the input timestamps (reproducibility)

THE core rule: labels come ONLY from real logged events. The labeler never
invents a label. Events with no matching video window are skipped and
counted (with the reason), never force-fit.

Events are parsed as plain JSON dicts in the ``NormalizedEvent`` shape on
purpose — this keeps the datasets package decoupled from the feed-adapter
service wiring (no DB engine, no singletons at import time).
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger("aegis.ml.datasets.auto_labeler")

#: Event types that become labels. Everything else in the vocabulary
#: (match_start, match_end, standings_update, commentary) is metadata, not
#: a visual label — those events are skipped with reason "non_label_event".
LABEL_EVENT_TYPES = {"kill", "knock", "team_wipe", "zone_update"}

#: Columns of manifest.csv, in order. The first nine are the documented
#: contract; the rest are provenance every row carries anyway.
MANIFEST_COLUMNS = [
    "frame_path",
    "ts",
    "label_type",
    "killer",
    "victim",
    "team",
    "weapon",
    "alive_count",
    "zone_phase",
    "event_id",
    "match_id",
    "frame_idx",
]


def _require_cv2():
    try:
        import cv2  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment issue
        raise RuntimeError(
            "OpenCV (cv2) is required to extract frames. "
            "Install it with: pip install opencv-python-headless"
        ) from exc
    return cv2


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------


def load_timestamps(path: str) -> List[Tuple[int, float]]:
    """Load ``timestamps.csv`` -> ``[(frame_idx, epoch_ts), ...]``.

    Raises ``ValueError`` if the file is corrupt (non-integer indices,
    non-monotonic timestamps): a broken clock means a broken dataset, so we
    fail loudly instead of misaligning labels.
    """
    rows: List[Tuple[int, float]] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != ["frame_idx", "epoch_ts"]:
            raise ValueError(
                f"{path}: expected header 'frame_idx,epoch_ts', "
                f"got {reader.fieldnames!r}"
            )
        for lineno, row in enumerate(reader, start=2):
            try:
                idx = int(row["frame_idx"])
                ts = float(row["epoch_ts"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path} line {lineno}: bad row {row!r}") from exc
            rows.append((idx, ts))
    if not rows:
        raise ValueError(f"{path}: no timestamp rows found")
    for (prev_idx, prev_ts), (idx, ts) in zip(rows, rows[1:]):
        if idx != prev_idx + 1:
            raise ValueError(
                f"{path}: frame_idx not sequential ({prev_idx} -> {idx}); "
                "re-record or repair the timestamps file"
            )
        if ts < prev_ts:
            raise ValueError(
                f"{path}: timestamps go backwards at frame {idx}; "
                "re-record or repair the timestamps file"
            )
    return rows


def load_events(path: str) -> Tuple[List[Dict[str, Any]], int]:
    """Load ``NormalizedEvent``-shaped JSONL -> ``(events, bad_lines)``.

    Malformed lines are counted (not silently dropped); events missing a
    numeric ``ts`` or ``event_type`` are counted as bad too.
    """
    events: List[Dict[str, Any]] = []
    bad = 0
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                logger.warning("unparseable event line", path=path, line=lineno)
                continue
            if not isinstance(obj, dict):
                bad += 1
                continue
            try:
                obj["ts"] = float(obj["ts"])
            except (KeyError, TypeError, ValueError):
                bad += 1
                logger.warning("event without numeric ts", path=path, line=lineno)
                continue
            if not obj.get("event_type"):
                bad += 1
                logger.warning("event without event_type", path=path, line=lineno)
                continue
            obj["event_type"] = str(obj["event_type"]).strip().lower()
            events.append(obj)
    events.sort(key=lambda e: e["ts"])
    return events, bad


# ---------------------------------------------------------------------------
# Labeling
# ---------------------------------------------------------------------------


@dataclass
class LabeledWindow:
    """One event joined to the video frames inside its time window."""

    event: Dict[str, Any]
    frame_indices: List[int]
    zone_phase: Optional[int] = None


@dataclass
class DatasetReport:
    """Everything the run produced, for logs and the dataset card."""

    dataset_dir: str
    version: int
    n_events: int
    n_bad_lines: int
    n_label_events: int
    n_labeled_events: int
    n_frames: int
    n_skipped: int
    skip_reasons: Dict[str, int] = field(default_factory=dict)
    label_counts: Dict[str, float] = field(default_factory=dict)
    video_path: str = ""
    window_s: float = 2.0


def _next_version(datasets_root: str) -> int:
    """Pick the next free ``vN`` under ``<root>/datasets/``."""
    base = os.path.join(datasets_root, "datasets")
    nxt = 1
    if os.path.isdir(base):
        for name in os.listdir(base):
            m = re.fullmatch(r"v(\d+)", name)
            if m and os.path.isdir(os.path.join(base, name)):
                nxt = max(nxt, int(m.group(1)) + 1)
    return nxt


def _window_frame_indices(
    event_ts: float,
    timestamps: List[Tuple[int, float]],
    window_s: float,
) -> List[int]:
    """Frame indices whose timestamp is within ±``window_s`` of the event."""
    return [idx for idx, ts in timestamps if abs(ts - event_ts) <= window_s]


def join_events_to_frames(
    events: List[Dict[str, Any]],
    timestamps: List[Tuple[int, float]],
    window_s: float = 2.0,
    ts_offset: float = 0.0,
) -> Tuple[List[LabeledWindow], Dict[str, int]]:
    """Join events to frame indices. Never invents labels.

    Returns ``(labeled_windows, skip_reasons)``. ``ts_offset`` shifts every
    event timestamp (seconds) before matching — use it when spotter taps
    systematically lag/lead the recorded video (e.g. spectator-mode delay
    on the observer phone vs the recording PC's clock).
    """
    skip_reasons: Dict[str, int] = {}
    labeled: List[LabeledWindow] = []
    zone_seq = 0

    def _skip(reason: str) -> None:
        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

    for event in events:
        etype = event["event_type"]
        if etype not in LABEL_EVENT_TYPES:
            _skip("non_label_event")
            continue
        zone_phase: Optional[int] = None
        if etype == "zone_update":
            zone_seq += 1
            payload = event.get("payload") or {}
            # Prefer an explicit phase from the logger; otherwise the phase
            # is simply "the Nth zone_update logged this match" — derived
            # from real logged events, not invented.
            try:
                zone_phase = int(payload.get("zone_phase", zone_seq))
            except (TypeError, ValueError):
                zone_phase = zone_seq

        frame_indices = _window_frame_indices(event["ts"] + ts_offset, timestamps, window_s)
        if not frame_indices:
            _skip("no_video_window")
            logger.warning(
                "event has no video window; skipped",
                event_type=etype,
                event_ts=event["ts"],
            )
            continue
        labeled.append(LabeledWindow(event=event, frame_indices=frame_indices, zone_phase=zone_phase))

    return labeled, skip_reasons


def _extract_frames(
    video_path: str, frame_indices: List[int], out_dir: str, jpeg_quality: int = 90
) -> Dict[int, str]:
    """Decode the requested frames -> ``{frame_idx: jpeg_path}``.

    Reads each index via random access (``CAP_PROP_POS_FRAMES``), so only
    labeled frames are ever decoded — the rest of the video is untouched.
    """
    cv2 = _require_cv2()
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")
    os.makedirs(out_dir, exist_ok=True)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    written: Dict[int, str] = {}
    try:
        for idx in sorted(set(frame_indices)):
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok or frame is None:
                raise RuntimeError(
                    f"Failed to decode frame {idx} from {video_path}; "
                    "the video may be truncated — re-record the match."
                )
            out_path = os.path.join(out_dir, f"frame_{idx:06d}.jpg")
            ok = cv2.imwrite(
                out_path, frame, [cv2.IMWRITE_JPEG_QUALITY, int(jpeg_quality)]
            )
            if not ok:
                raise RuntimeError(f"Failed to write JPEG: {out_path}")
            written[idx] = out_path
    finally:
        cap.release()
    return written


def _manifest_row(
    event: Dict[str, Any],
    frame_idx: int,
    frame_path: str,
    frame_ts: float,
    zone_phase: Optional[int],
) -> Dict[str, Any]:
    payload = event.get("payload") or {}

    def _payload_str(key: str) -> str:
        val = payload.get(key, "")
        return "" if val is None else str(val)

    return {
        "frame_path": frame_path,
        "ts": f"{frame_ts:.6f}",
        "label_type": event["event_type"],
        "killer": event.get("player") or "",
        "victim": event.get("victim") or "",
        "team": event.get("team") or "",
        "weapon": event.get("weapon") or "",
        "alive_count": _payload_str("alive_count"),
        "zone_phase": "" if zone_phase is None else str(zone_phase),
        "event_id": event.get("event_id") or "",
        "match_id": event.get("match_id") or "",
        "frame_idx": str(frame_idx),
    }


def _render_dataset_card(template_path: str, fields: Dict[str, str]) -> str:
    """Fill the dataset-card template with this run's real numbers."""
    if os.path.isfile(template_path):
        with open(template_path, encoding="utf-8") as fh:
            template = fh.read()
    else:  # pragma: no cover - template ships with the package
        template = "# Dataset {{version}}\n\n{{summary}}\n"
    card = template
    for key, value in fields.items():
        card = card.replace("{{" + key + "}}", value)
    leftover = sorted(set(re.findall(r"\{\{(\w+)\}\}", card)))
    if leftover:
        raise ValueError(
            f"dataset card template has unfilled placeholders: {leftover}"
        )
    return card


def build_dataset(
    video_path: str,
    timestamps_path: str,
    events_path: str,
    out_root: str,
    collector: str = "unknown",
    collection_notes: str = "",
    window_s: float = 2.0,
    ts_offset: float = 0.0,
    version: Optional[int] = None,
    jpeg_quality: int = 90,
) -> DatasetReport:
    """Build one versioned labeled dataset from a recording + event log.

    Steps: load + validate inputs → join events to frames by timestamp →
    extract labeled JPEGs → write ``manifest.csv`` → render
    ``dataset_card.md`` from the real run statistics → copy inputs for
    reproducibility. Returns a :class:`DatasetReport`.
    """
    timestamps = load_timestamps(timestamps_path)
    events, n_bad = load_events(events_path)
    labeled, skip_reasons = join_events_to_frames(
        events, timestamps, window_s=window_s, ts_offset=ts_offset
    )

    ver = version if version is not None else _next_version(out_root)
    dataset_dir = os.path.join(out_root, "datasets", f"v{ver}")
    frames_dir = os.path.join(dataset_dir, "frames")
    if os.path.exists(dataset_dir):
        raise FileExistsError(
            f"Dataset dir already exists: {dataset_dir} "
            "(pick a fresh out_root or an unused --version)"
        )
    os.makedirs(frames_dir, exist_ok=True)

    ts_by_idx = dict(timestamps)
    needed = [idx for w in labeled for idx in w.frame_indices]
    decoded = _extract_frames(video_path, needed, frames_dir, jpeg_quality=jpeg_quality)

    manifest_path = os.path.join(dataset_dir, "manifest.csv")
    n_frames = 0
    label_counts: Dict[str, float] = {}
    with open(manifest_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        for window in labeled:
            for idx in sorted(window.frame_indices):
                row = _manifest_row(
                    window.event, idx, decoded[idx], ts_by_idx[idx], window.zone_phase
                )
                # Store the path relative to the dataset dir: portable.
                row["frame_path"] = os.path.relpath(decoded[idx], dataset_dir)
                writer.writerow(row)
                n_frames += 1
            label_counts[window.event["event_type"]] = (
                label_counts.get(window.event["event_type"], 0) + 1
            )

    # Reproducibility: the exact inputs that produced this version.
    shutil.copy2(events_path, os.path.join(dataset_dir, "events.jsonl"))
    shutil.copy2(timestamps_path, os.path.join(dataset_dir, "timestamps.csv"))

    n_skipped = sum(skip_reasons.values())
    skip_lines = "\n".join(
        f"- {reason}: {count}" for reason, count in sorted(skip_reasons.items())
    ) or "- (none)"
    label_lines = "\n".join(
        f"- {label}: {int(count)} events" for label, count in sorted(label_counts.items())
    ) or "- (none)"

    # Gap report: honest signal about dropped frames in the recording.
    intervals = [b - a for (_, a), (_, b) in zip(timestamps, timestamps[1:])]
    gaps = 0
    if intervals:
        median = sorted(intervals)[len(intervals) // 2]
        gaps = sum(1 for dt in intervals if median > 0 and dt > 3 * median)

    card_fields = {
        "version": f"v{ver}",
        "collector": collector,
        "created": _utcnow_iso(),
        "match_id": next((e.get("match_id") or "" for e in events if e.get("match_id")), ""),
        "n_samples": str(n_frames),
        "n_events": str(len(events)),
        "n_labeled_events": str(len(labeled)),
        "n_skipped": str(n_skipped),
        "video": os.path.basename(video_path),
        "window_s": str(window_s),
        "ts_offset": str(ts_offset),
        "label_breakdown": label_lines,
        "skip_breakdown": skip_lines,
        "label_source": (
            "Human spotter / feed-operator event log (NormalizedEvent-shaped JSONL). "
            "Every label in manifest.csv comes from a real logged event joined to "
            "video frames by timestamp; no labels were synthesized."
        ),
        "notes": collection_notes or "(none)",
        "gaps": str(gaps),
    }
    template_path = os.path.join(os.path.dirname(__file__), "dataset_card.template.md")
    card = _render_dataset_card(template_path, card_fields)
    with open(os.path.join(dataset_dir, "dataset_card.md"), "w", encoding="utf-8") as fh:
        fh.write(card)

    stats = {
        "version": ver,
        "collector": collector,
        "created_utc": card_fields["created"],
        "video": os.path.basename(video_path),
        "window_s": window_s,
        "ts_offset": ts_offset,
        "n_events": len(events),
        "n_bad_lines": n_bad,
        "n_label_events": sum(1 for e in events if e["event_type"] in LABEL_EVENT_TYPES),
        "n_labeled_events": len(labeled),
        "n_frames": n_frames,
        "n_skipped": n_skipped,
        "skip_reasons": skip_reasons,
        "label_counts": {k: int(v) for k, v in label_counts.items()},
        "timestamp_gaps_over_3x_median": gaps,
        "n_timestamp_rows": len(timestamps),
    }
    with open(os.path.join(dataset_dir, "stats.json"), "w", encoding="utf-8") as fh:
        json.dump(stats, fh, indent=2)

    report = DatasetReport(
        dataset_dir=dataset_dir,
        version=ver,
        n_events=len(events),
        n_bad_lines=n_bad,
        n_label_events=stats["n_label_events"],
        n_labeled_events=len(labeled),
        n_frames=n_frames,
        n_skipped=n_skipped,
        skip_reasons=skip_reasons,
        label_counts=label_counts,
        video_path=video_path,
        window_s=window_s,
    )
    logger.info(
        "dataset built",
        dataset_dir=dataset_dir,
        frames=n_frames,
        labeled_events=len(labeled),
        skipped=n_skipped,
    )
    return report


def _utcnow_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def main(argv: Optional[list] = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Join a match recording with its event log -> versioned labeled dataset"
    )
    parser.add_argument("--video", required=True, help="Recorded match_*.mp4")
    parser.add_argument("--timestamps", required=True, help="timestamps.csv from the recorder")
    parser.add_argument("--events", required=True, help="NormalizedEvent-shaped events JSONL")
    parser.add_argument("--out", required=True, help="Output root (datasets/vN/ created inside)")
    parser.add_argument("--collector", default="unknown", help="Who collected this match")
    parser.add_argument("--notes", default="", help="Collection notes for the dataset card")
    parser.add_argument("--window", type=float, default=2.0,
                        help="±seconds around each event to sample (default 2.0)")
    parser.add_argument("--ts-offset", type=float, default=0.0,
                        help="Shift event timestamps by N seconds before joining (default 0)")
    parser.add_argument("--version", type=int, default=None,
                        help="Force dataset version (default: next free vN)")
    args = parser.parse_args(argv)

    try:
        report = build_dataset(
            video_path=args.video,
            timestamps_path=args.timestamps,
            events_path=args.events,
            out_root=args.out,
            collector=args.collector,
            collection_notes=args.notes,
            window_s=args.window,
            ts_offset=args.ts_offset,
            version=args.version,
        )
    except (RuntimeError, ValueError, FileNotFoundError, FileExistsError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Dataset v{report.version} -> {report.dataset_dir}")
    print(f"  {report.n_frames} frames from {report.n_labeled_events} labeled events "
          f"({report.n_skipped} skipped: {report.skip_reasons or 'none'})")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main())
