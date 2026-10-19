"""Pydantic schemas for the /api/v1/audio endpoints."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MixerStateResponse(BaseModel):
    commentary_volume: float
    game_volume: float
    master_mute: bool
    updated_at: float


class MixerUpdateRequest(BaseModel):
    commentary_volume: Optional[float] = Field(
        default=None, ge=0.0, le=1.5, description="Commentary gain, 0.0–1.5."
    )
    game_volume: Optional[float] = Field(
        default=None, ge=0.0, le=1.5, description="Game-audio gain hint for the FFmpeg mix, 0.0–1.5."
    )
    master_mute: Optional[bool] = Field(default=None, description="Mute all audio.")


class CommentaryLineRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000, description="Commentary line to speak.")
    profile_id: Optional[str] = Field(default=None, description="Voice profile id (default: active).")
    style: Optional[str] = Field(
        default=None,
        description="One of: normal, excited, calm, dramatic, whisper, shout.",
    )


class CommentaryLineResponse(BaseModel):
    queued: bool
    synthesized: bool
    clip_id: Optional[str] = None
    wav_path: Optional[str] = None
    duration_seconds: Optional[float] = None
    feed_running: Optional[bool] = None
    reason: Optional[str] = None


class ClipInfo(BaseModel):
    clip_id: str
    text: str
    duration_seconds: float
    profile_id: str
    created_at: float


class AudioStatusResponse(BaseModel):
    tts: Dict[str, Any]
    mixer: MixerStateResponse
    feed: Dict[str, Any]
    game_audio: Dict[str, Any]


class FeedActionResponse(BaseModel):
    running: bool
    fifo: Optional[str] = None
    already: Optional[bool] = None


class GameAudioActionResponse(BaseModel):
    recording: bool
    route: str
    record_path: Optional[str] = None
    already: Optional[bool] = None
    note: Optional[str] = None
    error: Optional[str] = None
