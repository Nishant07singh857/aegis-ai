"""StreamService — lifecycle owner for the AEGIS AI automated stream.

Pipeline (all binaries must exist on the host / streamer container):

    Xvfb  →  virtual display (e.g. :99 @ 1280x720)
    Chromium (headed, under Xvfb — headed renders the full compositor stack
              reliably; headless modes are not used here) pointed at the
              director page URL
    FFmpeg x11grab → libx264 → tee muxer → N RTMP destinations

AUDIO: the stream carries real audio only when a real source exists.
``build_ffmpeg_command`` accepts optional audio inputs (commentary PCM
FIFO from app/audio/commentary_feed.py, game audio via
``AEGIS_GAME_AUDIO_INPUT``) and mixes them with amix + AAC encode. With
no inputs the encoder gets ``-an`` — video-only, honestly — rather than
a faked silent track. Master mute is applied by not passing inputs.

CPU expectations (software x264, veryfast preset, 30 fps):
  * 1280x720@30  — a modest 2–4 vCPU box is enough; this is the default.
  * 1920x1080@30 — budget 6–8 vCPUs; encoding latency becomes the risk.
    Use the fastest preset your CPU allows (``veryfast`` → ``superfast`` →
    ``ultrafast``) or a hardware encoder build of FFmpeg (out of scope here).
The config exposes width/height/fps/preset so operators can trade quality
for CPU deliberately.

Crash handling: a monitor task watches the FFmpeg process. On unexpected
exit it restarts the pipeline with exponential backoff, up to
``max_restarts`` times, then transitions to FAILED and records
``last_error``. Stopping is graceful (terminate → wait → kill → wait) and
never leaks zombie processes: every spawned child is reaped on stop,
on failed start, and on monitor shutdown.

Stream keys: handled in :mod:`app.streaming.destinations`; keys are read
from env at call time and are NEVER logged, stored, or returned here.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import destinations
from .destinations import build_destination_urls, key_present

logger = logging.getLogger("aegis.streaming")

CHROMIUM_CANDIDATES = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")

#: Resolutions the API accepts (name → (width, height)).
RESOLUTIONS = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
}

DEFAULT_DIRECTOR_URL = "http://frontend:3000/broadcast/output?bg=brand"


class StreamState(str, enum.Enum):
    IDLE = "idle"           # never started (or fully torn down)
    STARTING = "starting"   # spawning Xvfb → Chromium → FFmpeg
    RUNNING = "running"     # pipeline up; monitor task watching
    STOPPING = "stopping"   # graceful teardown in progress
    STOPPED = "stopped"     # cleanly stopped; may be started again
    FAILED = "failed"       # unrecoverable: restart budget exhausted or fatal error


@dataclass
class StreamConfig:
    """Operator-tunable knobs for the streaming pipeline."""

    director_url: str = DEFAULT_DIRECTOR_URL
    resolution: str = "720p"            # one of RESOLUTIONS
    fps: int = 30
    video_bitrate: str = "4500k"       # 720p default; raise for 1080p
    x264_preset: str = "veryfast"      # ultrafast/superfast/veryfast/... — faster = less CPU
    xvfb_display: str = ":99"
    max_restarts: int = 5              # bounded crash restarts, then FAILED
    restart_backoff_base: float = 2.0  # seconds; attempt n waits base * 2**(n-1)
    monitor_interval: float = 1.0      # seconds between ffmpeg liveness polls
    stop_timeout: float = 10.0         # graceful terminate wait before SIGKILL

    @property
    def width(self) -> int:
        return RESOLUTIONS[self.resolution][0]

    @property
    def height(self) -> int:
        return RESOLUTIONS[self.resolution][1]


class StreamService:
    """Owns the FFmpeg streaming subprocess lifecycle.

    Exactly one logical stream exists; the module-level singleton
    (see :func:`get_stream_service`) is shared by the API router.
    """

    def __init__(self, config: Optional[StreamConfig] = None) -> None:
        self.config: StreamConfig = config or StreamConfig()
        self._state: StreamState = StreamState.IDLE
        self._lock = asyncio.Lock()
        self._xvfb: Optional[subprocess.Popen] = None
        self._chromium: Optional[subprocess.Popen] = None
        self._ffmpeg: Optional[subprocess.Popen] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._started_at: Optional[datetime] = None
        self._destinations: List[str] = []            # requested destination names
        self._custom_rtmp_url: Optional[str] = None
        self._restart_count: int = 0
        self._last_error: Optional[str] = None
        self._chromium_binary: Optional[str] = None

    # ------------------------------------------------------------------ #
    # introspection
    # ------------------------------------------------------------------ #

    @property
    def state(self) -> StreamState:
        return self._state

    def status(self) -> Dict:
        """Snapshot of stream state. NEVER includes stream keys — only the
        ``*_configured`` booleans below."""
        now = datetime.now(timezone.utc)
        uptime = (now - self._started_at).total_seconds() if self._started_at else 0.0
        ffmpeg = self._ffmpeg
        ffmpeg_alive = ffmpeg is not None and ffmpeg.poll() is None
        return {
            "state": self._state.value,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "uptime_seconds": round(uptime, 1),
            "destinations_requested": list(self._destinations),
            "destinations_configured": {
                "youtube": key_present(destinations.YOUTUBE_KEY_ENV),
                "twitch": key_present(destinations.TWITCH_KEY_ENV),
                "facebook": key_present(destinations.FACEBOOK_KEY_ENV),
                "custom": bool(self._custom_rtmp_url),
            },
            "resolution": self.config.resolution,
            "fps": self.config.fps,
            "director_url": self.config.director_url,
            "ffmpeg_pid": ffmpeg.pid if ffmpeg else None,
            "ffmpeg_alive": ffmpeg_alive,
            "restart_count": self._restart_count,
            "last_error": self._last_error,
        }

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #

    async def start(
        self,
        destinations_list: List[str],
        custom_rtmp_url: Optional[str] = None,
        resolution: str = "720p",
    ) -> Dict:
        """Start streaming to the given destinations.

        Raises:
            RuntimeError: when already running/starting, or when a required
                binary (ffmpeg, Xvfb, chromium) is missing — naming the binary.
            ValueError: when destinations/keys are invalid (missing stream key,
                unknown destination, bad custom URL).
        """
        async with self._lock:
            if self._state in (StreamState.STARTING, StreamState.RUNNING):
                raise RuntimeError("Stream is already running.")
            if self._state == StreamState.STOPPING:
                raise RuntimeError("Stream is stopping; try again once it is stopped.")

            # Validate destinations + keys BEFORE spawning anything, so a
            # missing key is a clean 400-class error, not a crashed pipeline.
            resolved = build_destination_urls(destinations_list, custom_rtmp_url)
            if resolution not in RESOLUTIONS:
                raise ValueError(
                    f'Unknown resolution "{resolution}". '
                    f"Supported: {', '.join(RESOLUTIONS)}."
                )
            self.config.resolution = resolution
            self._chromium_binary = self._find_chromium()  # raises RuntimeError naming it
            self._require_binary("ffmpeg")
            self._require_binary("Xvfb")

            self._state = StreamState.STARTING
            self._last_error = None
            self._restart_count = 0
            self._destinations = list(destinations_list)
            self._custom_rtmp_url = custom_rtmp_url

            try:
                self._launch_pipeline(resolved)
            except Exception as exc:  # noqa: BLE001 — convert to clean FAILED state
                self._cleanup_processes()
                self._state = StreamState.FAILED
                self._last_error = str(exc)
                logger.error("Stream failed to start: %s", exc)
                raise

            self._started_at = datetime.now(timezone.utc)
            self._state = StreamState.RUNNING
            self._monitor_task = asyncio.create_task(self._monitor())
            logger.info(
                "Stream started: %s → %s",
                self.config.resolution,
                ", ".join(self._destinations),
            )
            return self.status()

    async def stop(self) -> Dict:
        """Stop the stream gracefully; reaps every child process (no zombies)."""
        async with self._lock:
            if self._state in (StreamState.IDLE, StreamState.STOPPED, StreamState.FAILED):
                return self.status()  # idempotent no-op
            self._state = StreamState.STOPPING

            if self._monitor_task is not None:
                self._monitor_task.cancel()
                try:
                    await self._monitor_task
                except asyncio.CancelledError:
                    pass
                self._monitor_task = None

            self._cleanup_processes()
            self._started_at = None
            self._state = StreamState.STOPPED
            logger.info("Stream stopped.")
            return self.status()

    # ------------------------------------------------------------------ #
    # pipeline construction
    # ------------------------------------------------------------------ #

    @staticmethod
    def _require_binary(name: str) -> str:
        path = shutil.which(name)
        if not path:
            raise RuntimeError(
                f'Cannot start stream: required binary "{name}" was not found on PATH. '
                "Install it (e.g. apt install ffmpeg / xvfb) or run the streamer service image."
            )
        return path

    @classmethod
    def _find_chromium(cls) -> str:
        for candidate in CHROMIUM_CANDIDATES:
            path = shutil.which(candidate)
            if path:
                return path
        raise RuntimeError(
            "Cannot start stream: no Chromium/Chrome binary found on PATH "
            f"(looked for: {', '.join(CHROMIUM_CANDIDATES)}). "
            "Install chromium or run the streamer service image."
        )

    @staticmethod
    def _resolve_audio_inputs() -> Tuple[List[Dict[str, Any]], float]:
        """Resolve real audio inputs for the FFmpeg command.

        Returns (audio_inputs, game_volume). Sources:
          * commentary: the commentary PCM FIFO — included ONLY while the
            commentary feed is running (opening a reader on a FIFO with no
            writer would block the stream start forever).
          * game: ``AEGIS_GAME_AUDIO_INPUT`` env, format ``<ffmpeg_format>:<device>``
            e.g. ``alsa:hw:1,0`` (3.5mm cable route) or
            ``file:/path/game_audio.mkv`` (scrcpy --record file).
        Master mute drops every input; game volume comes from the mixer.
        """
        import os as _os

        inputs: List[Dict[str, Any]] = []
        game_volume = 1.0
        try:
            from app.audio.commentary_feed import get_commentary_feed
            from app.audio.mixer import get_audio_mixer

            mixer = get_audio_mixer()
            if mixer.master_mute:
                return [], 0.0
            game_volume = mixer.game_volume
            feed = get_commentary_feed()
            if feed.is_running():
                inputs.append(
                    {
                        "kind": "commentary",
                        "input_args": [
                            "-f", "s16le", "-ar", "48000", "-ac", "2",
                            "-i", str(feed.fifo_path),
                        ],
                    }
                )
            game_spec = _os.environ.get("AEGIS_GAME_AUDIO_INPUT", "").strip()
            if game_spec and ":" in game_spec:
                fmt, device = game_spec.split(":", 1)
                inputs.append(
                    {"kind": "game", "input_args": ["-f", fmt, "-i", device]}
                )
        except Exception as exc:  # audio is optional: never break the stream
            logger.warning("audio input resolution failed; streaming video-only", error=str(exc))
            return [], 1.0
        return inputs, game_volume

    def _launch_pipeline(self, resolved: List[Tuple[str, str]]) -> None:
        """Spawn Xvfb → Chromium → FFmpeg. Caller must hold the lock."""
        cfg = self.config
        display = cfg.xvfb_display
        size = f"{cfg.width}x{cfg.height}"

        xvfb_cmd = ["Xvfb", display, "-screen", "0", f"{size}x24"]
        self._xvfb = subprocess.Popen(xvfb_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        chromium_cmd = [
            self._chromium_binary,
            f"--display={display}",
            f"--window-size={cfg.width},{cfg.height}",
            "--kiosk",
            "--no-first-run",
            "--disable-infobars",
            "--hide-scrollbars",
            "--disable-gpu",
            "--no-sandbox",
            cfg.director_url,
        ]
        self._chromium = subprocess.Popen(
            chromium_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        audio_inputs, game_volume = self._resolve_audio_inputs()
        ffmpeg_cmd = self.build_ffmpeg_command(
            display=display,
            width=cfg.width,
            height=cfg.height,
            fps=cfg.fps,
            video_bitrate=cfg.video_bitrate,
            x264_preset=cfg.x264_preset,
            urls=[url for _, url in resolved],
            audio_inputs=audio_inputs,
            game_volume=game_volume,
        )
        # NOTE: URLs contain stream keys; the command list is passed to
        # Popen directly and is never logged or returned anywhere.
        self._ffmpeg = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ------------------------------------------------------------------ #
    # crash monitor
    # ------------------------------------------------------------------ #

    async def _monitor(self) -> None:
        """Watch FFmpeg; restart the pipeline on crash with bounded retries."""
        cfg = self.config
        try:
            while self._state == StreamState.RUNNING:
                await asyncio.sleep(cfg.monitor_interval)
                proc = self._ffmpeg
                if proc is None:
                    continue
                rc = proc.poll()
                if rc is not None:
                    await self._handle_crash(rc)
        except asyncio.CancelledError:
            pass

    async def _handle_crash(self, returncode: int) -> None:
        """One crash observed: restart with backoff, or fail after budget."""
        if self._restart_count >= self.config.max_restarts:
            async with self._lock:
                self._state = StreamState.FAILED
                self._last_error = (
                    f"FFmpeg exited (code {returncode}) and the restart budget "
                    f"({self.config.max_restarts} attempts) is exhausted. "
                    "Check encoder load and destination reachability, then restart manually."
                )
                self._cleanup_processes()
                logger.error("Stream FAILED: %s", self._last_error)
            return

        self._restart_count += 1
        backoff = self.config.restart_backoff_base * (2 ** (self._restart_count - 1))
        logger.warning(
            "FFmpeg exited (code %s); restart attempt %d/%d in %.1fs.",
            returncode,
            self._restart_count,
            self.config.max_restarts,
            backoff,
        )
        await asyncio.sleep(backoff)
        if self._state != StreamState.RUNNING:
            return  # stopped while backing off
        async with self._lock:
            if self._state != StreamState.RUNNING:
                return
            try:
                resolved = build_destination_urls(self._destinations, self._custom_rtmp_url)
                self._cleanup_processes()
                self._launch_pipeline(resolved)
            except Exception as exc:  # noqa: BLE001
                self._state = StreamState.FAILED
                self._last_error = f"Stream restart failed: {exc}"
                logger.error("Stream FAILED on restart: %s", exc)

    # ------------------------------------------------------------------ #
    # teardown (zombie-proof)
    # ------------------------------------------------------------------ #

    def _cleanup_processes(self) -> None:
        """Terminate → wait → kill → wait on every child; always reaps."""
        for name in ("_ffmpeg", "_chromium", "_xvfb"):
            proc = getattr(self, name)
            setattr(self, name, None)
            if proc is None:
                continue
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=self.config.stop_timeout)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=self.config.stop_timeout)
                else:
                    proc.wait()  # already exited — still reap it
            except Exception as exc:  # noqa: BLE001 — teardown must not raise
                logger.warning("Error cleaning up %s (pid=%s): %s", name, getattr(proc, "pid", "?"), exc)

    # ------------------------------------------------------------------ #
    # command builders (pure; unit-testable without binaries)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _escape_tee_url(url: str) -> str:
        """Escape ``:`` and ``|`` for FFmpeg's tee-muxer option syntax."""
        return url.replace("\\", "\\\\").replace(":", "\\:").replace("|", "\\|")

    @classmethod
    def build_ffmpeg_command(
        cls,
        *,
        display: str,
        width: int,
        height: int,
        fps: int,
        video_bitrate: str,
        x264_preset: str,
        urls: List[str],
        audio_inputs: Optional[List[Dict[str, Any]]] = None,
        game_volume: float = 1.0,
    ) -> List[str]:
        """Build the FFmpeg argv capturing the Xvfb display and pushing to N RTMP URLs.

        ``audio_inputs``: optional list of ``{"input_args": [...], "kind":
        "commentary"|"game"}``. Each ``input_args`` is appended verbatim as
        one ``-i`` input (e.g. ``["-f", "s16le", "-ar", "48000", "-ac",
        "2", "-i", fifo_path]``). Inputs are mixed with ``amix``; the game
        channel gets the ``game_volume`` gain (the commentary channel is
        already gained in software by the commentary feed, and master mute
        is applied by simply not passing inputs).

        With no audio inputs the encoder gets ``-an`` — video-only, honestly,
        exactly as before. We encode what exists rather than faking a track.
        """
        if not urls:
            raise ValueError("At least one destination URL is required.")
        audio_inputs = [a for a in (audio_inputs or []) if a.get("input_args")]
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "x11grab",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            str(fps),
            "-i",
            display,
        ]
        for spec in audio_inputs:
            cmd += spec["input_args"]
        if not audio_inputs:
            cmd += ["-an"]  # video-only: no audio source exists
        else:
            # Audio input N (1-based after the video input) -> labelled stream.
            mix_labels = []
            for idx, spec in enumerate(audio_inputs, start=1):
                kind = spec.get("kind", "commentary")
                vol = game_volume if kind == "game" else 1.0
                label = f"a{idx}"
                mix_labels.append((f"[{idx}:a]", label, vol))
            if len(mix_labels) == 1:
                (src, label, vol) = mix_labels[0]
                filtergraph = f"{src}volume={vol:.3f}[aout]"
            else:
                parts = []
                mixed = []
                for src, label, vol in mix_labels:
                    parts.append(f"{src}volume={vol:.3f}[{label}]")
                    mixed.append(f"[{label}]")
                parts.append(f"{''.join(mixed)}amix=inputs={len(mixed)}:normalize=0[aout]")
                filtergraph = ";".join(parts)
            cmd += [
                "-filter_complex", filtergraph,
                "-map", "0:v",
                "-map", "[aout]",
                "-c:a", "aac",
                "-b:a", "128k",
                "-ar", "48000",
            ]
        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            x264_preset,
            "-pix_fmt",
            "yuv420p",
            "-b:v",
            video_bitrate,
            "-g",
            str(fps * 2),  # ~2s keyframe interval for RTMP players
        ]
        if len(urls) == 1:
            cmd += ["-f", "flv", urls[0]]
        else:
            segments = "|".join(f"[f=flv]{cls._escape_tee_url(u)}" for u in urls)
            cmd += ["-f", "tee", segments]
        return cmd


_stream_service: Optional[StreamService] = None


def get_stream_service() -> StreamService:
    """Module-level singleton shared by the API router."""
    global _stream_service
    if _stream_service is None:
        _stream_service = StreamService()
    return _stream_service
