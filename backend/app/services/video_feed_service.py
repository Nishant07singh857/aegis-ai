# backend/app/services/video_feed_service.py
"""
AEGIS AI - Game video stream composition (Phase 17 W2).

W2 owns everything that COMPOSES or STREAMS the observer feeds; the
Phase 17 (W4) ``MultiDeviceCaptureManager`` (``app/vision/capture/
multi_device.py``) owns capture and the slot map. This module never
creates FrameGrabber sources directly — it asks the manager for frames.

Contents:
  * simulator test-pattern frames — generated, always watermarked
    "SIMULATED FEED", never real footage;
  * honest "NO FEED" slate frames served mid-stream when a slot loses
    its device (labelled, never a fake game image);
  * the MJPEG multipart chunk encoder shared by the /video/feed/{slot}
    endpoint.
"""

from __future__ import annotations

import os
import time
from typing import Dict, Tuple

import cv2
import numpy as np
import structlog

log = structlog.get_logger("aegis.video.feed")

MJPEG_BOUNDARY = "aegisframe"

# Test pattern / slate resolution. 960x540 keeps MJPEG bandwidth modest
# for the headless capture page while staying readable at 1080p.
SIM_WIDTH, SIM_HEIGHT = 960, 540
SLATE_WIDTH, SLATE_HEIGHT = 960, 540

_SIMULATOR_ENV = "AEGIS_VIDEO_SIMULATOR"


def simulator_enabled() -> bool:
    """Global simulator switch (env); the endpoint also takes ?sim=1."""
    return (os.getenv(_SIMULATOR_ENV, "") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def mjpeg_chunk(jpeg: bytes) -> bytes:
    """Wrap one JPEG frame as a multipart/x-mixed-replace chunk."""
    return (
        f"--{MJPEG_BOUNDARY}\r\n"
        "Content-Type: image/jpeg\r\n"
        f"Content-Length: {len(jpeg)}\r\n\r\n"
    ).encode("ascii") + jpeg + b"\r\n"


def render_simulator_frame(slot: int, quality: int = 75) -> bytes:
    """Generate a watermarked test-pattern frame for simulator mode.

    Always labelled "SIMULATED FEED" + "TEST PATTERN, NOT A REAL GAME";
    the sweep bar moves so the stream is visibly live, not a still.
    """
    w, h = SIM_WIDTH, SIM_HEIGHT
    img = np.zeros((h, w, 3), dtype=np.uint8)
    bars = [
        (192, 192, 192),
        (192, 192, 0),
        (0, 192, 192),
        (0, 192, 0),
        (192, 0, 192),
        (192, 0, 0),
        (0, 0, 192),
    ]
    bw = w // len(bars)
    for i, color in enumerate(bars):
        img[:, i * bw:(i + 1) * bw] = color
    # Moving sweep — the stream is alive, not a frozen still.
    sweep_x = int((time.time() * 120) % (w + 200)) - 100
    cv2.rectangle(img, (sweep_x, 0), (sweep_x + 60, h), (255, 255, 255), -1)
    # Dark plate behind the text for readability.
    cv2.rectangle(img, (0, h // 2 - 90), (w, h // 2 + 90), (0, 0, 0), -1)
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(
        img,
        "SIMULATED FEED",
        (w // 2 - 300, h // 2 - 10),
        font,
        1.6,
        (0, 240, 255),
        4,
        cv2.LINE_AA,
    )
    cv2.putText(
        img,
        f"CAM SLOT {slot}  -  TEST PATTERN, NOT A REAL GAME",
        (w // 2 - 300, h // 2 + 40),
        font,
        0.7,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        img,
        time.strftime("%Y-%m-%d %H:%M:%S"),
        (w // 2 - 300, h // 2 + 75),
        font,
        0.6,
        (160, 160, 160),
        1,
        cv2.LINE_AA,
    )
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""


_slate_cache: Dict[Tuple[int, str, int], bytes] = {}


def render_no_feed_slate(slot: int, reason: str, quality: int = 75) -> bytes:
    """Honest slate served mid-stream when a slot has no live frame.

    Never a fake game image: it says NO FEED and why, so the output
    page cannot silently show stale or invented footage.
    """
    key = (slot, reason, quality)
    cached = _slate_cache.get(key)
    if cached is not None:
        return cached
    w, h = SLATE_WIDTH, SLATE_HEIGHT
    img = np.full((h, w, 3), (12, 14, 20), dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(
        img,
        "NO FEED",
        (w // 2 - 130, h // 2 - 30),
        font,
        2.2,
        (255, 80, 80),
        5,
        cv2.LINE_AA,
    )
    cv2.putText(
        img,
        f"CAM SLOT {slot} - connect observer device",
        (w // 2 - 250, h // 2 + 25),
        font,
        0.7,
        (220, 220, 220),
        2,
        cv2.LINE_AA,
    )
    for i, line in enumerate([reason[i:i + 62] for i in range(0, len(reason), 62)][:2]):
        cv2.putText(
            img,
            line,
            (w // 2 - 280, h // 2 + 60 + i * 26),
            font,
            0.55,
            (140, 140, 140),
            1,
            cv2.LINE_AA,
        )
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    jpeg = buf.tobytes() if ok else b""
    if len(_slate_cache) > 64:
        _slate_cache.clear()
    _slate_cache[key] = jpeg
    return jpeg
