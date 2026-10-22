# backend/app/services/replay/__init__.py
"""AEGIS AI — Replay + highlight detection service package (Phase 10).

This is an **event replay** system, not video replay: it re-renders
broadcast-event segments (kills, knocks, fight windows) from the
time-windowed ring buffer so the output page / PiP can replay the
action contextually. There is no video DVR — see docs/GAME_FEED.md.

Phase 25 (P0-2) adds the two pieces that make replays professional:
``recorder`` (continuous ISO recording of every observer feed into
RECORDINGS_DIR, with truthful sidecar timing) and ``queue`` (the
offer/take queue with Replay A/B discipline — see those modules).
"""

from .buffer import REPLAY_EVENT_TYPES, BufferedEvent, ReplayBuffer
from .highlights import HighlightDetector, ScoredMoment
from .queue import ReplayQueue, ReplayQueueEntry, ReplayTakeRefused
from .recorder import ISORecorder, build_iso_recorder, build_iso_recorder_from_manager
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
    "ISORecorder",
    "build_iso_recorder",
    "build_iso_recorder_from_manager",
    "ReplayQueue",
    "ReplayQueueEntry",
    "ReplayTakeRefused",
    "CaptureRequest",
    "Clip",
    "ClipRequest",
    "Highlight",
    "Mark",
    "PlayRequest",
]
