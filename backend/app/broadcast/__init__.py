# backend/app/broadcast/__init__.py
"""
AEGIS AI - Broadcast Module
Complete OBS integration layer for live esports production.
Scene management, source control, transitions, audio mixing,
replay system, and recording management.
"""

from .obs_client import OBSClient, OBSConfig, ConnectionState
from .scene_manager import SceneManager, Scene, SceneItem
from .source_manager import SourceManager, Source, SourceType
from .transition_controller import TransitionController, Transition, TransitionType
from .audio_mixer import AudioMixer, AudioSource, AudioChannel
from .replay_controller import ReplayController, ReplayClip
from .recording_manager import RecordingManager, Recording, RecordingStatus

__all__ = [
    "OBSClient", "OBSConfig", "ConnectionState",
    "SceneManager", "Scene", "SceneItem",
    "SourceManager", "Source", "SourceType",
    "TransitionController", "Transition", "TransitionType",
    "AudioMixer", "AudioSource", "AudioChannel",
    "ReplayController", "ReplayClip",
    "RecordingManager", "Recording", "RecordingStatus",
]