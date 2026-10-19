"""AEGIS AI — audio mixer state.

Software volume/mute controls for the broadcast audio pipeline. The
mixer itself applies commentary gain to the commentary PCM feed (see
app/audio/commentary_feed.py); game-audio gain is applied by the FFmpeg
filtergraph that consumes the game-audio input (see docs/AUDIO.md).

State persists to JSON in AEGIS_AUDIO_DIR so a restart keeps the
operator's levels. All values are honest runtime state — nothing here
pretends audio hardware exists.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict

import structlog

logger = structlog.get_logger("aegis.audio.mixer")


def default_audio_dir() -> Path:
    """Runtime audio directory: $AEGIS_AUDIO_DIR or <repo>/audio_out."""
    env = os.environ.get("AEGIS_AUDIO_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    # backend/app/audio/mixer.py -> backend -> repo root
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "audio_out"


class AudioMixer:
    """Volume/mute state for commentary, game audio, and master."""

    def __init__(self, audio_dir: Path | None = None) -> None:
        self.audio_dir = audio_dir or default_audio_dir()
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self.audio_dir / "mixer_state.json"
        # RLock: set_levels() holds the lock while calling to_dict(),
        # which also acquires it.
        self._lock = threading.RLock()
        self.commentary_volume: float = 1.0
        self.game_volume: float = 1.0
        self.master_mute: bool = False
        self.updated_at: float = time.time()
        self._load()

    # ------------------------------------------------------------------ #
    # persistence
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        try:
            if self._state_file.exists():
                data = json.loads(self._state_file.read_text())
                self.commentary_volume = self._clamp(float(data.get("commentary_volume", 1.0)))
                self.game_volume = self._clamp(float(data.get("game_volume", 1.0)))
                self.master_mute = bool(data.get("master_mute", False))
                logger.info("mixer state restored", path=str(self._state_file))
        except Exception as exc:  # corrupt file -> honest defaults
            logger.warning("mixer state unreadable, using defaults", error=str(exc))

    def _save(self) -> None:
        try:
            self._state_file.write_text(json.dumps(self.to_dict(), indent=2))
        except Exception as exc:
            logger.warning("mixer state save failed", error=str(exc))

    # ------------------------------------------------------------------ #
    # API
    # ------------------------------------------------------------------ #
    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.5, value))

    def set_levels(
        self,
        commentary_volume: float | None = None,
        game_volume: float | None = None,
        master_mute: bool | None = None,
    ) -> Dict[str, Any]:
        with self._lock:
            if commentary_volume is not None:
                self.commentary_volume = self._clamp(commentary_volume)
            if game_volume is not None:
                self.game_volume = self._clamp(game_volume)
            if master_mute is not None:
                self.master_mute = master_mute
            self.updated_at = time.time()
            self._save()
            return self.to_dict()

    def effective_commentary_gain(self) -> float:
        """Gain actually applied to commentary PCM (0.0 when muted)."""
        return 0.0 if self.master_mute else self.commentary_volume

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "commentary_volume": round(self.commentary_volume, 3),
                "game_volume": round(self.game_volume, 3),
                "master_mute": self.master_mute,
                "updated_at": self.updated_at,
            }


_mixer: AudioMixer | None = None
_mixer_lock = threading.Lock()


def get_audio_mixer() -> AudioMixer:
    """Module-level singleton shared by the API router and the feed."""
    global _mixer
    with _mixer_lock:
        if _mixer is None:
            _mixer = AudioMixer()
        return _mixer
