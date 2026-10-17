"""AEGIS AI — automated live-streaming package (Phase 5).

Streams the director page (e.g. ``/broadcast/output``) to one or more RTMP
destinations (YouTube, Twitch, Facebook Live, custom RTMP) via a
headless-Chromium-under-Xvfb → FFmpeg x11grab → libx264 → tee pipeline.

Public surface:
    StreamService, StreamConfig, StreamState       — pipeline lifecycle
    build_destination_urls, DestinationError      — RTMP URL builders

Security: stream keys are read ONLY from environment variables at call time
(``YOUTUBE_STREAM_KEY``, ``TWITCH_STREAM_KEY``, ``FACEBOOK_STREAM_KEY``,
``CUSTOM_RTMP_KEY``). Keys are never hardcoded, never logged, and never
returned by any API — callers only ever see ``*_configured: true/false``.
"""

from .destinations import (
    DestinationError,
    build_destination_urls,
    custom_url,
    facebook_url,
    twitch_url,
    youtube_url,
)
from .service import StreamConfig, StreamService, StreamState, get_stream_service

__all__ = [
    "DestinationError",
    "StreamConfig",
    "StreamService",
    "StreamState",
    "build_destination_urls",
    "custom_url",
    "facebook_url",
    "get_stream_service",
    "twitch_url",
    "youtube_url",
]
