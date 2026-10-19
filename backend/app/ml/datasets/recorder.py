# backend/app/ml/datasets/recorder.py
"""AEGIS AI - Match screen recorder (Phase 15, W1: data collection).

Records the observer screen to a timestamped mp4 during a match, plus a
``timestamps.csv`` with one row per frame (``frame_idx,epoch_ts``) so the
auto-labeler can join video frames with spotter/feed events by time.

Standalone by design: it uses plain ``cv2.VideoWriter`` and takes the
screen-capture source as an injected callable/iterator — it does NOT
import from ``backend/app/vision/``.

Typical use (on the observer PC, real match running)::

    python -m app.ml.datasets.recorder --match-id BMSD_GF_M3 --out ./recordings --fps 10

This writes ``./recordings/match_BMSD_GF_M3.mp4`` and
``./recordings/timestamps.csv``. Stop with Ctrl+C; partial recordings are
finalized cleanly (the writer is released and the CSV flushed).

Screen capture is pluggable:

- ``frame_source=None`` (default): uses :func:`iter_mss_frames`, which needs
  the free ``mss`` package (``pip install mss``) on the recording machine.
- ``frame_source="test"``: generates synthetic frames (smoke-test the
  pipeline with no screen at all).
- a zero-arg callable returning a BGR ``numpy`` frame (``uint8``, HxWx3),
  or ``None`` when the stream ends.
- any iterator/generator yielding BGR ``numpy`` frames.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, Optional, Union

import numpy as np
import structlog

logger = structlog.get_logger("aegis.ml.datasets.recorder")

# Anything the frame source may be: a thunk, an iterable, or a preset name.
FrameSource = Union[
    Callable[[], Optional[np.ndarray]],
    Iterable[np.ndarray],
    Iterator[np.ndarray],
    str,
    None,
]


def _require_cv2():
    """Import cv2 lazily so the module imports fine without OpenCV."""
    try:
        import cv2  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment issue
        raise RuntimeError(
            "OpenCV (cv2) is required to record or decode video. "
            "Install it with: pip install opencv-python-headless"
        ) from exc
    return cv2


def sanitize_match_id(match_id: str) -> str:
    """Make a match id safe for use in a filename."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", match_id.strip())
    return safe or "match"


def iter_mss_frames(monitor: int = 1) -> Iterator[np.ndarray]:
    """Yield BGR frames captured from the screen via ``mss`` (free, MIT).

    Lazily imported — only the recording machine needs ``mss`` installed.
    """
    try:
        import mss  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "The default screen-capture source needs the 'mss' package "
            "(pip install mss), or pass your own frame_source callable "
            "to record_match()."
        ) from exc
    with mss.mss() as sct:
        mon = sct.monitors[monitor]
        while True:
            shot = sct.grab(mon)
            # mss gives BGRA; OpenCV wants BGR.
            frame = np.asarray(shot, dtype=np.uint8)[:, :, :3].copy()
            yield frame


def iter_test_frames(
    width: int = 640, height: int = 360, fps: float = 10.0
) -> Iterator[np.ndarray]:
    """Yield synthetic frames — a moving bar + frame counter text area.

    Lets the user smoke-test the whole record → label → dataset loop with
    no screen, no match, no GPU. Frames are BGR uint8.
    """
    cv2 = _require_cv2()
    n = 0
    while True:
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        x = int((n * 7) % max(width - 60, 1))
        cv2.rectangle(frame, (x, height // 3), (x + 60, 2 * height // 3), (0, 255, 0), -1)
        # Encode the frame number in the top-left pixel block so a test can
        # verify that frame N in the file really is frame N.
        frame[0:8, 0:8] = (n % 256, (n * 3) % 256, (n * 7) % 256)
        yield frame
        n += 1


def _normalize_source(
    frame_source: FrameSource, width: int, height: int, fps: float
) -> Iterator[np.ndarray]:
    """Turn any accepted frame_source into a plain frame iterator."""
    if frame_source is None:
        return iter_mss_frames()
    if isinstance(frame_source, str):
        name = frame_source.strip().lower()
        if name == "test":
            return iter_test_frames(width=width, height=height, fps=fps)
        if name == "mss":
            return iter_mss_frames()
        raise ValueError(
            f"Unknown frame_source preset {frame_source!r}; "
            "use 'mss', 'test', a callable, or an iterator of frames."
        )
    if callable(frame_source):
        def _gen() -> Iterator[np.ndarray]:
            while True:
                frame = frame_source()
                if frame is None:
                    return
                yield frame

        return _gen()
    return iter(frame_source)


@dataclass
class RecordingResult:
    """Outcome of :func:`record_match`."""

    video_path: str
    timestamps_path: str
    frames_written: int
    duration_s: float
    avg_fps: float
    frame_size: tuple = (0, 0)
    stopped_early: bool = False
    stop_reason: str = ""


def record_match(
    match_id: str,
    out_dir: str,
    fps: float = 10.0,
    frame_source: FrameSource = None,
    duration: Optional[float] = None,
    frame_size: Optional[tuple] = None,
    fourcc: str = "mp4v",
) -> RecordingResult:
    """Record frames from ``frame_source`` to ``match_<id>.mp4``.

    Writes ``match_<id>.mp4`` and ``timestamps.csv`` (``frame_idx,epoch_ts``)
    into ``out_dir``. Recording stops when ``duration`` seconds elapse, the
    source is exhausted, or the user hits Ctrl+C — in all cases the video
    writer is released and the CSV flushed so partial recordings stay valid.

    The loop paces itself to ``fps`` using a monotonic clock; the *actual*
    epoch timestamp of each captured frame is what lands in the CSV, so a
    slow machine degrades gracefully instead of drifting.
    """
    cv2 = _require_cv2()
    if fps <= 0:
        raise ValueError("fps must be positive")
    if duration is not None and duration <= 0:
        raise ValueError("duration must be positive")

    os.makedirs(out_dir, exist_ok=True)
    safe_id = sanitize_match_id(match_id)
    video_path = os.path.join(out_dir, f"match_{safe_id}.mp4")
    ts_path = os.path.join(out_dir, "timestamps.csv")

    width, height = frame_size or (640, 360)
    source = _normalize_source(frame_source, width, height, fps)

    writer = None
    csv_file = None
    csv_writer = None
    frames_written = 0
    stopped_early = False
    stop_reason = "completed"
    t_start = time.monotonic()
    interval = 1.0 / fps

    try:
        csv_file = open(ts_path, "w", newline="", encoding="utf-8")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(["frame_idx", "epoch_ts"])

        for frame in source:
            if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
                raise ValueError(
                    "frame_source must yield BGR numpy arrays with shape (H, W, 3); "
                    f"got {type(frame)} with shape {getattr(frame, 'shape', None)}"
                )
            frame = np.ascontiguousarray(frame, dtype=np.uint8)
            h, w = frame.shape[:2]

            if writer is None:
                # First real frame fixes the video geometry.
                writer = cv2.VideoWriter(
                    video_path, cv2.VideoWriter_fourcc(*fourcc), fps, (w, h)
                )
                if not writer.isOpened():
                    raise RuntimeError(
                        f"cv2.VideoWriter could not open {video_path!r} "
                        f"(fourcc={fourcc!r}, size={(w, h)})."
                    )
                width, height = w, h
            elif (w, h) != (width, height):
                raise ValueError(
                    f"Frame size changed mid-recording: {(width, height)} -> {(w, h)}. "
                    "A recording must keep one fixed geometry."
                )

            epoch_ts = time.time()  # the timestamp that matters for labeling
            writer.write(frame)
            csv_writer.writerow([frames_written, f"{epoch_ts:.6f}"])
            frames_written += 1

            if frames_written % 100 == 0:
                csv_file.flush()

            if duration is not None and (time.monotonic() - t_start) >= duration:
                stop_reason = f"duration {duration}s reached"
                break

            # Pace to the target fps (monotonic, drift-free).
            next_due = t_start + frames_written * interval
            sleep_for = next_due - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)

    except KeyboardInterrupt:
        stopped_early = True
        stop_reason = "interrupted by user (Ctrl+C)"
        logger.info("recording interrupted; finalizing partial file")
    finally:
        if writer is not None:
            writer.release()
        if csv_file is not None:
            csv_file.flush()
            csv_file.close()

    if frames_written == 0:
        # Don't leave a bogus empty recording behind.
        for path in (video_path, ts_path):
            if os.path.exists(path):
                os.remove(path)
        raise RuntimeError("No frames were captured — nothing was recorded.")

    elapsed = time.monotonic() - t_start
    result = RecordingResult(
        video_path=video_path,
        timestamps_path=ts_path,
        frames_written=frames_written,
        duration_s=elapsed,
        avg_fps=frames_written / elapsed if elapsed > 0 else 0.0,
        frame_size=(width, height),
        stopped_early=stopped_early,
        stop_reason=stop_reason,
    )
    logger.info(
        "recording finished",
        match_id=match_id,
        frames=frames_written,
        seconds=round(elapsed, 1),
        avg_fps=round(result.avg_fps, 2),
        reason=stop_reason,
    )
    return result


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record the observer screen to match_<id>.mp4 + timestamps.csv"
    )
    parser.add_argument("--match-id", required=True, help="Match identifier")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--fps", type=float, default=10.0, help="Target fps (default 10)")
    parser.add_argument(
        "--duration", type=float, default=None, help="Stop after N seconds (default: run until Ctrl+C)"
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Frame source preset: 'mss' (screen, needs mss package) or 'test' "
        "(synthetic frames). Default: screen via mss.",
    )
    parser.add_argument("--width", type=int, default=640, help="Test-source width (default 640)")
    parser.add_argument("--height", type=int, default=360, help="Test-source height (default 360)")
    args = parser.parse_args(argv)

    try:
        result = record_match(
            match_id=args.match_id,
            out_dir=args.out,
            fps=args.fps,
            frame_source=args.source,
            duration=args.duration,
            frame_size=(args.width, args.height),
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {result.video_path} ({result.frames_written} frames, "
          f"{result.avg_fps:.1f} avg fps)")
    print(f"Wrote {result.timestamps_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
