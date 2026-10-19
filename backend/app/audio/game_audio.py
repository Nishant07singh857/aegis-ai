"""AEGIS AI — game audio capture from the observer phone.

The broadcast needs the actual BGMI game sound. There are exactly two
honest routes — this module probes which one is possible on the current
machine and reports it. It never pretends audio exists.

Route A — scrcpy audio forwarding (preferred, all-digital):
    The observer phone runs BGMI in spectator mode while connected to
    the server over ADB. scrcpy (v2+) can forward device audio on
    Android 11+ (API 30) via the on-device audio capture API:

        scrcpy --no-video --audio-codec=opus --audio-bit-rate=128K \
               --record=/path/to/game_audio.mkv

    The --record file is then an FFmpeg input for the stream mix
    (see docs/AUDIO.md). Whether plain `--record` captures audio-only
    with `--no-video` depends on the scrcpy build — probe() reports the
    scrcpy version so the operator can verify; start() uses the
    documented flags and surfaces scrcpy's own stderr on failure.

Route B — manual 3.5mm cable (always works, needs hardware):
    3.5mm TRRS cable from the observer phone's headphone jack (or a
    USB-C audio dongle) into the server's line-in / a USB audio
    interface. The game audio then appears as an ALSA capture device
    (see `arecord -l`), used as an FFmpeg `-f alsa -i hw:X` input.
    No software on this machine can conjure this device — probe()
    lists ALSA capture devices honestly.

Neither route can be verified in a VM without a phone: probe() says so.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

from .mixer import default_audio_dir

logger = structlog.get_logger("aegis.audio.game_audio")

MIN_ANDROID_API_FOR_AUDIO = 30  # Android 11


def _run(cmd: List[str], timeout: int = 15) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout
        )
        return (
            proc.returncode,
            proc.stdout.decode(errors="replace"),
            proc.stderr.decode(errors="replace"),
        )
    except FileNotFoundError:
        return 127, "", f"binary not found: {cmd[0]}"
    except (subprocess.SubprocessError, OSError) as exc:
        return 1, "", str(exc)


class GameAudioCapture:
    """Probe + launch game-audio capture. Honest: reports what exists."""

    def __init__(self, audio_dir: Path | None = None) -> None:
        self.audio_dir = audio_dir or default_audio_dir()
        self.game_dir = self.audio_dir / "game"
        self.game_dir.mkdir(parents=True, exist_ok=True)
        self._proc: Optional[subprocess.Popen] = None
        self._proc_lock = threading.Lock()
        self._route: str = "none"  # none | scrcpy | manual
        self._record_path: Optional[Path] = None
        self._started_at: Optional[float] = None
        self._last_error: Optional[str] = None
        self._log = logger.bind(component="game_audio")

    # ------------------------------------------------------------------ #
    # probing
    # ------------------------------------------------------------------ #
    def probe(self) -> Dict[str, Any]:
        """Report exactly what game-audio routes are possible right now."""
        adb = shutil.which("adb")
        scrcpy = shutil.which("scrcpy")
        result: Dict[str, Any] = {
            "adb_installed": adb is not None,
            "scrcpy_installed": scrcpy is not None,
            "scrcpy_version": None,
            "adb_devices": [],
            "android_api": None,
            "scrcpy_route_possible": False,
            "alsa_capture_devices": self._alsa_capture_devices(),
            "route": "none",
            "recording": self.is_recording(),
            "notes": [],
        }

        if scrcpy:
            rc, out, _ = _run(["scrcpy", "--version"])
            m = re.search(r"scrcpy\s+([\d.]+)", out)
            result["scrcpy_version"] = m.group(1) if m else out.strip()[:40]

        if adb:
            rc, out, err = _run(["adb", "devices", "-l"])
            devices: List[Dict[str, str]] = []
            if rc == 0:
                for line in out.splitlines()[1:]:
                    line = line.strip()
                    if line and not line.startswith("*"):
                        parts = line.split()
                        if len(parts) >= 2 and parts[1] == "device":
                            devices.append({"serial": parts[0], "detail": " ".join(parts[2:])})
            result["adb_devices"] = devices
            if not devices:
                result["notes"].append(
                    "adb is installed but no device is connected "
                    "(adb devices shows nothing). Connect the observer phone "
                    "over USB/Wi-Fi and authorize the RSA prompt."
                )
            else:
                serial = devices[0]["serial"]
                rc, out, _ = _run(["adb", "-s", serial, "shell", "getprop", "ro.build.version.sdk"])
                try:
                    api = int(out.strip())
                except ValueError:
                    api = None
                result["android_api"] = api
                if api is not None and api < MIN_ANDROID_API_FOR_AUDIO:
                    result["notes"].append(
                        f"Device API level is {api}; scrcpy audio forwarding "
                        f"needs API {MIN_ANDROID_API_FOR_AUDIO}+ (Android 11). "
                        "Use the manual 3.5mm cable route instead."
                    )
        else:
            result["notes"].append(
                "adb is not installed (apt install adb). Without adb neither "
                "the scrcpy route nor device probing can work."
            )

        if (
            result["scrcpy_installed"]
            and result["adb_devices"]
            and (result["android_api"] or 0) >= MIN_ANDROID_API_FOR_AUDIO
        ):
            result["scrcpy_route_possible"] = True
            result["route"] = "scrcpy"

        if result["alsa_capture_devices"]:
            if result["route"] == "none":
                result["route"] = "manual"
            result["notes"].append(
                "ALSA capture device(s) present — the manual 3.5mm cable "
                "route can feed FFmpeg directly (see docs/AUDIO.md)."
            )
        elif result["route"] == "none":
            result["notes"].append(
                "No game audio hardware detected at all: no ADB device and "
                "no ALSA capture device. The broadcast game-audio channel "
                "will be silent until hardware is connected."
            )

        if self.is_recording():
            result["route"] = self._route
        return result

    @staticmethod
    def _alsa_capture_devices() -> List[str]:
        rc, out, _ = _run(["arecord", "-l"])
        if rc != 0:
            return []
        devices = []
        for line in out.splitlines():
            m = re.match(r"card\s+(\d+):\s*(\S+).*device\s+(\d+):", line)
            if m:
                devices.append(f"hw:{m.group(1)},{m.group(3)} ({m.group(2)})")
        return devices

    # ------------------------------------------------------------------ #
    # capture control
    # ------------------------------------------------------------------ #
    def is_recording(self) -> bool:
        with self._proc_lock:
            return self._proc is not None and self._proc.poll() is None

    def start_scrcpy(self) -> Dict[str, Any]:
        """Launch scrcpy audio-forward recording. Fails loudly, never fakes."""
        probe = self.probe()
        if not probe["scrcpy_route_possible"]:
            reasons = "; ".join(probe["notes"]) or "scrcpy route not possible"
            self._last_error = reasons
            return {"recording": False, "route": "none", "error": reasons}
        with self._proc_lock:
            if self._proc is not None and self._proc.poll() is None:
                return {
                    "recording": True,
                    "route": "scrcpy",
                    "record_path": str(self._record_path),
                    "already": True,
                }
            ts = time.strftime("%Y%m%d-%H%M%S")
            self._record_path = self.game_dir / f"game_audio_{ts}.mkv"
            cmd = [
                "scrcpy",
                "--no-video",
                "--no-window",
                "--audio-codec=opus",
                "--audio-bit-rate=128K",
                f"--record={self._record_path}",
            ]
            self._log.info("starting scrcpy audio capture", cmd=" ".join(cmd))
            try:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
                )
            except OSError as exc:
                self._last_error = f"failed to launch scrcpy: {exc}"
                return {"recording": False, "route": "none", "error": self._last_error}
            # Give scrcpy a moment: an immediate exit means the flags/device failed.
            time.sleep(2.0)
            rc = self._proc.poll()
            if rc is not None:
                err = ""
                try:
                    _, err = self._proc.communicate(timeout=3)
                except Exception:
                    pass
                self._proc = None
                self._last_error = (
                    f"scrcpy exited immediately (rc={rc}). "
                    f"{err.strip()[-500:]} "
                    "Check the scrcpy build supports --no-video audio "
                    "recording on this device (see docs/AUDIO.md)."
                )
                self._log.error("scrcpy audio capture failed", error=self._last_error)
                return {"recording": False, "route": "none", "error": self._last_error}
            self._route = "scrcpy"
            self._started_at = time.time()
            return {
                "recording": True,
                "route": "scrcpy",
                "record_path": str(self._record_path),
                "note": (
                    "Recording device audio to MKV. Feed it to the stream "
                    "mix with the FFmpeg recipe in docs/AUDIO.md."
                ),
            }

    def stop(self) -> Dict[str, Any]:
        with self._proc_lock:
            proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        was = self._route
        self._route = "none"
        self._log.info("game audio capture stopped", route=was)
        return {"recording": False, "route": "none"}

    def status(self) -> Dict[str, Any]:
        info = self.probe()
        info["started_at"] = self._started_at
        info["record_path"] = str(self._record_path) if self._record_path else None
        info["last_error"] = self._last_error
        return info


_capture: Optional[GameAudioCapture] = None
_capture_lock = threading.Lock()


def get_game_audio_capture() -> GameAudioCapture:
    """Module-level singleton shared by the API router."""
    global _capture
    with _capture_lock:
        if _capture is None:
            _capture = GameAudioCapture()
        return _capture
