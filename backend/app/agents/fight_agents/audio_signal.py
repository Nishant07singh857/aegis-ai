# backend/app/agents/fight_agents/audio_signal.py
"""
Audio-gunshot fight signal — plug interface (Phase 22 / W3, M4).

The PRD lists "audio gunshots" as a fight-detection signal
(``FightSignal.SHOOTING``, weight 0.05 in ``fight_detector.py``). In
this build there is NO producer: spectator audio is the mixed,
delayed program feed, and no per-feed gunshot classifier exists.
Inventing gunshot events from that mix would be fabrication.

This module is the complete plug point:

* :class:`AudioGunshotProvider` — the interface a real producer
  implements (e.g. a future per-device audio classifier). A reading
  carries an honest ``confidence`` and the team/feed it belongs to.
* :class:`NullAudioGunshotProvider` — today's producer: ``poll()``
  always returns ``None``, so the SHOOTING signal stays ABSENT from
  fight detection (documented, deliberate).
* :func:`poll_audio_signal` — the adapter the fight detector calls:
  provider reading in → ``FightSignalData(SHOOTING)`` out; no reading
  → ``None`` (no signal created).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class GunshotReading:
    """One gunshot-activity reading from an audio producer."""

    team_id: str
    #: Estimated shots/activity intensity 0..100 (producer-defined,
    #: must be documented by the producer; never a guess by callers).
    intensity: float
    confidence: float  # 0..1
    timestamp: float = 0.0


class AudioGunshotProvider(ABC):
    """Interface for a real gunshot-activity producer."""

    @abstractmethod
    def poll(self) -> Optional[GunshotReading]:
        """Latest reading, or None when no gunshot activity is measured."""


class NullAudioGunshotProvider(AudioGunshotProvider):
    """No producer in this build — the signal stays absent, honestly."""

    def poll(self) -> Optional[GunshotReading]:
        return None


def poll_audio_signal(provider: AudioGunshotProvider):
    """Adapt a provider reading to a SHOOTING FightSignalData (or None).

    Import of fight_detector is local to avoid a circular import
    (fight_detector does not import this module at module level).
    """
    reading = provider.poll()
    if reading is None:
        return None
    from .fight_detector import FightSignal, FightSignalData

    return FightSignalData(
        signal_type=FightSignal.SHOOTING,
        timestamp=reading.timestamp or time.time(),
        source_team_id=reading.team_id,
        value=max(0.0, min(100.0, float(reading.intensity))),
        confidence=max(0.0, min(1.0, float(reading.confidence))),
    )
