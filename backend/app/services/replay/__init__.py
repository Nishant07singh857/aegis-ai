# backend/app/services/replay/__init__.py
"""AEGIS AI — Replay + highlight detection service package (Phase 10).

This is an **event replay** system, not video replay: it re-renders
broadcast-event segments (kills, knocks, fight windows) from the
time-windowed ring buffer so the output page / PiP can replay the
action contextually. There is no video DVR — see docs/GAME_FEED.md.
"""

from .buffer import REPLAY_EVENT_TYPES, BufferedEvent, ReplayBuffer
from .highlights import HighlightDetector, ScoredMoment
from .service import replay_service, ReplayService
from .schemas import (
    CaptureRequest,
    Clip,
    ClipRequest,
    Highlight,
    Mark,
    PlayRequest,
)

__all__ = [
    "REPLAY_EVENT_TYPES",
    "BufferedEvent",
    "ReplayBuffer",
    "HighlightDetector",
    "ScoredMoment",
    "replay_service",
    "ReplayService",
    "CaptureRequest",
    "Clip",
    "ClipRequest",
    "Highlight",
    "Mark",
    "PlayRequest",
]
