"""AEGIS AI — commentary audio feed.

Commentary text (from the commentary agents / director) -> Piper TTS ->
WAV clips on disk -> a continuous 48 kHz stereo PCM stream.

Two outputs, both real:
  1. FILE:  every synthesized line is saved as
     ``<audio_dir>/commentary/clip_<seq>_<ts>.wav``. Anything (FFmpeg,
     a player, the director UI) can read these directly.
  2. STREAM: a background writer thread emits uninterrupted PCM
     (s16le, 48000 Hz, stereo) to a FIFO at
     ``<audio_dir>/pipes/commentary.pcm`` — silence when the queue is
     empty, clip audio when a line is queued. FFmpeg consumes it with::

         -f s16le -ar 48000 -ac 2 -i <audio_dir>/pipes/commentary.pcm

     The writer opens the FIFO O_RDWR|O_NONBLOCK (the standard trick so
     the open never blocks waiting for a reader): if nothing is reading
     yet, the kernel pipe buffer fills and further audio is dropped
     (EAGAIN) with a warn-once log — never buffered forever. The
     director's commentary volume / master mute from AudioMixer are
     applied as software gain to the PCM samples.

Honest limits are documented in docs/AUDIO.md. Nothing here invents
audio: when no TTS engine is configured, enqueue_line() returns
``synthesized=False`` and nothing is written to the feed.
"""

from __future__ import annotations

import array
import asyncio
import errno
import os
import shutil
import struct
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

import structlog

from .mixer import default_audio_dir, get_audio_mixer

logger = structlog.get_logger("aegis.audio.commentary_feed")

SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 2  # s16le
CHUNK_SECONDS = 0.1
CHUNK_BYTES = int(SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH * CHUNK_SECONDS)


@dataclass
class ClipRecord:
    clip_id: str
    text: str
    wav_path: str
    duration_seconds: float
    profile_id: str
    created_at: float = field(default_factory=time.time)


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _wav_to_pcm48_stereo(wav_path: Path) -> Optional[bytes]:
    """Convert any WAV to raw s16le / 48 kHz / stereo via ffmpeg. Real bytes."""
    if not _ffmpeg_available():
        logger.error("ffmpeg not found: cannot convert clip to feed PCM")
        return None
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-y", "-i", str(wav_path),
                "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.error("clip conversion failed", error=str(exc), clip=str(wav_path))
        return None
    if proc.returncode != 0 or not proc.stdout:
        logger.error(
            "ffmpeg clip conversion failed",
            rc=proc.returncode,
            stderr=proc.stderr.decode(errors="replace")[:300],
        )
        return None
    return proc.stdout


def _apply_gain(pcm: bytes, gain: float) -> bytes:
    """Software gain on s16le samples. gain=1.0 is a pass-through copy."""
    if gain == 1.0:
        return pcm
    samples = array.array("h")
    samples.frombytes(pcm)
    if len(samples) % 2:
        samples = samples[:-1]
    if gain == 0.0:
        return b"\x00" * len(samples) * 2
    out = array.array("h", (max(-32768, min(32767, int(s * gain))) for s in samples))
    return out.tobytes()


class CommentaryAudioFeed:
    """Queue of commentary lines -> WAV clips -> continuous PCM FIFO."""

    def __init__(self, audio_dir: Path | None = None) -> None:
        self.audio_dir = audio_dir or default_audio_dir()
        self.clips_dir = self.audio_dir / "commentary"
        self.pipes_dir = self.audio_dir / "pipes"
        self.clips_dir.mkdir(parents=True, exist_ok=True)
        self.pipes_dir.mkdir(parents=True, exist_ok=True)
        self.fifo_path = self.pipes_dir / "commentary.pcm"

        self._synth = None  # lazy VoiceSynthesizer
        self._synth_lock = threading.Lock()
        self._queue: Deque[bytes] = deque()  # raw PCM chunks awaiting write
        self._queue_lock = threading.Lock()
        self._clips: Deque[ClipRecord] = deque(maxlen=50)
        self._seq = 0
        self._seq_lock = threading.Lock()

        self._writer_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._no_reader_warned = False
        self._log = logger.bind(component="commentary_feed")

    # ------------------------------------------------------------------ #
    # TTS engine (lazy)
    # ------------------------------------------------------------------ #
    def _get_synth(self):
        with self._synth_lock:
            if self._synth is None:
                from app.agents.base_agent import AgentConfig
                from app.agents.commentary_agents.voice_synthesizer import (
                    VoiceSynthesizer,
                )
                from app.core.event_bus import EventBus

                self._synth = VoiceSynthesizer(
                    AgentConfig(agent_id="commentary_audio_feed"), EventBus()
                )
                self._log.info("voice synthesizer attached to audio feed")
            return self._synth

    def tts_status(self) -> Dict[str, Any]:
        try:
            stats = self._get_synth().get_stats()
        except Exception as exc:  # engine import/config failure: say so
            return {"synthesis_enabled": False, "tts_engine": "error", "error": str(exc)}
        return {
            "synthesis_enabled": stats["synthesis_enabled"],
            "tts_engine": stats["tts_engine"],
            "active_profile": stats["active_profile"],
            "total_synthesized": stats["total_synthesized"],
            "avg_latency_ms": stats["avg_latency_ms"],
        }

    # ------------------------------------------------------------------ #
    # enqueue
    # ------------------------------------------------------------------ #
    async def enqueue_line(
        self,
        text: str,
        profile_id: Optional[str] = None,
        style: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Synthesize one commentary line, save the WAV, queue PCM for the feed."""
        text = (text or "").strip()
        if not text:
            return {"queued": False, "synthesized": False, "reason": "empty text"}
        synth = self._get_synth()
        kwargs: Dict[str, Any] = {}
        if style:
            from app.agents.commentary_agents.voice_synthesizer import VoiceStyle

            try:
                kwargs["style"] = VoiceStyle(style)
            except ValueError:
                self._log.warning("unknown voice style, using default", style=style)

        audio = await synth.synthesize(text, profile_id=profile_id, **kwargs)
        if audio is None or not audio.synthesized or not audio.audio_data:
            self._log.warning("line not synthesized: no TTS engine or failure")
            return {
                "queued": False,
                "synthesized": False,
                "reason": (
                    "No TTS engine configured (set AEGIS_TTS_ENGINE=piper and "
                    "AEGIS_TTS_MODEL_PATH) or synthesis failed. See docs/AUDIO.md."
                ),
            }

        with self._seq_lock:
            self._seq += 1
            seq = self._seq
        clip_id = f"clip_{seq:04d}_{int(time.time())}"
        wav_path = self.clips_dir / f"{clip_id}.wav"
        wav_path.write_bytes(audio.audio_data)

        pcm = await asyncio.to_thread(_wav_to_pcm48_stereo, wav_path)
        if pcm:
            with self._queue_lock:
                # split into writer-sized chunks
                for i in range(0, len(pcm), CHUNK_BYTES):
                    self._queue.append(pcm[i : i + CHUNK_BYTES])
        else:
            self._log.warning("clip saved as WAV but not queued to PCM feed "
                             "(ffmpeg missing?)", clip_id=clip_id)

        record = ClipRecord(
            clip_id=clip_id,
            text=text,
            wav_path=str(wav_path),
            duration_seconds=round(audio.duration_seconds, 2),
            profile_id=profile_id or synth.active_profile_id,
        )
        self._clips.append(record)
        self._log.info("commentary line queued", clip_id=clip_id,
                       duration_s=record.duration_seconds)
        return {
            "queued": True,
            "synthesized": True,
            "clip_id": clip_id,
            "wav_path": str(wav_path),
            "duration_seconds": record.duration_seconds,
            "feed_running": self.is_running(),
        }

    # ------------------------------------------------------------------ #
    # FIFO writer
    # ------------------------------------------------------------------ #
    def _ensure_fifo(self) -> None:
        if self.fifo_path.exists() and not self._is_fifo(self.fifo_path):
            raise RuntimeError(f"{self.fifo_path} exists and is not a FIFO")
        if not self.fifo_path.exists():
            os.mkfifo(self.fifo_path)

    @staticmethod
    def _is_fifo(path: Path) -> bool:
        import stat as statmod

        return statmod.S_ISFIFO(os.stat(path).st_mode)

    def start(self) -> Dict[str, Any]:
        if self.is_running():
            return {"running": True, "fifo": str(self.fifo_path), "already": True}
        self._ensure_fifo()
        self._stop_event.clear()
        self._writer_thread = threading.Thread(
            target=self._writer_loop, name="commentary-pcm-writer", daemon=True
        )
        self._writer_thread.start()
        self._log.info("commentary PCM feed started", fifo=str(self.fifo_path))
        return {"running": True, "fifo": str(self.fifo_path)}

    def stop(self) -> Dict[str, Any]:
        self._stop_event.set()
        if self._writer_thread and self._writer_thread.is_alive():
            self._writer_thread.join(timeout=5)
        self._writer_thread = None
        self._log.info("commentary PCM feed stopped")
        return {"running": False}

    def is_running(self) -> bool:
        return self._writer_thread is not None and self._writer_thread.is_alive()

    def _writer_loop(self) -> None:
        fd = -1
        try:
            # O_RDWR|O_NONBLOCK: the open never blocks waiting for a reader
            # (plain O_WRONLY|O_NONBLOCK fails with ENXIO when no reader is
            # present). The kernel pipe buffer absorbs ~0.3 s; beyond that,
            # writes return EAGAIN and audio is dropped until a reader
            # (FFmpeg) attaches and drains.
            fd = os.open(self.fifo_path, os.O_RDWR | os.O_NONBLOCK)
        except OSError as exc:
            self._log.error("cannot open commentary FIFO", error=str(exc))
            return
        silence = b"\x00" * CHUNK_BYTES
        try:
            while not self._stop_event.is_set():
                with self._queue_lock:
                    chunk = self._queue.popleft() if self._queue else None
                if chunk is None:
                    chunk = silence
                else:
                    gain = get_audio_mixer().effective_commentary_gain()
                    chunk = _apply_gain(chunk, gain)
                    if len(chunk) < CHUNK_BYTES:
                        chunk += b"\x00" * (CHUNK_BYTES - len(chunk))
                try:
                    os.write(fd, chunk)
                    self._no_reader_warned = False
                except OSError as exc:
                    if exc.errno in (errno.ENXIO, errno.EAGAIN):
                        # No reader draining the FIFO: drop, don't buffer forever.
                        if not self._no_reader_warned:
                            self._log.warning(
                                "no reader on commentary FIFO; audio dropped "
                                "until FFmpeg (or another reader) connects",
                                fifo=str(self.fifo_path),
                            )
                            self._no_reader_warned = True
                        time.sleep(0.2)
                        continue
                    raise
                # pace the stream: one CHUNK_SECONDS chunk per tick
                time.sleep(CHUNK_SECONDS)
        except Exception as exc:  # never kill the thread silently
            self._log.error("commentary writer loop failed", error=str(exc))
        finally:
            try:
                if fd >= 0:
                    os.close(fd)
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    # status / clips
    # ------------------------------------------------------------------ #
    def status(self) -> Dict[str, Any]:
        with self._queue_lock:
            queued_chunks = len(self._queue)
        return {
            "feed_running": self.is_running(),
            "fifo_path": str(self.fifo_path),
            "fifo_exists": self.fifo_path.exists(),
            "queued_chunks": queued_chunks,
            "queued_seconds": round(queued_chunks * CHUNK_SECONDS, 1),
            "clips_kept": len(self._clips),
            "tts": self.tts_status(),
            "ffmpeg_available": _ffmpeg_available(),
            "sample_format": f"s16le {SAMPLE_RATE}Hz stereo",
        }

    def recent_clips(self, limit: int = 20) -> List[Dict[str, Any]]:
        items = list(self._clips)[-limit:]
        items.reverse()
        return [
            {
                "clip_id": c.clip_id,
                "text": c.text,
                "wav_path": c.wav_path,
                "duration_seconds": c.duration_seconds,
                "profile_id": c.profile_id,
                "created_at": c.created_at,
            }
            for c in items
        ]

    def clip_wav_path(self, clip_id: str) -> Optional[Path]:
        for c in self._clips:
            if c.clip_id == clip_id:
                p = Path(c.wav_path)
                return p if p.exists() else None
        return None


_feed: Optional[CommentaryAudioFeed] = None
_feed_lock = threading.Lock()


def get_commentary_feed() -> CommentaryAudioFeed:
    """Module-level singleton shared by the API router."""
    global _feed
    with _feed_lock:
        if _feed is None:
            _feed = CommentaryAudioFeed()
        return _feed
