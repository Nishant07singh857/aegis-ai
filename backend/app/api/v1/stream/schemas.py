"""Pydantic schemas for the /api/v1/stream endpoints.

Stream keys are NEVER accepted in request bodies and NEVER appear in
responses — only ``*_configured`` booleans.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from app.streaming.destinations import KNOWN_DESTINATIONS
from app.streaming.service import RESOLUTIONS


class StreamStartRequest(BaseModel):
    destinations: List[str] = Field(
        ...,
        min_length=1,
        description='Destination names: "youtube", "twitch", "facebook", "custom".',
    )
    custom_rtmp_url: Optional[str] = Field(
        default=None,
        description='Required when "custom" is in destinations, e.g. '
        '"rtmp://ingest.example.com/live". The key comes from the '
        "CUSTOM_RTMP_KEY env var — never put keys in this body.",
    )
    resolution: str = Field(
        default="720p",
        description=f"One of: {', '.join(RESOLUTIONS)}.",
    )
    director_url: Optional[str] = Field(
        default=None,
        description="Override the director page URL captured by Chromium.",
    )

    @field_validator("destinations")
    @classmethod
    def _known_destinations(cls, value: List[str]) -> List[str]:
        unknown = [d for d in value if d not in KNOWN_DESTINATIONS]
        if unknown:
            raise ValueError(
                f"Unknown destination(s): {', '.join(unknown)}. "
                f"Supported: {', '.join(KNOWN_DESTINATIONS)}."
            )
        if len(set(value)) != len(value):
            raise ValueError("Duplicate destinations are not allowed.")
        return value

    @field_validator("resolution")
    @classmethod
    def _known_resolution(cls, value: str) -> str:
        if value not in RESOLUTIONS:
            raise ValueError(
                f'Unknown resolution "{value}". Supported: {", ".join(RESOLUTIONS)}.'
            )
        return value


class StreamStatusResponse(BaseModel):
    state: str
    started_at: Optional[str] = None
    uptime_seconds: float = 0.0
    destinations_requested: List[str] = []
    destinations_configured: Dict[str, bool] = {}
    resolution: str = "720p"
    fps: int = 30
    director_url: str = ""
    ffmpeg_pid: Optional[int] = None
    ffmpeg_alive: bool = False
    restart_count: int = 0
    last_error: Optional[str] = None


class StreamActionResponse(BaseModel):
    message: str
    state: str
