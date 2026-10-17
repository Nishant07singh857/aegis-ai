"""RTMP destination URL builders for the AEGIS AI streaming pipeline.

Stream keys are read ONLY from environment variables, AT CALL TIME
(never cached, never hardcoded):

    YOUTUBE_STREAM_KEY   → YouTube Live
    TWITCH_STREAM_KEY    → Twitch
    FACEBOOK_STREAM_KEY  → Facebook Live
    CUSTOM_RTMP_KEY      → custom RTMP server (base URL comes from the
                           request body field ``custom_rtmp_url``)

Rules:
  * Keys are never logged and never appear in exceptions/messages —
    error messages name the missing *variable*, not the value.
  * Every builder raises :class:`DestinationError` (a ``ValueError``) when
    the key is missing or empty, or when the custom base URL is invalid.
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

YOUTUBE_RTMP_BASE = "rtmp://a.rtmp.youtube.com/live2"
TWITCH_RTMP_BASE = "rtmp://live.twitch.tv/app"
FACEBOOK_RTMP_BASE = "rtmps://live-api-s.facebook.com:443/rtmp"

YOUTUBE_KEY_ENV = "YOUTUBE_STREAM_KEY"
TWITCH_KEY_ENV = "TWITCH_STREAM_KEY"
FACEBOOK_KEY_ENV = "FACEBOOK_STREAM_KEY"
CUSTOM_KEY_ENV = "CUSTOM_RTMP_KEY"

#: Destination names accepted by the stream API.
KNOWN_DESTINATIONS = ("youtube", "twitch", "facebook", "custom")


class DestinationError(ValueError):
    """Raised when a destination URL cannot be built (missing/invalid key or URL)."""


def _read_key(env_name: str) -> str:
    """Read a stream key from the environment at call time.

    Returns the stripped value, or ``""`` when unset/blank. The value is
    never logged or included in exceptions.
    """
    return (os.environ.get(env_name, "") or "").strip()


def key_present(env_name: str) -> bool:
    """True when the named env var holds a non-blank stream key (no key leak)."""
    return bool(_read_key(env_name))


def youtube_url() -> str:
    """Build the YouTube Live RTMP ingest URL.

    Raises:
        DestinationError: when YOUTUBE_STREAM_KEY is missing or empty.
    """
    key = _read_key(YOUTUBE_KEY_ENV)
    if not key:
        raise DestinationError(
            f"Cannot stream to YouTube: {YOUTUBE_KEY_ENV} is not set or is empty."
        )
    return f"{YOUTUBE_RTMP_BASE}/{key}"


def twitch_url() -> str:
    """Build the Twitch RTMP ingest URL.

    Raises:
        DestinationError: when TWITCH_STREAM_KEY is missing or empty.
    """
    key = _read_key(TWITCH_KEY_ENV)
    if not key:
        raise DestinationError(
            f"Cannot stream to Twitch: {TWITCH_KEY_ENV} is not set or is empty."
        )
    return f"{TWITCH_RTMP_BASE}/{key}"


def facebook_url() -> str:
    """Build the Facebook Live RTMPS ingest URL.

    Raises:
        DestinationError: when FACEBOOK_STREAM_KEY is missing or empty.
    """
    key = _read_key(FACEBOOK_KEY_ENV)
    if not key:
        raise DestinationError(
            f"Cannot stream to Facebook Live: {FACEBOOK_KEY_ENV} is not set or is empty."
        )
    return f"{FACEBOOK_RTMP_BASE}/{key}"


def custom_url(base_url: str) -> str:
    """Build a custom RTMP ingest URL from a user-supplied base URL + env key.

    Args:
        base_url: the RTMP(S) server URL, e.g. ``"rtmp://ingest.example.com/live"``.

    Raises:
        DestinationError: when ``base_url`` is not an rtmp(s) URL, or when
            CUSTOM_RTMP_KEY is missing or empty.
    """
    base = (base_url or "").strip().rstrip("/")
    if not base or not base.lower().startswith(("rtmp://", "rtmps://")):
        raise DestinationError(
            "Cannot stream to custom destination: custom_rtmp_url must be an "
            "rtmp:// or rtmps:// URL."
        )
    key = _read_key(CUSTOM_KEY_ENV)
    if not key:
        raise DestinationError(
            f"Cannot stream to custom destination: {CUSTOM_KEY_ENV} is not set or is empty."
        )
    return f"{base}/{key}"


def build_destination_urls(
    destinations: List[str],
    custom_rtmp_url: Optional[str] = None,
) -> List[Tuple[str, str]]:
    """Build ``(name, ingest_url)`` pairs for the requested destinations.

    Unknown destination names raise :class:`DestinationError`. Missing keys
    raise :class:`DestinationError` naming the missing variable (never the
    key value). Requesting ``"custom"`` without ``custom_rtmp_url`` raises
    :class:`DestinationError`.

    The returned URLs live only in process memory long enough to be handed to
    FFmpeg; they are never logged, stored, or returned by the API.
    """
    if not destinations:
        raise DestinationError("At least one destination is required.")

    built: List[Tuple[str, str]] = []
    for name in destinations:
        if name == "youtube":
            built.append((name, youtube_url()))
        elif name == "twitch":
            built.append((name, twitch_url()))
        elif name == "facebook":
            built.append((name, facebook_url()))
        elif name == "custom":
            if not (custom_rtmp_url or "").strip():
                raise DestinationError(
                    'Cannot stream to custom destination: "custom" was requested '
                    "but no custom_rtmp_url was provided."
                )
            built.append((name, custom_url(custom_rtmp_url)))
        else:
            raise DestinationError(
                f'Unknown destination "{name}". '
                f"Supported destinations: {', '.join(KNOWN_DESTINATIONS)}."
            )
    return built
