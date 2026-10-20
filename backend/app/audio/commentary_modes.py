"""AEGIS AI — commentary modes (Phase 22 / M13 completion).

Five director-controlled commentary modes with REAL behavioural
differences — not labels. Each mode changes:

  * language mix of generated lines (which language(s) a line is spoken in)
  * which Piper voice(s) are used
  * pacing (minimum gap / per-trigger cooldown multiplier, filler policy)
  * content mix (play-by-play vs analysis vs hype weights)
  * whether the AI speaks at all:
      - ``silent``      -> no TTS at all; text-only lower-thirds/captions.
      - ``human_assist``-> AI generates text suggestions for the human
                           caster but NEVER synthesizes audio.

Modes (PRD §13):
  hindi_main       Hindi/Hinglish voice (hi_IN-pratham), Hindi-first lines.
  english_main     English voice (en_US-amy), English lines.
  bilingual_blend  Alternating Hindi + English lines; BOTH voices; the
                   dual-track path renders both languages for every line.
  silent           Muted. No audio synthesis. Captions/lower-thirds only.
  human_assist     Text suggestions only (queued for the human caster).

Persistence follows the AudioMixer pattern: JSON state file in
``AEGIS_AUDIO_DIR`` (``commentary_mode.json``), process-local singleton,
per-match overrides keyed by match_id with a global default.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

from .mixer import default_audio_dir

logger = structlog.get_logger("aegis.audio.commentary_modes")


class CommentaryMode(str, Enum):
    HINDI_MAIN = "hindi_main"
    ENGLISH_MAIN = "english_main"
    BILINGUAL_BLEND = "bilingual_blend"
    SILENT = "silent"
    HUMAN_ASSIST = "human_assist"


#: Human-readable labels (director UI).
MODE_LABELS: Dict[CommentaryMode, str] = {
    CommentaryMode.HINDI_MAIN: "Hindi Main",
    CommentaryMode.ENGLISH_MAIN: "English Main",
    CommentaryMode.BILINGUAL_BLEND: "Bilingual Blend",
    CommentaryMode.SILENT: "Silent",
    CommentaryMode.HUMAN_ASSIST: "Human Assist",
}

# Content categories a commentary line can belong to.
CONTENT_PBP = "play_by_play"
CONTENT_ANALYSIS = "analysis"
CONTENT_HYPE = "hype"


@dataclass(frozen=True)
class ModeBehavior:
    """Concrete behaviour knobs for one mode. Every field is consumed by
    real code (commentary_feed / script routing / pacing gates) — nothing
    here is decorative."""

    mode: CommentaryMode
    #: Whether AI audio is synthesized at all.
    speaks: bool
    #: Whether generated text is queued as suggestions for a human caster
    #: instead of being spoken (human_assist).
    suggestions_only: bool
    #: Whether text still flows to lower-thirds / captions when not spoken.
    text_overlay: bool
    #: Primary spoken language ("hi" | "en").
    primary_language: str
    #: Secondary spoken language for blend mode, else None.
    secondary_language: Optional[str]
    #: Voice profile ids, in preference order, for the primary track.
    primary_profiles: tuple
    #: Voice profile id for the secondary (dual) track, else None.
    secondary_profile: Optional[str]
    #: Minimum seconds between spoken lines (pacing).
    min_gap_seconds: float
    #: Multiplier applied to per-trigger cooldowns (pacing).
    cooldown_multiplier: float
    #: Whether filler lines are generated during silence.
    filler_enabled: bool
    #: Content-mix weights {play_by_play, analysis, hype} — used to pick
    #: the script style for a trigger (see pick_style()).
    content_mix: Dict[str, float]
    #: Whether the dual-track (hi+en) render path runs for every line.
    dual_track: bool


MODE_BEHAVIORS: Dict[CommentaryMode, ModeBehavior] = {
    CommentaryMode.HINDI_MAIN: ModeBehavior(
        mode=CommentaryMode.HINDI_MAIN,
        speaks=True,
        suggestions_only=False,
        text_overlay=True,
        primary_language="hi",
        secondary_language=None,
        primary_profiles=("hindi_caster_1", "hinglish_caster"),
        secondary_profile=None,
        min_gap_seconds=3.0,
        cooldown_multiplier=1.0,
        filler_enabled=True,
        content_mix={CONTENT_PBP: 0.45, CONTENT_ANALYSIS: 0.20, CONTENT_HYPE: 0.35},
        dual_track=False,
    ),
    CommentaryMode.ENGLISH_MAIN: ModeBehavior(
        mode=CommentaryMode.ENGLISH_MAIN,
        speaks=True,
        suggestions_only=False,
        text_overlay=True,
        primary_language="en",
        secondary_language=None,
        primary_profiles=("english_analyst",),
        secondary_profile=None,
        min_gap_seconds=4.0,
        cooldown_multiplier=1.25,
        filler_enabled=True,
        content_mix={CONTENT_PBP: 0.40, CONTENT_ANALYSIS: 0.40, CONTENT_HYPE: 0.20},
        dual_track=False,
    ),
    CommentaryMode.BILINGUAL_BLEND: ModeBehavior(
        mode=CommentaryMode.BILINGUAL_BLEND,
        speaks=True,
        suggestions_only=False,
        text_overlay=True,
        primary_language="hi",
        secondary_language="en",
        primary_profiles=("hinglish_caster", "hindi_caster_1"),
        secondary_profile="english_analyst",
        min_gap_seconds=3.5,
        cooldown_multiplier=1.1,
        filler_enabled=True,
        content_mix={CONTENT_PBP: 0.40, CONTENT_ANALYSIS: 0.25, CONTENT_HYPE: 0.35},
        dual_track=True,
    ),
    CommentaryMode.SILENT: ModeBehavior(
        mode=CommentaryMode.SILENT,
        speaks=False,
        suggestions_only=False,
        text_overlay=True,
        primary_language="hi",
        secondary_language=None,
        primary_profiles=(),
        secondary_profile=None,
        min_gap_seconds=0.0,
        cooldown_multiplier=1.0,
        filler_enabled=False,
        content_mix={CONTENT_PBP: 0.5, CONTENT_ANALYSIS: 0.3, CONTENT_HYPE: 0.2},
        dual_track=False,
    ),
    CommentaryMode.HUMAN_ASSIST: ModeBehavior(
        mode=CommentaryMode.HUMAN_ASSIST,
        speaks=False,
        suggestions_only=True,
        text_overlay=False,
        primary_language="en",
        secondary_language="hi",
        primary_profiles=(),
        secondary_profile=None,
        min_gap_seconds=2.0,
        cooldown_multiplier=0.8,
        filler_enabled=False,
        content_mix={CONTENT_PBP: 0.30, CONTENT_ANALYSIS: 0.50, CONTENT_HYPE: 0.20},
        dual_track=False,
    ),
}


def behavior_for(mode: CommentaryMode) -> ModeBehavior:
    return MODE_BEHAVIORS[mode]


def pick_style(mode: CommentaryMode, trigger: str, roll: float) -> str:
    """Pick a content style for a line from the mode's content mix.

    ``roll`` is a caller-supplied uniform float in [0, 1) so tests are
    deterministic. High-priority triggers (clutch/team wipe) always map
    to hype regardless of the roll — the mix governs ordinary lines.
    """
    hype_triggers = {"clutch_win", "clutch_start", "team_wipe", "multi_kill", "winner_announced"}
    if trigger in hype_triggers:
        return "hype"
    mix = MODE_BEHAVIORS[mode].content_mix
    pbp = mix[CONTENT_PBP]
    if roll < pbp:
        return CONTENT_PBP
    if roll < pbp + mix[CONTENT_ANALYSIS]:
        return CONTENT_ANALYSIS
    return CONTENT_HYPE


# Map content style -> ScriptGenerator CommentaryStyle value.
STYLE_FOR_CONTENT = {
    CONTENT_PBP: "play_by_play",
    CONTENT_ANALYSIS: "analytical",
    CONTENT_HYPE: "hype",
}


class CommentaryModeService:
    """Director-controlled commentary mode: global default + per-match
    overrides, persisted to JSON (AudioMixer pattern)."""

    def __init__(self, audio_dir: Path | None = None) -> None:
        self.audio_dir = audio_dir or default_audio_dir()
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self.audio_dir / "commentary_mode.json"
        self._lock = threading.RLock()
        self._default_mode: CommentaryMode = CommentaryMode.BILINGUAL_BLEND
        self._per_match: Dict[str, CommentaryMode] = {}
        self.updated_at: float = time.time()
        self.updated_by: Optional[str] = None
        self._load()

    # -------------------------------------------------------------- #
    # persistence
    # -------------------------------------------------------------- #
    def _load(self) -> None:
        try:
            if self._state_file.exists():
                data = json.loads(self._state_file.read_text())
                self._default_mode = CommentaryMode(data.get("default_mode", "bilingual_blend"))
                self._per_match = {
                    k: CommentaryMode(v)
                    for k, v in (data.get("per_match") or {}).items()
                    if v in CommentaryMode._value2member_map_
                }
                self.updated_by = data.get("updated_by")
                logger.info("commentary mode state restored", path=str(self._state_file))
        except Exception as exc:
            logger.warning("commentary mode state unreadable, using defaults", error=str(exc))

    def _save(self) -> None:
        try:
            self._state_file.write_text(json.dumps(self.to_dict(), indent=2))
        except Exception as exc:
            logger.warning("commentary mode save failed", error=str(exc))

    # -------------------------------------------------------------- #
    # API
    # -------------------------------------------------------------- #
    def get_mode(self, match_id: Optional[str] = None) -> CommentaryMode:
        with self._lock:
            if match_id and match_id in self._per_match:
                return self._per_match[match_id]
            return self._default_mode

    def behavior(self, match_id: Optional[str] = None) -> ModeBehavior:
        return behavior_for(self.get_mode(match_id))

    def set_mode(
        self,
        mode: CommentaryMode | str,
        match_id: Optional[str] = None,
        updated_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        mode = CommentaryMode(mode)
        with self._lock:
            if match_id:
                self._per_match[match_id] = mode
            else:
                self._default_mode = mode
            self.updated_at = time.time()
            self.updated_by = updated_by
            self._save()
            logger.info(
                "commentary mode set",
                mode=mode.value,
                match_id=match_id,
                updated_by=updated_by,
            )
            return self.to_dict()

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "default_mode": self._default_mode.value,
                "per_match": {k: v.value for k, v in self._per_match.items()},
                "updated_at": self.updated_at,
                "updated_by": self.updated_by,
            }

    def describe(self, match_id: Optional[str] = None) -> Dict[str, Any]:
        """Full mode description for API/UI: active mode + its behaviour."""
        mode = self.get_mode(match_id)
        b = behavior_for(mode)
        return {
            "mode": mode.value,
            "label": MODE_LABELS[mode],
            "match_id": match_id,
            "speaks": b.speaks,
            "suggestions_only": b.suggestions_only,
            "text_overlay": b.text_overlay,
            "primary_language": b.primary_language,
            "secondary_language": b.secondary_language,
            "primary_profiles": list(b.primary_profiles),
            "secondary_profile": b.secondary_profile,
            "min_gap_seconds": b.min_gap_seconds,
            "cooldown_multiplier": b.cooldown_multiplier,
            "filler_enabled": b.filler_enabled,
            "content_mix": dict(b.content_mix),
            "dual_track": b.dual_track,
            "all_modes": [
                {"mode": m.value, "label": MODE_LABELS[m]} for m in CommentaryMode
            ],
        }


_service: Optional[CommentaryModeService] = None
_service_lock = threading.Lock()


def get_commentary_mode_service() -> CommentaryModeService:
    global _service
    with _service_lock:
        if _service is None:
            _service = CommentaryModeService()
        return _service
