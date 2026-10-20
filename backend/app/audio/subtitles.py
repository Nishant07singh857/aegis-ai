"""AEGIS AI — subtitle / caption sidecar (Phase 22 / M13).

Commentary lines already carry text + a generation timestamp. This
module records them as caption cues (per language) and renders a
standards-compliant WebVTT sidecar that the output/watch pages (or an
external player / OBS) can consume:

    GET /api/v1/audio/subtitles/{hi|en}.vtt
    GET /api/v1/audio/captions            -> latest cues as JSON
    GET /api/v1/audio/captions/current    -> cue active "now" (for overlay)

Timing is honest: a cue starts when its line was generated/enqueued and
lasts for the line's measured/estimated spoken duration (min 2s). Cues
are kept in a bounded in-memory ring per language; nothing is invented —
an empty track renders a valid empty WebVTT document.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

import structlog

logger = structlog.get_logger("aegis.audio.subtitles")

MAX_CUES_PER_LANGUAGE = 500


@dataclass
class CaptionCue:
    language: str  # "hi" | "en"
    text: str
    start: float  # epoch seconds when the line was enqueued
    duration: float  # seconds the cue stays up
    source: str = "live"  # live | cached_fallback | suggestion

    @property
    def end(self) -> float:
        return self.start + max(self.duration, 0.5)

    def to_dict(self) -> dict:
        return {
            "language": self.language,
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "duration": round(self.duration, 2),
            "source": self.source,
        }


def _vtt_timestamp(epoch_seconds: float, origin: float) -> str:
    rel = max(0.0, epoch_seconds - origin)
    hours = int(rel // 3600)
    minutes = int((rel % 3600) // 60)
    seconds = rel % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


def render_webvtt(cues: List[CaptionCue]) -> str:
    """Render cues as a WebVTT document (times relative to first cue)."""
    lines = ["WEBVTT - AEGIS AI commentary captions", ""]
    if not cues:
        return "\n".join(lines) + "\n"
    origin = cues[0].start
    for i, cue in enumerate(cues, start=1):
        text = cue.text.replace("-->", "->").replace("\n", " ").strip()
        if not text:
            continue
        lines.append(str(i))
        lines.append(
            f"{_vtt_timestamp(cue.start, origin)} --> {_vtt_timestamp(cue.end, origin)}"
        )
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


class SubtitleStore:
    """Bounded per-language cue store + WebVTT rendering."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cues: Dict[str, Deque[CaptionCue]] = {
            "hi": deque(maxlen=MAX_CUES_PER_LANGUAGE),
            "en": deque(maxlen=MAX_CUES_PER_LANGUAGE),
        }
        self._match_started_at: Optional[float] = None

    def set_match_start(self, ts: Optional[float] = None) -> None:
        with self._lock:
            self._match_started_at = ts if ts is not None else time.time()

    def add_cue(
        self,
        language: str,
        text: str,
        duration: float,
        start: Optional[float] = None,
        source: str = "live",
    ) -> Optional[CaptionCue]:
        lang = (language or "en").strip().lower()
        if lang not in self._cues:
            lang = "en"
        text = (text or "").strip()
        if not text:
            return None
        cue = CaptionCue(
            language=lang,
            text=text,
            start=start if start is not None else time.time(),
            duration=max(float(duration or 0), 2.0),
            source=source,
        )
        with self._lock:
            self._cues[lang].append(cue)
        return cue

    def cues(self, language: str, limit: int = 200) -> List[CaptionCue]:
        lang = (language or "en").strip().lower()
        with self._lock:
            items = list(self._cues.get(lang, []))
        return items[-limit:]

    def webvtt(self, language: str) -> str:
        return render_webvtt(self.cues(language, limit=MAX_CUES_PER_LANGUAGE))

    def current(self, language: Optional[str] = None) -> Optional[dict]:
        """Cue active right now (for the captions overlay), or None."""
        now = time.time()
        langs = [language] if language else ["hi", "en"]
        best: Optional[CaptionCue] = None
        with self._lock:
            for lang in langs:
                for cue in reversed(list(self._cues.get(lang or "en", []))):
                    if cue.start <= now <= cue.end:
                        if best is None or cue.start > best.start:
                            best = cue
                        break
        return best.to_dict() if best else None

    def latest(self, limit: int = 20) -> List[dict]:
        with self._lock:
            all_cues = [c for dq in self._cues.values() for c in dq]
        all_cues.sort(key=lambda c: c.start)
        return [c.to_dict() for c in all_cues[-limit:]]

    def clear(self) -> None:
        with self._lock:
            for dq in self._cues.values():
                dq.clear()


_store: Optional[SubtitleStore] = None
_store_lock = threading.Lock()


def get_subtitle_store() -> SubtitleStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = SubtitleStore()
        return _store
