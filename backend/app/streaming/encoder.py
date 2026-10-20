# backend/app/streaming/encoder.py
"""
Encoder selection: NVENC when a GPU is really present (Phase 22 / W3).

Rule: ``h264_nvenc`` is selected ONLY when both probes pass —

1. ``ffmpeg -encoders`` lists ``h264_nvenc``, AND
2. ``nvidia-smi`` runs successfully (a real NVIDIA GPU is present).

Otherwise the encoder is ``libx264`` (software), exactly as before.
Config override via ``VIDEO_ENCODER`` env: ``auto`` (default, probe),
``nvenc`` (force NVENC — but still falls back with a warning when the
probes fail; forcing must never produce a dead stream), ``libx264``.

NVENC does NOT accept x264 ``-preset`` names: FFmpeg's h264_nvenc
rejects e.g. ``veryfast`` at option-parse time (verified by the W4 QA
gate). Callers that select NVENC must therefore map the preset —
both current callers (the highlight renderer and StreamService) use
``p4`` for NVENC and pass the configured x264 preset through only on
the libx264 path.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Dict, Optional

LIBX264 = "libx264"
NVENC = "h264_nvenc"


def _ffmpeg_has_nvenc() -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "h264_nvenc" in (proc.stdout or "")


def _gpu_present() -> bool:
    smi = shutil.which("nvidia-smi")
    if not smi:
        return False
    try:
        proc = subprocess.run(
            [smi, "-L"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and "GPU" in (proc.stdout or "")


def detect_encoder(configured: Optional[str] = None) -> Dict[str, Any]:
    """Probe + select the video encoder.

    Returns ``{"encoder": ..., "nvenc_available": bool,
    "gpu_present": bool, "configured": str, "reason": str}``.
    """
    mode = (configured or os.environ.get("VIDEO_ENCODER", "auto")).strip().lower()
    gpu = _gpu_present()
    nvenc = _ffmpeg_has_nvenc()
    available = bool(gpu and nvenc)

    if mode == "libx264":
        encoder, reason = LIBX264, "configured libx264"
    elif mode == "nvenc":
        if available:
            encoder, reason = NVENC, "configured nvenc; GPU + encoder probe passed"
        else:
            encoder = LIBX264
            reason = (
                "configured nvenc but probe failed "
                f"(gpu_present={gpu}, ffmpeg_nvenc={nvenc}); fell back to libx264"
            )
    else:  # auto
        if available:
            encoder, reason = NVENC, "auto: GPU present and ffmpeg has h264_nvenc"
        else:
            encoder = LIBX264
            reason = (
                "auto: NVENC unavailable "
                f"(gpu_present={gpu}, ffmpeg_nvenc={nvenc}); using libx264"
            )
    return {
        "encoder": encoder,
        "nvenc_available": available,
        "gpu_present": gpu,
        "ffmpeg_has_nvenc": nvenc,
        "configured": mode,
        "reason": reason,
    }


def selected_encoder(configured: Optional[str] = None) -> str:
    """Just the encoder name for command builders."""
    return detect_encoder(configured)["encoder"]
