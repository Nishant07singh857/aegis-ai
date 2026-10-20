# backend/app/vision/ingest_plugins.py
"""
AEGIS AI - Ingest Plug Points: WebRTC + NDI (Phase 22 / W3, M2).

Formal plug-in points for the two PRD ingest transports that are NOT
implemented in this build. Both plugins honour the system's honesty
contract: ``available()`` is ``False`` and ``grab_frame()`` returns
``None`` (no feed) — they never synthesise frames. When the external
piece arrives (an ``aiortc``-based browser-spectator service for
WebRTC; the proprietary NDI SDK on a venue machine for NDI), the
implementation fills in these classes behind the same interface and
the capture layer picks them up via :func:`get_ingest_plugin`.

Interface contract for a real implementation:

* ``available() -> bool`` — True only when the transport can actually
  deliver frames right now (dependency importable AND source live).
* ``grab_frame()`` — the latest frame as an ``np.ndarray`` (BGR), or
  ``None`` when there is no feed this tick.
* ``status()`` — operator-facing dict; ``implemented`` must stay
  truthful.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger("aegis.vision.ingest_plugins")


class IngestPlugin(ABC):
    """One external ingest transport (WebRTC, NDI, ...)."""

    #: Stable transport name, matches FrameSource values where one exists.
    name: str = "base"

    @abstractmethod
    def available(self) -> bool:
        """True only when frames can actually be delivered."""

    @abstractmethod
    def grab_frame(self) -> Optional[Any]:
        """Latest frame (BGR ndarray) or None for honest no-feed."""

    def status(self) -> Dict[str, Any]:
        return {
            "transport": self.name,
            "implemented": self.available(),
            "available": self.available(),
        }


class WebRTCIngestPlugin(IngestPlugin):
    """PLUG-READY: WebRTC ingest (browser-based spectators).

    Blocker: no product requirement has landed and ``aiortc`` is not a
    dependency of this build. Implementing means: accept an SDP offer
    from the spectator browser, run the aiortc peer connection, and
    surface the remote video track's frames from ``grab_frame()``.
    """

    name = "webrtc"

    def available(self) -> bool:
        return False

    def grab_frame(self) -> Optional[Any]:
        return None  # honest no-feed until implemented

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base.update(
            {
                "implemented": False,
                "blocker": (
                    "aiortc not installed and no browser-spectator "
                    "requirement yet; see module docstring"
                ),
            }
        )
        return base


class NDIIngestPlugin(IngestPlugin):
    """PLUG-READY: NDI ingest (professional venue setups).

    Formalises the pre-existing honest stub: ``FrameGrabber`` raises /
    reports NO_FEED for ``FrameSource.NDI_FEED``. Blocker: NDI is a
    proprietary SDK installed per venue machine; no venue has
    requested it. Implementing means wrapping the NDI receiver behind
    ``grab_frame()`` on a machine where the SDK is installed.
    """

    name = "ndi_feed"

    def available(self) -> bool:
        return False

    def grab_frame(self) -> Optional[Any]:
        return None  # honest no-feed until the NDI SDK is present

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base.update(
            {
                "implemented": False,
                "blocker": (
                    "NDI SDK (proprietary, per-venue install) not present "
                    "in this build; use RTMP/ADB/screen capture instead"
                ),
            }
        )
        return base


_PLUGINS: Dict[str, IngestPlugin] = {
    "webrtc": WebRTCIngestPlugin(),
    "ndi_feed": NDIIngestPlugin(),
}


def get_ingest_plugin(name: str) -> Optional[IngestPlugin]:
    """Return the plug point for a transport name, or None if unknown."""
    return _PLUGINS.get(name)


def ingest_plugin_status() -> Dict[str, Any]:
    """Status of every registered ingest plug point (operator surface)."""
    return {name: plugin.status() for name, plugin in _PLUGINS.items()}
