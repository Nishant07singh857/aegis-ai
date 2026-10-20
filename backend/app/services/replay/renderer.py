# backend/app/services/replay/renderer.py
"""Highlight VIDEO renderer (Phase 21 / T4).

Turns a scored highlight window into REAL video files by cutting a
recorded match video with FFmpeg:

  * ``<moment_id>_16x9.mp4``  — landscape cut (1280x720, pad-preserved)
  * ``<moment_id>_9x16.mp4``  — vertical cut (720x1280, scale + pad)
  * ``<moment_id>_preview.gif`` — first 3 s of the window as a GIF

HONESTY CONTRACT (same as the rest of the replay system):
  * If no source video exists for the match, the render is recorded as
    ``no_source`` and NO file is created. Nothing is ever synthesised —
    there is no fallback "generated" clip.
  * If FFmpeg fails or the window cannot be mapped onto the source
    timeline, the render is ``failed`` with the reason kept on the
    record, and partial outputs are deleted.

SOURCE VIDEO DISCOVERY — where match video lives on disk:
  The system has no video DVR (see service.py). Match video reaches
  disk only as operator recordings (scrcpy --record / OBS) dropped into
  ``settings.RECORDINGS_DIR`` (``backend/data/recordings/``). The
  renderer resolves a source in this order:

    1. an explicit ``source_path`` argument (operator-supplied),
    2. ``$AEGIS_HIGHLIGHT_SOURCE_DIR`` / RECORDINGS_DIR files whose
       filename contains the ``match_id``,
    3. a sidecar ``<video>.json`` next to a candidate file may also
       declare ``{"match_id": ...}``.

TIMELINE MAPPING — highlight windows are epoch seconds (replay buffer
timestamps). Cutting needs (a) the recording's start epoch, resolved
from a ``<video>.json`` sidecar (``started_at_epoch`` /
``start_time_epoch`` / ``recorded_at_epoch``), an explicit
``source_started_at`` argument, or the container's ``creation_time``
tag; or (b) when the window start is smaller than the source duration,
the window is already source-relative and is used directly. If neither
holds, the render fails honestly instead of guessing an offset.

RENDER STATUS is persisted in a sidecar registry file
(``HIGHLIGHTS_DIR/render_status.json``) keyed by moment_id — the live
highlight schema is in-memory, and the DB ``Highlight`` model carries
its own ``video_path``/``meta`` for persisted rows; this registry is the
status surface for buffer highlights. Statuses: ``pending`` (render
running / requested), ``rendered``, ``no_source``, ``failed``. A moment
that was never requested reports ``not_requested``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger("aegis.replay.renderer")

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".ts", ".m4v"}

#: Output kinds produced per successful render.
RENDER_KINDS = ("landscape", "portrait", "gif")

_GIF_SECONDS = 3.0


def _default_highlights_dir() -> Path:
    try:
        from app.config import settings

        return Path(settings.HIGHLIGHTS_DIR)
    except Exception:  # pragma: no cover - config import fallback
        return Path(__file__).resolve().parents[2] / "data" / "highlights"


def _default_recordings_dir() -> Path:
    env = os.environ.get("AEGIS_HIGHLIGHT_SOURCE_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    try:
        from app.config import settings

        return Path(settings.RECORDINGS_DIR)
    except Exception:  # pragma: no cover - config import fallback
        return Path(__file__).resolve().parents[2] / "data" / "recordings"


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def probe(path: Path) -> Optional[Dict[str, Any]]:
    """ffprobe a media file. Returns {duration, width, height} or None."""
    if not ffmpeg_available() or not path.exists():
        return None
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries",
                "stream=width,height:format=duration:format_tags=creation_time",
                "-of", "json",
                str(path),
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout.decode(errors="replace") or "{}")
    except ValueError:
        return None
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    tags = fmt.get("tags") or {}
    out: Dict[str, Any] = {
        "duration": float(fmt["duration"]) if fmt.get("duration") else None,
        "width": streams[0].get("width") if streams else None,
        "height": streams[0].get("height") if streams else None,
        "creation_time": tags.get("creation_time"),
    }
    return out


def _sidecar(path: Path) -> Dict[str, Any]:
    """Optional ``<video>.json`` metadata sitting next to a recording."""
    side = path.with_suffix(path.suffix + ".json")
    if not side.exists():
        side = path.with_suffix(".json")
    if not side.exists():
        return {}
    try:
        data = json.loads(side.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def find_source_video(
    match_id: Optional[str],
    recordings_dir: Path,
    source_path: Optional[str] = None,
) -> Optional[Path]:
    """Resolve the recorded source video for a match. None = no source."""
    if source_path:
        p = Path(source_path).expanduser()
        return p if p.exists() and p.is_file() else None
    if not recordings_dir.exists():
        return None
    candidates = sorted(
        p for p in recordings_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )
    if not candidates:
        return None
    if match_id:
        for p in candidates:
            if match_id in p.name:
                return p
        for p in candidates:
            if str(_sidecar(p).get("match_id") or "") == match_id:
                return p
        return None  # recordings exist, but none belongs to this match
    # No match scoping: only an unambiguous single recording may be used.
    return candidates[0] if len(candidates) == 1 else None


def _source_start_epoch(source: Path) -> Optional[float]:
    """Epoch at which the recording started, if it can be known."""
    meta = _sidecar(source)
    for key in ("started_at_epoch", "start_time_epoch", "recorded_at_epoch"):
        value = meta.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    info = probe(source)
    if info and info.get("creation_time"):
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(
                str(info["creation_time"]).replace("Z", "+00:00")
            )
            return dt.timestamp()
        except ValueError:
            return None
    return None


@dataclass
class RenderRecord:
    moment_id: str
    match_id: Optional[str] = None
    status: str = "not_requested"  # not_requested|pending|rendered|no_source|failed
    source_path: Optional[str] = None
    files: Dict[str, str] = field(default_factory=dict)  # kind -> filename
    error: Optional[str] = None
    window_start: Optional[float] = None
    window_end: Optional[float] = None
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "moment_id": self.moment_id,
            "match_id": self.match_id,
            "status": self.status,
            "source_path": self.source_path,
            "files": dict(self.files),
            "error": self.error,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RenderRecord":
        return cls(
            moment_id=str(data["moment_id"]),
            match_id=data.get("match_id"),
            status=str(data.get("status") or "not_requested"),
            source_path=data.get("source_path"),
            files=dict(data.get("files") or {}),
            error=data.get("error"),
            window_start=data.get("window_start"),
            window_end=data.get("window_end"),
            updated_at=float(data.get("updated_at") or time.time()),
        )


class HighlightRenderer:
    """FFmpeg-backed renderer + persistent (sidecar JSON) status registry."""

    def __init__(
        self,
        highlights_dir: Optional[Path] = None,
        recordings_dir: Optional[Path] = None,
    ) -> None:
        self.highlights_dir = Path(highlights_dir) if highlights_dir else _default_highlights_dir()
        self.recordings_dir = Path(recordings_dir) if recordings_dir else _default_recordings_dir()
        self.highlights_dir.mkdir(parents=True, exist_ok=True)
        self._registry_file = self.highlights_dir / "render_status.json"
        self._lock = threading.Lock()
        self._records: Dict[str, RenderRecord] = {}
        self._load()

    # ── registry persistence ───────────────────────────────────

    def _load(self) -> None:
        try:
            if self._registry_file.exists():
                data = json.loads(self._registry_file.read_text())
                if isinstance(data, dict):
                    for key, value in data.items():
                        if isinstance(value, dict):
                            value.setdefault("moment_id", key)
                            self._records[key] = RenderRecord.from_dict(value)
        except (OSError, ValueError) as exc:
            logger.warning("render registry unreadable; starting empty", error=str(exc))
            self._records = {}

    def _save(self) -> None:
        payload = {k: v.to_dict() for k, v in self._records.items()}
        tmp = self._registry_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self._registry_file)

    def _set(self, record: RenderRecord) -> RenderRecord:
        record.updated_at = time.time()
        with self._lock:
            self._records[record.moment_id] = record
            self._save()
        return record

    # ── reads ──────────────────────────────────────────────────

    def get_record(self, moment_id: str) -> RenderRecord:
        with self._lock:
            record = self._records.get(moment_id)
        if record is not None:
            return record
        return RenderRecord(moment_id=moment_id, status="not_requested")

    def list_records(self) -> List[RenderRecord]:
        with self._lock:
            return sorted(
                self._records.values(), key=lambda r: r.updated_at, reverse=True
            )

    def file_path(self, moment_id: str, kind: str) -> Optional[Path]:
        record = self.get_record(moment_id)
        name = record.files.get(kind)
        if not name:
            return None
        path = self.highlights_dir / name
        return path if path.exists() else None

    # ── rendering ──────────────────────────────────────────────

    def render(
        self,
        moment_id: str,
        window_start: float,
        window_end: float,
        match_id: Optional[str] = None,
        source_path: Optional[str] = None,
        source_started_at: Optional[float] = None,
        pre_roll: float = 2.0,
        post_roll: float = 3.0,
    ) -> RenderRecord:
        """Cut one highlight window into MP4/MP4/GIF. Never fabricates.

        ``window_start``/``window_end`` are epoch seconds (replay buffer
        convention); see the module docstring for timeline mapping.
        ``pre_roll``/``post_roll`` widen the cut for broadcast context.
        """
        record = RenderRecord(
            moment_id=moment_id, match_id=match_id, status="pending",
            window_start=window_start, window_end=window_end,
        )
        self._set(record)

        if not ffmpeg_available():
            record.status = "failed"
            record.error = "ffmpeg/ffprobe not found on this host; cannot render video."
            return self._set(record)

        source = find_source_video(match_id, self.recordings_dir, source_path)
        if source is None:
            record.status = "no_source"
            record.error = (
                "No recorded source video for this match in "
                f"{self.recordings_dir}. Highlights stay event-only until "
                "an operator recording exists — no clip is fabricated."
            )
            logger.info("highlight render: no source", moment_id=moment_id, match_id=match_id)
            return self._set(record)
        record.source_path = str(source)

        info = probe(source)
        if info is None or not info.get("duration"):
            record.status = "failed"
            record.error = f"Source video {source.name} could not be probed by ffprobe."
            return self._set(record)
        duration_src = float(info["duration"])

        # Timeline mapping: epoch window -> offset into the recording.
        start_epoch = (
            source_started_at
            if source_started_at is not None
            else _source_start_epoch(source)
        )
        if start_epoch is not None:
            offset = window_start - start_epoch
        elif window_end <= duration_src:
            offset = window_start  # already source-relative
        else:
            record.status = "failed"
            record.error = (
                "Cannot map the highlight window onto the recording: the "
                "window is epoch-based but the recording start time is "
                "unknown (no sidecar started_at_epoch, no creation_time "
                "tag, no explicit source_started_at). Refusing to guess."
            )
            return self._set(record)

        clip_start = max(0.0, offset - max(0.0, pre_roll))
        clip_end = (window_end - (start_epoch or 0.0)) if start_epoch is not None else window_end
        clip_end += max(0.0, post_roll)
        if clip_end <= clip_start or clip_start >= duration_src:
            record.status = "failed"
            record.error = (
                f"Highlight window maps outside the recording "
                f"(offset {offset:.1f}s, source duration {duration_src:.1f}s)."
            )
            return self._set(record)
        clip_end = min(clip_end, duration_src)
        clip_dur = clip_end - clip_start

        outputs = {
            "landscape": self.highlights_dir / f"{moment_id}_16x9.mp4",
            "portrait": self.highlights_dir / f"{moment_id}_9x16.mp4",
            "gif": self.highlights_dir / f"{moment_id}_preview.gif",
        }
        # Phase 22 (W3, Tech): encoder selection — NVENC only when a
        # GPU + NVENC FFmpeg are really present, else libx264. NVENC
        # takes -b:v instead of -crf, so quality args differ per codec.
        from app.streaming.encoder import detect_encoder

        _enc = detect_encoder()["encoder"]
        if _enc == "h264_nvenc":
            _enc_args = ["-c:v", _enc, "-preset", "p4", "-b:v", "6M"]
        else:
            _enc_args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
        commands = {
            "landscape": [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{clip_start:.3f}", "-i", str(source),
                "-t", f"{clip_dur:.3f}",
                "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
                       "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1",
                *_enc_args,
                "-c:a", "aac", "-movflags", "+faststart",
                str(outputs["landscape"]),
            ],
            "portrait": [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{clip_start:.3f}", "-i", str(source),
                "-t", f"{clip_dur:.3f}",
                "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,"
                       "pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1",
                *_enc_args,
                "-c:a", "aac", "-movflags", "+faststart",
                str(outputs["portrait"]),
            ],
            "gif": [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{clip_start:.3f}", "-i", str(source),
                "-t", f"{min(_GIF_SECONDS, clip_dur):.3f}",
                "-vf", "fps=12,scale=480:-2:flags=lanczos",
                "-an", str(outputs["gif"]),
            ],
        }

        try:
            for kind in RENDER_KINDS:
                proc = subprocess.run(
                    commands[kind], stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, timeout=600,
                )
                if proc.returncode != 0 or not outputs[kind].exists():
                    raise RuntimeError(
                        f"ffmpeg {kind} cut failed: "
                        + proc.stderr.decode(errors="replace")[:300]
                    )
        except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
            for path in outputs.values():
                path.unlink(missing_ok=True)
            record.status = "failed"
            record.error = str(exc)[:500]
            logger.warning("highlight render failed", moment_id=moment_id, error=record.error)
            return self._set(record)

        record.files = {kind: path.name for kind, path in outputs.items()}
        record.status = "rendered"
        record.error = None
        logger.info(
            "highlight rendered", moment_id=moment_id, match_id=match_id,
            clip_start=round(clip_start, 2), clip_dur=round(clip_dur, 2),
        )
        return self._set(record)


#: Process-wide singleton used by the replay service / API router.
highlight_renderer = HighlightRenderer()
