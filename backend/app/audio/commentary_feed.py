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
    # Phase 22 provenance: live | cache | cached_fallback
    source: str = "live"
    language: str = "en"


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

        # ── Phase 22 state ──────────────────────────────────────────
        # Dual-track FIFOs (language-tagged tracks for bilingual mode).
        self.hi_fifo_path = self.pipes_dir / "commentary_hi.pcm"
        self.en_fifo_path = self.pipes_dir / "commentary_en.pcm"
        self._track_queues: Dict[str, Deque[bytes]] = {"hi": deque(), "en": deque()}
        self._track_threads: Dict[str, Optional[threading.Thread]] = {"hi": None, "en": None}
        self._track_no_reader_warned: Dict[str, bool] = {"hi": False, "en": False}
        self.dual_dir = self.audio_dir / "dual_track"
        self.dual_dir.mkdir(parents=True, exist_ok=True)
        # Pacing gate (mode-driven) + bilingual alternation counter.
        self._last_spoken_at: float = 0.0
        self._blend_counter: int = 0
        # Human Assist suggestions for the human caster (never spoken).
        self._suggestions: Deque[Dict[str, Any]] = deque(maxlen=50)
        # Latency measurements (real, from enqueue_line runs).
        self._latency_samples: Deque[Dict[str, Any]] = deque(maxlen=200)
        # Most recent text-only line (Silent mode lower-third source).
        self._last_text_line: Optional[Dict[str, Any]] = None

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
            # Phase 21/T5a: which Piper voices are installed per language.
            "voices": stats.get("voices", {}),
            "voices_dir": stats.get("voices_dir"),
        }

    # ------------------------------------------------------------------ #
    # enqueue
    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    # Phase 22 helpers
    # ------------------------------------------------------------------ #
    def _mode_service(self):
        from .commentary_modes import get_commentary_mode_service

        return get_commentary_mode_service()

    def suggestions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Human Assist suggestions (newest first). Empty in other modes."""
        return list(reversed(list(self._suggestions)))[:limit]

    def latency_stats(self) -> Dict[str, Any]:
        """Real measured pipeline latency from enqueue_line runs.

        Stages per sample: profanity filter, TTS synthesis (engine's own
        latency), WAV->PCM conversion + queueing, and end-to-end total.
        Percentiles are computed over the recorded samples only — with no
        samples the values are null, never invented.
        """
        samples = list(self._latency_samples)
        if not samples:
            return {"samples": 0, "total_ms": None, "synthesis_ms": None,
                    "note": "no commentary lines measured yet"}

        def _pct(values: List[float], p: float) -> float:
            ordered = sorted(values)
            idx = min(len(ordered) - 1, int(len(ordered) * p))
            return round(ordered[idx], 1)

        totals = [s["total_ms"] for s in samples]
        synths = [s["synthesis_ms"] for s in samples if s.get("synthesis_ms") is not None]
        return {
            "samples": len(samples),
            "total_ms": {"p50": _pct(totals, 0.5), "p95": _pct(totals, 0.95),
                         "last": round(totals[-1], 1)},
            "synthesis_ms": (
                {"p50": _pct(synths, 0.5), "p95": _pct(synths, 0.95),
                 "last": round(synths[-1], 1)} if synths else None
            ),
            "sources": {
                src: sum(1 for s in samples if s.get("source") == src)
                for src in {s.get("source") for s in samples}
            },
        }

    async def enqueue_line(
        self,
        text: str,
        profile_id: Optional[str] = None,
        style: Optional[str] = None,
        language: Optional[str] = None,
        match_id: Optional[str] = None,
        priority: int = 50,
        event_ts: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Mode-aware commentary line pipeline (Phase 22):

            profanity filter -> mode gate (silent / human_assist / pacing)
            -> voice routing per mode -> Piper synthesis (or cached
            fallback) -> WAV clip + PCM FIFO(s) + subtitle cue(s)
            -> dual-track render when the mode is bilingual.

        Voice routing: an explicit ``profile_id`` wins; otherwise the
        ACTIVE MODE decides the profile/language (Hindi Main forces the
        Hindi voice, English Main the English voice, Bilingual Blend
        alternates and renders both tracks).
        """
        t_start = time.time()
        text = (text or "").strip()
        if not text:
            return {"queued": False, "synthesized": False, "reason": "empty text"}

        # 1) Profanity filter — before TTS and before any display.
        from .profanity import filter_profanity

        filtered = filter_profanity(text)
        text = filtered.text

        # 2) Mode gate.
        behavior = self._mode_service().behavior(match_id)
        mode_value = behavior.mode.value
        synth = self._get_synth()
        from app.agents.commentary_agents.voice_synthesizer import detect_text_language

        detected = detect_text_language(text)

        if not behavior.speaks:
            if behavior.suggestions_only:
                # Human Assist: text suggestion for the human caster only.
                suggestion = {
                    "text": text,
                    "language": detected,
                    "mode": mode_value,
                    "created_at": time.time(),
                    "profanity_filtered": filtered.filtered,
                }
                self._suggestions.append(suggestion)
                return {
                    "queued": False,
                    "synthesized": False,
                    "suggestion": True,
                    "mode": mode_value,
                    "reason": "human_assist mode: AI suggests text, the human caster speaks",
                    "text": text,
                }
            # Silent: no audio; text flows to lower-third + captions only.
            self._last_text_line = {
                "text": text, "language": detected, "created_at": time.time(),
                "mode": mode_value,
            }
            if behavior.text_overlay:
                from .subtitles import get_subtitle_store

                get_subtitle_store().add_cue(
                    detected, text, duration=max(2.0, len(text.split()) / 3.0 + 1.0),
                    source="text_only",
                )
            return {
                "queued": False,
                "synthesized": False,
                "text_only": True,
                "mode": mode_value,
                "reason": "silent mode: commentary is text-only (lower-third/captions)",
                "text": text,
            }

        # 3) Pacing gate (mode-driven min gap; high priority bypasses).
        now = time.time()
        if priority < 80 and self._last_spoken_at > 0:
            gap = now - self._last_spoken_at
            if gap < behavior.min_gap_seconds:
                return {
                    "queued": False,
                    "synthesized": False,
                    "mode": mode_value,
                    "reason": (
                        f"pacing: {behavior.min_gap_seconds:.1f}s minimum gap in "
                        f"{mode_value} mode ({gap:.1f}s elapsed); raise priority>=80 to bypass"
                    ),
                }

        # 4) Voice routing per mode.
        spoken_language = detected
        if profile_id is None:
            if behavior.mode.value == "bilingual_blend":
                # Alternate primary voice per line; dual render below
                # produces the other language's track too.
                self._blend_counter += 1
                spoken_language = "hi" if self._blend_counter % 2 == 1 else "en"
                routed = synth.get_profile_for_language(spoken_language)
                profile_id = routed.profile_id if routed else behavior.primary_profiles[0]
            elif behavior.primary_profiles:
                profile_id = behavior.primary_profiles[0]
                spoken_language = behavior.primary_language
            if language and profile_id is None:
                routed = synth.get_profile_for_language(language.strip().lower())
                if routed is not None:
                    profile_id = routed.profile_id
        kwargs: Dict[str, Any] = {}
        if style:
            from app.agents.commentary_agents.voice_synthesizer import VoiceStyle

            try:
                kwargs["style"] = VoiceStyle(style)
            except ValueError:
                self._log.warning("unknown voice style, using default", style=style)

        # 5) Synthesize (synthesizer falls back to the cached pack itself).
        audio = await synth.synthesize(text, profile_id=profile_id, **kwargs)
        if audio is None or not audio.synthesized or not audio.audio_data:
            self._log.warning("line not synthesized: no TTS engine or failure")
            return {
                "queued": False,
                "synthesized": False,
                "mode": mode_value,
                "reason": (
                    "No TTS engine configured (set AEGIS_TTS_ENGINE=piper and "
                    "AEGIS_TTS_MODEL_PATH) or synthesis failed. See docs/AUDIO.md."
                ),
            }
        self._last_spoken_at = time.time()
        spoken_text = audio.text  # differs from input on cached fallback
        source = getattr(audio, "source", "live")

        with self._seq_lock:
            self._seq += 1
            seq = self._seq
        clip_id = f"clip_{seq:04d}_{int(time.time())}"
        wav_path = self.clips_dir / f"{clip_id}.wav"
        wav_path.write_bytes(audio.audio_data)

        pcm = await asyncio.to_thread(_wav_to_pcm48_stereo, wav_path)
        if pcm:
            with self._queue_lock:
                for i in range(0, len(pcm), CHUNK_BYTES):
                    self._queue.append(pcm[i : i + CHUNK_BYTES])
            # Language track FIFO gets this language's PCM.
            self._queue_track_pcm(spoken_language, pcm)
        else:
            self._log.warning("clip saved as WAV but not queued to PCM feed "
                             "(ffmpeg missing?)", clip_id=clip_id)

        # 6) Subtitle cue for the spoken line.
        from .subtitles import get_subtitle_store

        store = get_subtitle_store()
        store.add_cue(spoken_language, spoken_text, duration=audio.duration_seconds,
                      source=source)

        record = ClipRecord(
            clip_id=clip_id,
            text=spoken_text,
            wav_path=str(wav_path),
            duration_seconds=round(audio.duration_seconds, 2),
            profile_id=profile_id or synth.active_profile_id,
            source=source,
            language=spoken_language,
        )
        self._clips.append(record)

        result: Dict[str, Any] = {
            "queued": True,
            "synthesized": True,
            "clip_id": clip_id,
            "wav_path": str(wav_path),
            "duration_seconds": record.duration_seconds,
            "feed_running": self.is_running(),
            "mode": mode_value,
            "language": spoken_language,
            "source": source,
            "profanity_filtered": filtered.filtered,
        }
        if source == "cached_fallback":
            result["requested_text"] = text

        # 7) Dual-track render (bilingual mode): translate + synthesize
        # the other language, queue it to its track FIFO, add its cue,
        # and mux a two-audio-stream program file when both clips exist.
        if behavior.dual_track:
            dual = await self._render_dual_track(
                text=text, primary_language=spoken_language,
                primary_wav=wav_path, style=style, seq=seq,
            )
            result["dual_track"] = dual

        # 8) Latency sample (honest, measured here).
        total_ms = (time.time() - t_start) * 1000.0
        self._latency_samples.append({
            "total_ms": total_ms,
            "synthesis_ms": getattr(audio, "latency_ms", None),
            "source": source,
            "mode": mode_value,
            "event_to_line_ms": (
                round((t_start - event_ts) * 1000.0, 1) if event_ts else None
            ),
            "at": t_start,
        })
        result["latency_ms"] = round(total_ms, 1)
        self._log.info("commentary line queued", clip_id=clip_id,
                       duration_s=record.duration_seconds, mode=mode_value,
                       source=source)
        return result

    def _queue_track_pcm(self, language: str, pcm: bytes) -> None:
        queue = self._track_queues.get(language)
        if queue is None:
            return
        with self._queue_lock:
            for i in range(0, len(pcm), CHUNK_BYTES):
                queue.append(pcm[i : i + CHUNK_BYTES])

    async def _render_dual_track(
        self,
        text: str,
        primary_language: str,
        primary_wav: Path,
        style: Optional[str],
        seq: int,
    ) -> Dict[str, Any]:
        """Render the second-language track for one line.

        Translation goes through LanguageManager (local Ollama when
        reachable). When no real translation exists the second track is
        NOT faked: the result reports ``rendered: False`` with the reason
        and no second clip is produced.
        """
        other = "en" if primary_language == "hi" else "hi"
        try:
            from app.agents.base_agent import AgentConfig
            from app.agents.commentary_agents.language_manager import (
                Language,
                LanguageManager,
            )
            from app.core.event_bus import EventBus

            manager = LanguageManager(
                AgentConfig(agent_id="dual-track-translator"), EventBus()
            )
            result = manager.translate(
                text,
                target_language=Language.ENGLISH if other == "en" else Language.HINDI,
                source_language=Language.HINDI if primary_language == "hi" else Language.ENGLISH,
            )
            if not result.translated:
                return {"rendered": False, "language": other,
                        "reason": f"no translation available ({result.note})"}
            synth = self._get_synth()
            routed = synth.get_profile_for_language(other)
            kwargs: Dict[str, Any] = {}
            if style:
                from app.agents.commentary_agents.voice_synthesizer import VoiceStyle

                try:
                    kwargs["style"] = VoiceStyle(style)
                except ValueError:
                    pass
            audio = await synth.synthesize(
                result.text,
                profile_id=routed.profile_id if routed else None,
                **kwargs,
            )
            if audio is None or not audio.synthesized or not audio.audio_data:
                return {"rendered": False, "language": other,
                        "reason": "secondary synthesis failed"}
            clip_id = f"clip_{seq:04d}_{other}_{int(time.time())}"
            wav_path = self.clips_dir / f"{clip_id}.wav"
            wav_path.write_bytes(audio.audio_data)
            pcm = await asyncio.to_thread(_wav_to_pcm48_stereo, wav_path)
            if pcm:
                self._queue_track_pcm(other, pcm)
            from .subtitles import get_subtitle_store

            get_subtitle_store().add_cue(
                other, audio.text, duration=audio.duration_seconds,
                source=getattr(audio, "source", "live"),
            )
            mux = await asyncio.to_thread(
                self._mux_dual_track_file, primary_wav, primary_language,
                wav_path, other, seq,
            )
            return {"rendered": True, "language": other, "clip_id": clip_id,
                    "text": audio.text, "program_file": mux}
        except Exception as exc:
            self._log.warning("dual-track render failed", error=str(exc))
            return {"rendered": False, "language": other, "reason": str(exc)}

    def _mux_dual_track_file(
        self, wav_a: Path, lang_a: str, wav_b: Path, lang_b: str, seq: int
    ) -> Optional[str]:
        """Mux two WAVs into one .mkv with two language-tagged audio
        streams (the program/recording dual-track deliverable)."""
        if not _ffmpeg_available():
            return None
        out = self.dual_dir / f"program_{seq:04d}_{int(time.time())}.mkv"
        lang3 = {"hi": "hin", "en": "eng"}
        try:
            proc = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(wav_a), "-i", str(wav_b),
                    "-map", "0:a", "-map", "1:a",
                    "-c:a", "aac", "-b:a", "128k",
                    "-metadata:s:a:0", f"language={lang3.get(lang_a, lang_a)}",
                    "-metadata:s:a:1", f"language={lang3.get(lang_b, lang_b)}",
                    str(out),
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
            )
            if proc.returncode != 0 or not out.exists():
                self._log.error("dual-track mux failed",
                                stderr=proc.stderr.decode(errors="replace")[:300])
                return None
            return str(out)
        except Exception as exc:
            self._log.error("dual-track mux error", error=str(exc))
            return None

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
        for path in (self.hi_fifo_path, self.en_fifo_path):
            if path.exists() and not self._is_fifo(path):
                raise RuntimeError(f"{path} exists and is not a FIFO")
            if not path.exists():
                os.mkfifo(path)
        self._stop_event.clear()
        self._writer_thread = threading.Thread(
            target=self._writer_loop, name="commentary-pcm-writer", daemon=True
        )
        self._writer_thread.start()
        # Dual-track writers (Phase 22): one FIFO per language track.
        for lang, path in (("hi", self.hi_fifo_path), ("en", self.en_fifo_path)):
            t = threading.Thread(
                target=self._track_writer_loop, args=(lang, path),
                name=f"commentary-pcm-writer-{lang}", daemon=True,
            )
            t.start()
            self._track_threads[lang] = t
        self._log.info("commentary PCM feed started", fifo=str(self.fifo_path))
        return {"running": True, "fifo": str(self.fifo_path),
                "track_fifos": {"hi": str(self.hi_fifo_path), "en": str(self.en_fifo_path)}}

    def _track_writer_loop(self, language: str, fifo_path: Path) -> None:
        """Same FIFO discipline as the primary writer, per language track:
        silence when idle, track PCM when queued, drop (never buffer
        forever) when no reader is attached."""
        fd = -1
        try:
            fd = os.open(fifo_path, os.O_RDWR | os.O_NONBLOCK)
        except OSError as exc:
            self._log.error("cannot open track FIFO", language=language, error=str(exc))
            return
        silence = b"\x00" * CHUNK_BYTES
        queue = self._track_queues[language]
        try:
            while not self._stop_event.is_set():
                with self._queue_lock:
                    chunk = queue.popleft() if queue else None
                if chunk is None:
                    chunk = silence
                else:
                    gain = get_audio_mixer().effective_commentary_gain()
                    chunk = _apply_gain(chunk, gain)
                    if len(chunk) < CHUNK_BYTES:
                        chunk += b"\x00" * (CHUNK_BYTES - len(chunk))
                try:
                    os.write(fd, chunk)
                    self._track_no_reader_warned[language] = False
                except OSError as exc:
                    if exc.errno in (errno.ENXIO, errno.EAGAIN):
                        if not self._track_no_reader_warned[language]:
                            self._log.warning(
                                "no reader on track FIFO; audio dropped until a reader connects",
                                language=language, fifo=str(fifo_path),
                            )
                            self._track_no_reader_warned[language] = True
                        time.sleep(0.2)
                        continue
                    raise
                time.sleep(CHUNK_SECONDS)
        except Exception as exc:
            self._log.error("track writer loop failed", language=language, error=str(exc))
        finally:
            try:
                if fd >= 0:
                    os.close(fd)
            except OSError:
                pass

    def stop(self) -> Dict[str, Any]:
        self._stop_event.set()
        if self._writer_thread and self._writer_thread.is_alive():
            self._writer_thread.join(timeout=5)
        self._writer_thread = None
        for lang, t in self._track_threads.items():
            if t and t.is_alive():
                t.join(timeout=5)
            self._track_threads[lang] = None
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
            track_queued = {k: len(v) for k, v in self._track_queues.items()}
        mode = self._mode_service().describe()
        return {
            "feed_running": self.is_running(),
            "fifo_path": str(self.fifo_path),
            "fifo_exists": self.fifo_path.exists(),
            "track_fifos": {
                "hi": str(self.hi_fifo_path),
                "en": str(self.en_fifo_path),
            },
            "track_queued_chunks": track_queued,
            "queued_chunks": queued_chunks,
            "queued_seconds": round(queued_chunks * CHUNK_SECONDS, 1),
            "clips_kept": len(self._clips),
            "tts": self.tts_status(),
            "commentary_mode": mode,
            "latency": self.latency_stats(),
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
                "source": c.source,
                "language": c.language,
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
