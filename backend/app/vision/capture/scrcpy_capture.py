# backend/app/vision/capture/scrcpy_capture.py
"""
AEGIS AI - scrcpy Frame Capture

Higher-FPS Android capture path for Phase 14.

How it works: a persistent ``scrcpy`` process encodes the device screen
to H.264 and writes it into a named pipe (FIFO) as an MKV stream.
A reader thread decodes that stream with OpenCV and hands out the
latest frame.

Honest behavior:
- If the ``scrcpy`` binary is missing -> RuntimeError with the exact
  install instruction (never a fake frame).
- If the device is unreachable -> RuntimeError from scrcpy's own error
  output (never a fake frame).
- ``read_frame()`` returns None while no frame has arrived yet.

Requirements (all free):
- scrcpy  (https://github.com/Genymobile/scrcpy)
- OpenCV built with ffmpeg support (opencv-python-headless qualifies)
"""

import os
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Optional

import structlog

# NOTE: cv2 is imported at module top on purpose, so OpenCV's import-time
# initialization happens in the main thread rather than lazily inside a
# worker thread (safer for threaded VideoCapture use).
import cv2

logger = structlog.get_logger("aegis.vision.scrcpy")


class ScrcpyCapture:
    """
    Persistent scrcpy capture session for one Android device.

    Usage:
        cap = ScrcpyCapture(serial="192.168.1.50:5555", max_size=960, max_fps=15)
        cap.start()                       # raises RuntimeError when unavailable
        try:
            while running:
                frame = cap.read_frame()  # numpy BGR array or None
        finally:
            cap.stop()
    """

    def __init__(
        self,
        serial: str,
        max_size: int = 960,
        max_fps: int = 15,
        scrcpy_path: str = "scrcpy",
    ):
        self.serial = serial
        self.max_size = max_size
        self.max_fps = max_fps
        self.scrcpy_path = scrcpy_path

        self._fifo_path: Optional[str] = None
        self._tmpdir: Optional[str] = None
        self._process: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._latest_frame = None
        self._latest_lock = threading.Lock()
        self._frames_read = 0
        self._start_error: Optional[str] = None
        self._log = logger.bind(serial=serial)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # LIFECYCLE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def start(self) -> None:
        """Start the scrcpy session. Raises RuntimeError when unavailable."""
        binary = shutil.which(self.scrcpy_path)
        if binary is None:
            raise RuntimeError(
                f"scrcpy binary not found (looked for '{self.scrcpy_path}'). "
                "Install scrcpy (https://github.com/Genymobile/scrcpy) or set "
                "AEGIS_CAPTURE_BACKEND=adb_poll."
            )
        if not self.serial:
            raise RuntimeError(
                "No device serial configured. Set AEGIS_ADB_SERIAL, e.g. "
                "AEGIS_ADB_SERIAL=192.168.1.50:5555"
            )

        self._tmpdir = tempfile.mkdtemp(prefix="aegis_scrcpy_")
        self._fifo_path = os.path.join(self._tmpdir, "video.mkv")
        os.mkfifo(self._fifo_path)

        cmd = [
            binary,
            "-s", self.serial,
            "--no-display",
            "--no-audio",
            "--max-size", str(self.max_size),
            "--max-fps", str(self.max_fps),
            "--video-codec=h264",
            "--record-format=mkv",
            "--record", self._fifo_path,
        ]
        self._log.info("Starting scrcpy", max_size=self.max_size, max_fps=self.max_fps)

        # Start the reader thread first so scrcpy never blocks on the FIFO.
        self._stop_event.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop, name=f"scrcpy-reader-{self.serial}", daemon=True
        )
        self._reader_thread.start()

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            self.stop()
            raise RuntimeError(f"Failed to launch scrcpy: {exc}") from exc

        # Give scrcpy a moment; fail fast with its own stderr when it exits.
        time.sleep(2.0)
        if self._process.poll() is not None:
            _, stderr = self._process.communicate(timeout=5)
            err = stderr.decode(errors="replace").strip()[-2000:]
            self.stop()
            raise RuntimeError(
                f"scrcpy exited immediately for device '{self.serial}'. "
                f"Is the device connected (adb devices)? scrcpy said: {err}"
            )

    def stop(self) -> None:
        """Stop the session and clean up. Safe to call multiple times."""
        self._stop_event.set()

        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=5)
            self._reader_thread = None

        if self._fifo_path and os.path.exists(self._fifo_path):
            try:
                os.unlink(self._fifo_path)
            except OSError:
                pass
            self._fifo_path = None

        if self._tmpdir and os.path.isdir(self._tmpdir):
            try:
                os.rmdir(self._tmpdir)
            except OSError:
                pass
            self._tmpdir = None

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FRAME ACCESS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def read_frame(self):
        """
        Return the latest decoded BGR frame, or None when no frame has
        arrived yet. Non-blocking.
        """
        with self._latest_lock:
            frame = self._latest_frame
            return None if frame is None else frame.copy()

    @property
    def frames_read(self) -> int:
        return self._frames_read

    @property
    def is_alive(self) -> bool:
        """True while the scrcpy process is still running."""
        return self._process is not None and self._process.poll() is None

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # INTERNALS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _reader_loop(self) -> None:
        """Decode the MKV stream from the FIFO (reader thread)."""
        cap = None
        try:
            cap = cv2.VideoCapture(self._fifo_path)
            if not cap.isOpened():
                self._log.error("Could not open scrcpy video stream from FIFO")
                return

            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    if self._stop_event.is_set():
                        break
                    # Stream hiccup: brief pause, then keep trying.
                    time.sleep(0.05)
                    continue
                with self._latest_lock:
                    self._latest_frame = frame
                self._frames_read += 1
        except Exception as exc:  # never kill the process on decode errors
            self._log.error("scrcpy reader error", error=str(exc))
        finally:
            try:
                if cap is not None:
                    cap.release()
            except Exception:
                pass
