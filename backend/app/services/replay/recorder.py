# backend/app/services/replay/recorder.py
"""Continuous ISO recording of observer feeds (Phase 25, P0-2).

WHY THIS EXISTS
---------------
Until Phase 25 AEGIS had NO video DVR (see ``service.py``): an operator
might drop a scrcpy/OBS recording into ``settings.RECORDINGS_DIR`` and
the highlight renderer could cut it, but nothing recorded the 16
observer feeds continuously. Real replay craft (research doc §2.3) is
the opposite: ISO-record every feed continuously, mark moments, offer
replays, take them in lulls. This module is the recording half.

TAP POINT (the truthful one)
----------------------------
Frames already flow through exactly one place: ``FrameGrabber``
(``app/vision/capture/frame_grabber.py``) — every capture backend
(adb_poll, scrcpy, RTMP, file, screen) appends ``CapturedFrame`` objects
(timestamped with ``time.time()`` epoch at grab time) to its per-source
``frame_buffers``. The production frame provider built by
:func:`build_iso_recorder_from_manager` reads the latest frame per
observer source through the SAME surface the video router's program
feed uses internally (``FrameGrabber.get_latest_frame(source_id)`` on
the ``MultiDeviceCaptureManager``'s grabber) and de-duplicates by
``frame_id`` so polling never double-records a frame. No new capture
path is created and no frame is ever synthesised: when a source has no
frame, the provider returns ``None`` and the feed records NOTHING.

SEGMENTS + SIDECARS (renderer contract)
---------------------------------------
Each feed writes rolling segments::

    RECORDINGS_DIR/
      iso_slot-1_1735689600_0000.mp4
      iso_slot-1_1735689600_0000.mp4.json   <- sidecar
      iso_slot-1_1735689630_0001.mp4
      ...

A segment is one FFmpeg process fed raw BGR frames on stdin
(libx264, explicit bitrate — see storage math below). The sidecar is
written when the segment OPENS and finalised when it CLOSES, and it is
the contract the highlight renderer already honours
(``renderer._source_start_epoch`` reads ``started_at_epoch``):

    {"feed_id": "slot-1", "source_id": "aegis-observer-slot-01",
     "segment_file": "iso_slot-1_....mp4",
     "started_at_epoch": <epoch of the FIRST frame in the segment>,
     "ended_at_epoch": <epoch of the LAST frame, null while open>,
     "frame_count": N, "fps": 10, "width": W, "height": H,
     "bitrate": "2500k", "open": true|false}

``started_at_epoch`` is the first frame's own capture timestamp — the
same epoch clock the replay buffer timestamps events with — never the
file's mtime and never an estimate. That is what lets the renderer map
an epoch highlight window onto the segment without guessing an offset.

STORAGE — BOUNDED, WITH THE MATH WRITTEN DOWN
---------------------------------------------
Default encode: 2500 kbit/s per feed, video only, no audio.

  * per feed:  2.5 Mbit/s = 0.3125 MB/s = 1.125 GB/hour
  * 16 feeds: 40 Mbit/s   = 5 MB/s     = 18.0 GB/hour
  * one ~30-minute match, all 16 feeds: ~9 GB
  * a 5-match day (~2.5 h of play): ~45 GB

For comparison, recording 1080p at the renderer's 6 Mbit/s NVENC
preset would cost 2.7 GB/hour/feed — 43.2 GB/hour for the fleet,
~21.6 GB per match. The default total cap is therefore 20 GB
(~2 matches of headroom); it is configurable, and enforcement is a
rolling delete: when the fleet total exceeds the cap, the OLDEST
closed segments (by truthful ``started_at_epoch``) are deleted first.
The currently-open segment of a feed is never deleted under it; if the
cap is smaller than the open segments alone, feeds honestly flip to
``disk_full_paused`` instead of pretending to record.

PER-FEED HONEST STATE
---------------------
``recording``       frames are flowing into an open segment
``no_source``       running, but this feed has produced no frame
                    (yet / any more) — nothing is being written
``disk_full_paused`` cap/ENOSPC stopped this feed; others may continue
``stopped``         recorder (or this feed) is not running

A feed dropping pauses ONLY that feed: every feed owns its FFmpeg
process, its segment counter and its state.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger("aegis.replay.recorder")

#: Per-feed honest states (exposed verbatim via status / API).
STATE_RECORDING = "recording"
STATE_NO_SOURCE = "no_source"
STATE_DISK_FULL_PAUSED = "disk_full_paused"
STATE_STOPPED = "stopped"

DEFAULT_SEGMENT_SECONDS = 30.0
DEFAULT_TOTAL_CAP_BYTES = 20 * 1024**3  # 20 GiB — see storage math above
DEFAULT_FPS = 10
DEFAULT_BITRATE = "2500k"

#: Async/sync frame provider: feed_id -> CapturedFrame-like object
#: (``.image`` ndarray + ``.timestamp`` epoch + ``.frame_id``) or a
#: ``(image, timestamp)`` tuple, or None when the feed has no frame.
FrameProvider = Callable[[str], Any]


def _default_recordings_dir() -> Path:
    try:
        from app.config import settings

        return Path(settings.RECORDINGS_DIR)
    except Exception:  # pragma: no cover - config import fallback
        return Path(__file__).resolve().parents[2] / "data" / "recordings"


def storage_math(bitrate_kbps: int = 2500, feeds: int = 16) -> Dict[str, float]:
    """Fleet storage cost at a per-feed bitrate (documented, computed)."""
    per_feed_gb_h = bitrate_kbps * 1000 * 3600 / 8 / 1e9
    return {
        "bitrate_kbps_per_feed": float(bitrate_kbps),
        "feeds": float(feeds),
        "gb_per_hour_per_feed": round(per_feed_gb_h, 3),
        "gb_per_hour_fleet": round(per_feed_gb_h * feeds, 2),
        "gb_per_30min_match_fleet": round(per_feed_gb_h * feeds * 0.5, 2),
    }


@dataclass
class _Segment:
    path: Path
    started_at_epoch: float
    frame_count: int = 0
    last_frame_ts: Optional[float] = None
    width: int = 0
    height: int = 0
    is_open: bool = True

    @property
    def sidecar_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".json")

    @property
    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0


@dataclass
class _FeedState:
    feed_id: str
    source_id: Optional[str] = None
    state: str = STATE_STOPPED
    segments: List[_Segment] = field(default_factory=list)
    segment_index: int = 0
    proc: Optional[subprocess.Popen] = field(default=None, repr=False)
    current: Optional[_Segment] = None
    last_frame_id: Any = None
    last_frame_at: Optional[float] = None  # wall time a frame last arrived
    frames_written: int = 0
    last_error: Optional[str] = None


class ISORecorder:
    """Rolling per-feed ISO recorder writing into RECORDINGS_DIR.

    Synchronous core (``start``/``write_frame``/``stop``) so tests and
    the async polling loop share one code path. The async loop
    (:meth:`run`) merely polls the injected frame provider and feeds
    :meth:`write_frame`; it adds no behaviour of its own.
    """

    def __init__(
        self,
        recordings_dir: Optional[Path] = None,
        segment_seconds: float = DEFAULT_SEGMENT_SECONDS,
        total_cap_bytes: int = DEFAULT_TOTAL_CAP_BYTES,
        fps: int = DEFAULT_FPS,
        bitrate: str = DEFAULT_BITRATE,
        frame_provider: Optional[FrameProvider] = None,
        no_source_after_s: float = 5.0,
    ) -> None:
        if segment_seconds <= 0:
            raise ValueError("segment_seconds must be positive")
        if total_cap_bytes <= 0:
            raise ValueError("total_cap_bytes must be positive")
        self.recordings_dir = Path(recordings_dir) if recordings_dir else _default_recordings_dir()
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        self.segment_seconds = float(segment_seconds)
        self.total_cap_bytes = int(total_cap_bytes)
        self.fps = int(fps)
        self.bitrate = bitrate
        self.frame_provider = frame_provider
        self.no_source_after_s = float(no_source_after_s)
        self._feeds: Dict[str, _FeedState] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._adopt_existing_segments()

    # ── adoption (restart honesty) ─────────────────────────────

    def _adopt_existing_segments(self) -> None:
        """Index segments already on disk so caps/lookups stay truthful
        across a process restart. Open-at-crash segments are treated as
        closed with their sidecar's last known frame time."""
        for sidecar in sorted(self.recordings_dir.glob("iso_*.mp4.json")):
            try:
                data = json.loads(sidecar.read_text())
                video = self.recordings_dir / str(data.get("segment_file") or "")
                started = float(data.get("started_at_epoch"))
            except (OSError, ValueError, TypeError):
                continue
            if not video.exists():
                continue
            feed_id = str(data.get("feed_id") or "unknown")
            feed = self._feeds.setdefault(feed_id, _FeedState(feed_id=feed_id))
            seg = _Segment(
                path=video,
                started_at_epoch=started,
                frame_count=int(data.get("frame_count") or 0),
                last_frame_ts=data.get("ended_at_epoch"),
                width=int(data.get("width") or 0),
                height=int(data.get("height") or 0),
                is_open=False,
            )
            feed.segments.append(seg)
            feed.segment_index = max(feed.segment_index, len(feed.segments))

    # ── lifecycle ──────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._running

    def register_feed(self, feed_id: str, source_id: Optional[str] = None) -> None:
        feed = self._feeds.setdefault(feed_id, _FeedState(feed_id=feed_id))
        if source_id:
            feed.source_id = source_id

    def start(self, feed_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """Start recording. Feeds begin in ``no_source`` — honestly,
        nothing is written until a real frame arrives for that feed."""
        self._running = True
        ids = feed_ids or list(self._feeds.keys())
        for feed_id in ids:
            self.register_feed(feed_id)
            feed = self._feeds[feed_id]
            if feed.state == STATE_STOPPED:
                feed.state = STATE_NO_SOURCE
        logger.info("iso_recorder_started", feeds=ids or "provider-driven")
        return self.status()

    def stop(self, feed_id: Optional[str] = None) -> Dict[str, Any]:
        """Stop one feed, or the whole recorder when feed_id is None."""
        targets = [self._feeds[feed_id]] if feed_id and feed_id in self._feeds else (
            list(self._feeds.values()) if feed_id is None else []
        )
        for feed in targets:
            self._close_segment(feed, ended_at=feed.current.last_frame_ts if feed.current else None)
            feed.state = STATE_STOPPED
        if feed_id is None:
            self._running = False
        return self.status()

    async def start_async(self, feed_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """Start + spawn the provider polling loop (lifespan wiring)."""
        result = self.start(feed_ids)
        if self.frame_provider is not None and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self.run())
        return result

    async def stop_async(self) -> Dict[str, Any]:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        return self.stop()

    async def run(self) -> None:
        """Poll the frame provider at ``fps`` and record new frames.

        De-duplicates by frame_id/timestamp: the grabber's latest-frame
        surface returns the same frame until a new one is captured, and
        re-encoding the same frame would fabricate motion that never
        happened at that rate — duplicates are skipped, so the recorded
        frame rate is the feed's REAL rate (<= target fps).
        """
        interval = 1.0 / max(1, self.fps)
        while self._running:
            try:
                if self.frame_provider is not None:
                    for feed_id in list(self._feeds.keys()):
                        try:
                            frame = self.frame_provider(feed_id)
                        except Exception as exc:  # one bad feed must not kill the loop
                            logger.warning("iso_frame_provider_failed", feed=feed_id, error=str(exc)[:200])
                            continue
                        if frame is None:
                            self._note_no_frame(feed_id)
                            continue
                        image, ts, fid = _unpack_frame(frame)
                        if image is None:
                            self._note_no_frame(feed_id)
                            continue
                        feed = self._feeds[feed_id]
                        marker = fid if fid is not None else ts
                        if marker is not None and marker == feed.last_frame_id:
                            continue  # same frame re-polled — not new footage
                        feed.last_frame_id = marker
                        self.write_frame(feed_id, image, ts=ts)
                self._sweep_no_source()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # pragma: no cover - loop must survive
                logger.warning("iso_recorder_loop_error", error=str(exc)[:200])
            await asyncio.sleep(interval)

    def _note_no_frame(self, feed_id: str) -> None:
        feed = self._feeds.get(feed_id)
        if feed is not None and feed.state == STATE_RECORDING:
            # A gap starts: state flips in _sweep_no_source once the gap
            # is older than no_source_after_s; the segment stays open so
            # a brief cable bump doesn't fragment the recording.
            pass

    def _sweep_no_source(self) -> None:
        now = time.time()
        for feed in self._feeds.values():
            if feed.state != STATE_RECORDING or feed.last_frame_at is None:
                continue
            if now - feed.last_frame_at > self.no_source_after_s:
                self._close_segment(feed, ended_at=feed.current.last_frame_ts if feed.current else None)
                feed.state = STATE_NO_SOURCE
                logger.info("iso_feed_no_source", feed=feed.feed_id)

    # ── frame intake (the tap) ─────────────────────────────────

    def write_frame(self, feed_id: str, image: Any, ts: Optional[float] = None) -> bool:
        """Record one real frame. Returns False (and writes nothing)
        when the recorder is stopped / disk-paused, or the frame is
        empty. ``ts`` is the frame's capture epoch — the same clock the
        replay buffer uses; defaults to now only for direct callers
        that genuinely captured the frame at call time."""
        if not self._running:
            return False
        self.register_feed(feed_id)
        feed = self._feeds[feed_id]
        if feed.state in (STATE_STOPPED, STATE_DISK_FULL_PAUSED):
            return False
        if image is None or getattr(image, "size", 0) == 0:
            return False
        ts = float(ts) if ts is not None else time.time()
        try:
            if feed.current is None:
                self._start_segment(feed, image, ts)
            elif ts - feed.current.started_at_epoch >= self.segment_seconds:
                self._close_segment(feed, ended_at=feed.current.last_frame_ts)
                self._start_segment(feed, image, ts)
            elif (image.shape[1], image.shape[0]) != (feed.current.width, feed.current.height):
                # Resolution change mid-segment would corrupt the file;
                # rotate honestly instead of rescaling silently.
                self._close_segment(feed, ended_at=feed.current.last_frame_ts)
                self._start_segment(feed, image, ts)
            assert feed.proc is not None and feed.proc.stdin is not None
            feed.proc.stdin.write(image.tobytes())
            feed.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            return self._on_write_failure(feed, exc)
        feed.current.frame_count += 1
        feed.current.last_frame_ts = ts
        feed.frames_written += 1
        feed.last_frame_at = time.time()
        feed.state = STATE_RECORDING
        return True

    def _on_write_failure(self, feed: _FeedState, exc: BaseException) -> bool:
        feed.last_error = str(exc)[:300]
        self._close_segment(feed, ended_at=feed.current.last_frame_ts if feed.current else None, proc_dead=True)
        if getattr(exc, "errno", None) == 28 or "no space" in str(exc).lower():
            feed.state = STATE_DISK_FULL_PAUSED
            logger.error("iso_feed_disk_full", feed=feed.feed_id)
        else:
            feed.state = STATE_NO_SOURCE
            logger.warning("iso_feed_write_failed", feed=feed.feed_id, error=feed.last_error)
        return False

    # ── segment management ─────────────────────────────────────

    def _start_segment(self, feed: _FeedState, image: Any, ts: float) -> None:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg not found on this host; cannot ISO-record.")
        height, width = int(image.shape[0]), int(image.shape[1])
        stamp = int(ts)
        name = f"iso_{feed.feed_id}_{stamp}_{feed.segment_index:04d}.mp4"
        feed.segment_index += 1
        path = self.recordings_dir / name
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}", "-r", str(self.fps),
            "-i", "-",
            "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v", "libx264", "-preset", "veryfast",
            "-b:v", self.bitrate, "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(path),
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        seg = _Segment(path=path, started_at_epoch=float(ts), width=width, height=height)
        feed.proc = proc
        feed.current = seg
        feed.segments.append(seg)
        self._write_sidecar(feed, seg)
        logger.info("iso_segment_opened", feed=feed.feed_id, file=name, started_at_epoch=seg.started_at_epoch)

    def _close_segment(self, feed: _FeedState, ended_at: Optional[float], proc_dead: bool = False) -> None:
        seg = feed.current
        proc = feed.proc
        feed.current = None
        feed.proc = None
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            if not proc_dead:
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            else:
                try:
                    proc.kill()
                except OSError:
                    pass
        if seg is not None:
            seg.is_open = False
            if ended_at is None:
                ended_at = seg.last_frame_ts
            self._write_sidecar(feed, seg, ended_at=ended_at)
            logger.info(
                "iso_segment_closed", feed=feed.feed_id, file=seg.path.name,
                frames=seg.frame_count, size_bytes=seg.size_bytes,
            )
            self.enforce_cap()

    def _write_sidecar(self, feed: _FeedState, seg: _Segment, ended_at: Optional[float] = None) -> None:
        data = {
            "feed_id": feed.feed_id,
            "source_id": feed.source_id,
            "segment_file": seg.path.name,
            "started_at_epoch": seg.started_at_epoch,
            "ended_at_epoch": ended_at if ended_at is not None else seg.last_frame_ts,
            "frame_count": seg.frame_count,
            "fps": self.fps,
            "width": seg.width,
            "height": seg.height,
            "bitrate": self.bitrate,
            "open": seg.is_open,
        }
        try:
            seg.sidecar_path.write_text(json.dumps(data, indent=2))
        except OSError as exc:
            logger.warning("iso_sidecar_write_failed", file=str(seg.sidecar_path), error=str(exc)[:200])

    # ── storage cap ────────────────────────────────────────────

    def total_bytes(self) -> int:
        return sum(seg.size_bytes for feed in self._feeds.values() for seg in feed.segments)

    def enforce_cap(self) -> Dict[str, Any]:
        """Delete oldest CLOSED segments until the fleet is under cap.

        Open segments are never deleted. If open segments alone exceed
        the cap, affected feeds are marked ``disk_full_paused`` —
        honestly paused, not silently dropping footage.
        """
        deleted: List[str] = []
        total = self.total_bytes()
        if total <= self.total_cap_bytes:
            return {"deleted": deleted, "total_bytes": total, "cap_bytes": self.total_cap_bytes}
        closed = sorted(
            (seg for feed in self._feeds.values() for seg in feed.segments if not seg.is_open),
            key=lambda s: s.started_at_epoch,
        )
        for seg in closed:
            if total <= self.total_cap_bytes:
                break
            size = seg.size_bytes
            for feed in self._feeds.values():
                if seg in feed.segments:
                    feed.segments.remove(seg)
                    break
            seg.path.unlink(missing_ok=True)
            seg.sidecar_path.unlink(missing_ok=True)
            total -= size
            deleted.append(seg.path.name)
        if total > self.total_cap_bytes:
            for feed in self._feeds.values():
                if feed.current is not None:
                    feed.state = STATE_DISK_FULL_PAUSED
            logger.error("iso_cap_exceeded_by_open_segments", total_bytes=total, cap_bytes=self.total_cap_bytes)
        if deleted:
            logger.info("iso_cap_enforced", deleted=deleted, total_bytes=total)
        return {"deleted": deleted, "total_bytes": total, "cap_bytes": self.total_cap_bytes}

    # ── lookups (renderer hand-off) ────────────────────────────

    def list_segments(self, feed_id: Optional[str] = None) -> List[Dict[str, Any]]:
        feeds = [self._feeds[feed_id]] if feed_id and feed_id in self._feeds else list(self._feeds.values())
        out = []
        for feed in feeds:
            for seg in feed.segments:
                out.append({
                    "feed_id": feed.feed_id,
                    "file": seg.path.name,
                    "path": str(seg.path),
                    "started_at_epoch": seg.started_at_epoch,
                    "ended_at_epoch": seg.last_frame_ts,
                    "frame_count": seg.frame_count,
                    "size_bytes": seg.size_bytes,
                    "open": seg.is_open,
                })
        return sorted(out, key=lambda s: s["started_at_epoch"])

    def find_recording_for_window(
        self, feed_id: Optional[str], window_start: float, window_end: float
    ) -> Optional[Tuple[Path, float]]:
        """Segment (path, started_at_epoch) covering a highlight window.

        Prefers a CLOSED segment of the requested feed that contains
        ``window_start``; falls back to any feed's closed segment
        containing it (a moment's feed is not always attributable), then
        to the segment with the largest overlap. Open segments are
        excluded — FFmpeg has not finalised them, so cutting one would
        be guessing. Returns None when no recording covers the window:
        the caller must then fall back to an honest event replay.
        """
        candidates: List[_Segment] = []
        ordered_feeds = []
        if feed_id and feed_id in self._feeds:
            ordered_feeds.append(self._feeds[feed_id])
        ordered_feeds.extend(f for f in self._feeds.values() if f.feed_id != feed_id)
        for feed in ordered_feeds:
            for seg in feed.segments:
                if seg.is_open or seg.last_frame_ts is None:
                    continue
                if seg.started_at_epoch <= window_start <= seg.last_frame_ts:
                    return seg.path, seg.started_at_epoch
                if seg.started_at_epoch <= window_end and window_start <= seg.last_frame_ts:
                    candidates.append(seg)
        if candidates:
            best = max(candidates, key=lambda s: min(s.last_frame_ts or s.started_at_epoch, window_end) - max(s.started_at_epoch, window_start))
            return best.path, best.started_at_epoch
        return None

    # ── status ─────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        feeds: Dict[str, Any] = {}
        for feed_id, feed in self._feeds.items():
            feeds[feed_id] = {
                "feed_id": feed_id,
                "source_id": feed.source_id,
                "state": feed.state if self._running else (STATE_STOPPED if feed.state != STATE_DISK_FULL_PAUSED else feed.state),
                "frames_written": feed.frames_written,
                "segments": len(feed.segments),
                "open_segment": feed.current.path.name if feed.current else None,
                "last_frame_at": feed.last_frame_at,
                "last_error": feed.last_error,
            }
        try:
            kbps = int(str(self.bitrate).rstrip("kK"))
        except ValueError:
            kbps = 2500
        return {
            "running": self._running,
            "recordings_dir": str(self.recordings_dir),
            "segment_seconds": self.segment_seconds,
            "total_cap_bytes": self.total_cap_bytes,
            "total_bytes": self.total_bytes(),
            "fps": self.fps,
            "bitrate": self.bitrate,
            "storage_math": storage_math(kbps, feeds=max(1, len(self._feeds))),
            "feeds": feeds,
        }


def _unpack_frame(frame: Any) -> Tuple[Any, Optional[float], Any]:
    """Normalise a provider result to (image, timestamp, frame_id)."""
    if isinstance(frame, tuple) and len(frame) >= 2:
        return frame[0], float(frame[1]), None
    image = getattr(frame, "image", None)
    ts = getattr(frame, "timestamp", None)
    fid = getattr(frame, "frame_id", None)
    return image, (float(ts) if ts is not None else None), fid


# ── builders for main.py lifespan wiring (coordinator calls these) ──


def build_iso_recorder(
    recordings_dir: Optional[Path] = None,
    frame_provider: Optional[FrameProvider] = None,
    **kwargs: Any,
) -> ISORecorder:
    """Plain builder: recorder over an explicit frame provider."""
    return ISORecorder(recordings_dir=recordings_dir, frame_provider=frame_provider, **kwargs)


def build_iso_recorder_from_manager(
    manager: Any,
    recordings_dir: Optional[Path] = None,
    max_slots: int = 16,
    **kwargs: Any,
) -> ISORecorder:
    """Builder wired to the live capture manager (the real tap point).

    Feed ids are ``slot-1``..``slot-16``; each maps to the manager's
    grabber source ``aegis-observer-slot-NN`` — the same latest-frame
    surface the program feed is encoded from. Degrades honestly: a
    manager without a grabber yields a provider that returns None for
    every feed (all feeds report ``no_source``, nothing is recorded).
    """
    grabber = getattr(manager, "_grabber", None)

    def _source_id(slot: int) -> str:
        return f"aegis-observer-slot-{slot:02d}"

    def provider(feed_id: str) -> Any:
        if grabber is None:
            return None
        try:
            slot = int(str(feed_id).rsplit("-", 1)[-1])
        except ValueError:
            return None
        return grabber.get_latest_frame(_source_id(slot))

    recorder = ISORecorder(recordings_dir=recordings_dir, frame_provider=provider, **kwargs)
    for slot in range(1, max_slots + 1):
        recorder.register_feed(f"slot-{slot}", source_id=_source_id(slot))
    return recorder
