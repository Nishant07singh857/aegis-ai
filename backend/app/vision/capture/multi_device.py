# backend/app/vision/capture/multi_device.py
"""
AEGIS AI - Multi-Device Capture Manager (Phase 17, W4).

Manages up to 16 observer phones feeding the director: one ADB device
(or scrcpy instance) per observer slot 1-16, each slot bound to the team
assigned in the observer-slot mapping (Phase 12 / Phase 16 DB rows --
labels are READ FROM THE DATABASE, never hardcoded).

This module does NOT reimplement capture: every frame flows through the
Phase 14 ``FrameGrabber`` (``adb_poll`` or ``scrcpy`` backend) and device
discovery goes through the Phase 14 ``ADBController``. This manager adds:

  * deterministic slot <-> device (ADB serial) mapping,
  * per-slot feed status: connected / disconnected / reconnecting / no_feed,
  * supervisor loop with auto-reconnect using the same capped-exponential
    backoff formula as Phase 14 (``FrameGrabber._next_backoff``),
  * team labels pulled from ``ObserverMappingService`` (DB),
  * director "on air" selection, broadcast over the WS fan-out via the
    same listener pattern ``director_service`` uses
    (``app/main.py`` wires ``ConnectionManager.broadcast``),
  * JPEG thumbnails of the latest frame per slot for the director view.

Coordination note (W2): the video layer should NOT talk to the grabber
directly. Read ``GET /api/v1/video/status`` and subscribe to the WS
events ``video.feeds`` / ``video.on_air`` documented below.

Honesty note: nothing here can be tested against 16 real phones in this
VM. The unit tests exercise this manager against a FAKE adb binary
(canned ``devices -l`` + generated PNG screenshots), which is stated
plainly in the test file and in docs/OBSERVER_SETUP.md.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import structlog

from .adb_controller import ADBController
from .frame_grabber import (
    FrameGrabber,
    FrameSource,
    SourceState,
    StreamConfig,
)

logger = structlog.get_logger("aegis.vision.multi_device")

# ────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────

MAX_OBSERVER_SLOTS = 16
"""A tournament runs exactly 16 observer slots (Phase 12 contract)."""

DEFAULT_SUPERVISOR_INTERVAL_S = 10.0
"""How often the supervisor re-discovers devices and refreshes labels."""

DEFAULT_THUMBNAIL_WIDTH = 320
"""Thumbnail width in px for the director wall (16-up view)."""

DEFAULT_DISCOVERY_CACHE_TTL_S = 5.0
"""How long ``adb devices`` results are cached for status reads.

Full ADB discovery costs ~3 getprop subprocesses per device (48 for 16
phones); the director wall polls GET /video/status every ~2s, so status
reads reuse the last discovery for a few seconds instead of hammering
ADB. ``discover_and_map()`` always refreshes the cache immediately.
"""

#: Async listener signature, same as DirectorService.BroadcastListener.
BroadcastListener = Callable[[Dict[str, Any]], Awaitable[None]]

#: Async callable returning {slot_number: (team_id, team_name)}.
#: Wired to ObserverMappingService (DB) by the video router; defaults to
#: empty so slots are honestly reported as "Unassigned" without a DB.
LabelProvider = Callable[[], Awaitable[Dict[int, Tuple[Optional[str], Optional[str]]]]]

#: WS message types this manager broadcasts to its listeners.
WS_FEEDS_TYPE = "video.feeds"
WS_ON_AIR_TYPE = "video.on_air"


# ────────────────────────────────────────────────────────────────
# Status
# ────────────────────────────────────────────────────────────────

class FeedStatus(str, Enum):
    """Per-slot feed status exposed by GET /video/status."""

    CONNECTED = "connected"        # ADB device present, frames flowing
    DISCONNECTED = "disconnected"  # no device assigned, or ADB lost the serial
    RECONNECTING = "reconnecting"  # device seen but capture is (re)connecting
    NO_FEED = "no_feed"            # device present but no frames arriving


def _map_source_state(
    source_state: Optional[SourceState], device_seen: bool, assigned: bool
) -> FeedStatus:
    """Translate Phase 14 grabber state + ADB discovery into FeedStatus."""
    if not assigned:
        return FeedStatus.DISCONNECTED
    if not device_seen:
        return FeedStatus.DISCONNECTED
    if source_state == SourceState.LIVE:
        return FeedStatus.CONNECTED
    if source_state == SourceState.NO_FEED:
        return FeedStatus.NO_FEED
    # CONNECTING / STOPPED / RECONNECTING / None -> trying (again)
    return FeedStatus.RECONNECTING


def _next_backoff(backoff: float) -> float:
    """Exponential backoff capped at 60s.

    Identical formula to Phase 14 ``FrameGrabber._next_backoff`` (kept as a
    module function here so the manager does not reach into the grabber's
    private method).
    """
    return min(60.0, backoff * 1.5 if backoff >= 1.0 else backoff + 1.0)


# ────────────────────────────────────────────────────────────────
# Data
# ────────────────────────────────────────────────────────────────

@dataclass
class SlotFeed:
    """One observer slot's capture assignment."""

    slot_number: int  # 1..16
    serial: str = ""  # ADB serial; "" = no device assigned to this slot
    device_name: str = ""
    model: str = ""
    connection_type: str = ""  # "usb" | "tcpip" (from ADBController)
    team_id: Optional[str] = None
    team_name: Optional[str] = None
    # Backoff state for the supervisor when the device disappears from ADB.
    backoff_s: float = 1.0
    next_check_at: float = 0.0
    consecutive_misses: int = 0

    @property
    def source_id(self) -> str:
        return f"aegis-observer-slot-{self.slot_number:02d}"

    @property
    def display_label(self) -> str:
        """Director-wall label: 'Slot 3 — TeamName' (never hardcoded)."""
        team = self.team_name or "Unassigned"
        return f"Slot {self.slot_number} — {team}"


# ────────────────────────────────────────────────────────────────
# Manager
# ────────────────────────────────────────────────────────────────

class MultiDeviceCaptureManager:
    """Capture manager for the 16 observer feeds.

    One shared :class:`FrameGrabber` holds 16 ``ADB_DEVICE`` sources
    (``source_id = aegis-observer-slot-NN``); one :class:`ADBController`
    handles discovery. The supervisor loop keeps the slot <-> device map
    in sync with ``adb devices`` and refreshes team labels from the DB.
    """

    def __init__(
        self,
        adb_path: str = "adb",
        capture_backend: str = "adb_poll",
        max_slots: int = MAX_OBSERVER_SLOTS,
        supervisor_interval_s: float = DEFAULT_SUPERVISOR_INTERVAL_S,
        thumbnail_width: int = DEFAULT_THUMBNAIL_WIDTH,
        discovery_cache_ttl_s: float = DEFAULT_DISCOVERY_CACHE_TTL_S,
        label_provider: Optional[LabelProvider] = None,
        adb_controller: Optional[ADBController] = None,
        frame_grabber: Optional[FrameGrabber] = None,
    ) -> None:
        self.adb_path = adb_path
        self.capture_backend = capture_backend
        self.max_slots = max_slots
        self.supervisor_interval_s = supervisor_interval_s
        self.thumbnail_width = thumbnail_width
        self.discovery_cache_ttl_s = discovery_cache_ttl_s
        self._seen_cache: Tuple[float, frozenset] = (0.0, frozenset())
        self._label_provider: LabelProvider = (
            label_provider or self._empty_label_provider
        )
        # Dependency injection seams exist for tests (fake adb binary +
        # canned label provider). Production passes nothing.
        self._adb = adb_controller or ADBController(adb_path=adb_path)
        self._grabber = frame_grabber or FrameGrabber()

        self._slots: Dict[int, SlotFeed] = {
            n: SlotFeed(slot_number=n) for n in range(1, max_slots + 1)
        }
        self._on_air_slot: Optional[int] = None
        self._listeners: List[BroadcastListener] = []
        self._lock = asyncio.Lock()
        self._running = False
        self._supervisor_task: Optional[asyncio.Task] = None
        self._log = logger.bind(component="multi_device_capture")

    # ── listeners (same pattern as DirectorService) ──────────────

    def add_listener(self, listener: BroadcastListener) -> None:
        """Register an async callback for video.feeds / video.on_air."""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: BroadcastListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    async def _broadcast(self, message: Dict[str, Any]) -> None:
        for listener in list(self._listeners):
            try:
                await listener(message)
            except Exception as exc:  # a dead listener must not kill capture
                self._log.warning(f"broadcast listener failed: {exc}")

    @staticmethod
    def _utcnow_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    async def _empty_label_provider() -> Dict[int, Tuple[Optional[str], Optional[str]]]:
        return {}

    # ── lifecycle ────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> Dict[str, Any]:
        """Discover devices, map them to slots, start capture + supervisor.

        Idempotent: calling twice keeps existing capture running.
        """
        if self._running:
            self._log.info("capture already running")
            return await self.get_status()

        self._log.info(
            "starting multi-device capture",
            backend=self.capture_backend,
            slots=self.max_slots,
        )
        await self.discover_and_map()
        await self.refresh_labels()
        self._running = True
        self._supervisor_task = asyncio.create_task(self._supervisor_loop())
        await self._broadcast_feeds(reason="capture_started")
        return await self.get_status()

    async def stop(self) -> None:
        """Stop the supervisor and every capture source."""
        self._running = False
        if self._supervisor_task:
            self._supervisor_task.cancel()
            try:
                await self._supervisor_task
            except asyncio.CancelledError:
                pass
            self._supervisor_task = None
        await self._grabber.stop_all()
        self._log.info("multi-device capture stopped")

    # ── discovery & mapping ──────────────────────────────────────

    async def discover_and_map(self) -> Dict[str, Any]:
        """Reconcile slots with ``adb devices``.

        * New serials are assigned to the lowest free slot (stable: existing
          assignments are never shuffled).
        * Serials that vanished keep their slot reserved (cable bump) but
          report DISCONNECTED with a capped-exponential backoff before the
          supervisor stops probing them every tick.
        * A capture source is created + started for every newly mapped slot.
        """
        devices = await self._discover_devices()
        seen_serials = {d.serial for d in devices}
        self._seen_cache = (time.time(), frozenset(seen_serials))
        now = time.time()

        async with self._lock:
            # 1. Mark misses / recoveries for already-mapped slots.
            for slot in self._slots.values():
                if not slot.serial:
                    continue
                if slot.serial in seen_serials:
                    if slot.consecutive_misses:
                        self._log.info(
                            f"slot {slot.slot_number}: device {slot.serial} "
                            "reappeared on ADB"
                        )
                    slot.consecutive_misses = 0
                    slot.backoff_s = 1.0
                else:
                    slot.consecutive_misses += 1
                    slot.backoff_s = _next_backoff(slot.backoff_s)
                    slot.next_check_at = now + slot.backoff_s
                    self._log.warning(
                        f"slot {slot.slot_number}: device {slot.serial} missing "
                        f"from ADB (miss {slot.consecutive_misses}), next "
                        f"probe in {slot.backoff_s:.0f}s"
                    )

            # 2. Map brand-new serials to free slots, lowest slot first.
            assigned = {s.serial for s in self._slots.values() if s.serial}
            free_slots = sorted(
                n for n, s in self._slots.items() if not s.serial
            )
            newly_mapped: List[Dict[str, Any]] = []
            for device in sorted(devices, key=lambda d: d.serial):
                if device.serial in assigned:
                    continue
                if not free_slots:
                    self._log.warning(
                        f"no free observer slot for device {device.serial} "
                        f"({self.max_slots} slots full)"
                    )
                    break
                slot_no = free_slots.pop(0)
                slot = self._slots[slot_no]
                slot.serial = device.serial
                slot.device_name = device.model or device.serial
                slot.model = device.model
                slot.connection_type = getattr(device, "connection_type", "")
                slot.consecutive_misses = 0
                slot.backoff_s = 1.0
                newly_mapped.append(
                    {"slot_number": slot_no, "serial": device.serial}
                )
                self._log.info(
                    f"slot {slot_no} <- device {device.serial}",
                    model=device.model,
                )

        # 3. Ensure a capture source exists + runs for every mapped slot
        #    (outside the lock; start_capture is idempotent per source).
        for info in newly_mapped:
            slot = self._slots[info["slot_number"]]
            self._ensure_source(slot)
            ok = await self._grabber.start_capture(slot.source_id)
            if not ok:
                self._log.error(f"failed to start capture for {slot.source_id}")

        return {
            "devices_seen": len(seen_serials),
            "slots_mapped": sum(1 for s in self._slots.values() if s.serial),
            "newly_mapped": newly_mapped,
        }

    async def _discover_devices(self) -> List[Any]:
        """ADB discovery via the Phase 14 controller (async)."""
        return await self._adb.discover_devices()

    async def _seen_serials(self) -> frozenset:
        """Serials currently on the ADB bus, cached for a few seconds.

        Status reads (director wall polling) must not pay for 48 getprop
        subprocesses on every call; ``discover_and_map()`` bypasses the
        cache and refreshes it.
        """
        now = time.time()
        cached_at, serials = self._seen_cache
        if now - cached_at < self.discovery_cache_ttl_s:
            return serials
        devices = await self._discover_devices()
        serials = frozenset(d.serial for d in devices)
        self._seen_cache = (now, serials)
        return serials

    def _ensure_source(self, slot: SlotFeed) -> None:
        """Register the slot's ADB source with the grabber (idempotent)."""
        if slot.source_id in self._grabber.captures:
            return
        config = StreamConfig(
            source_id=slot.source_id,
            source_type=FrameSource.ADB_DEVICE,
            device_id=slot.serial,
            team_id=slot.team_id or "",
            capture_backend=self.capture_backend,
            adb_path=self.adb_path,
            # Thumbnails + vision need modest resolution; full-HD per phone
            # x16 would drown the server. Keep grabber native, downscale
            # only for thumbnails.
            target_fps=10,
            reconnect_attempts=10**9,  # retry forever; supervisor owns policy
            reconnect_delay=2.0,
            feed_timeout_s=8.0,
        )
        self._grabber.add_source(config)

    # ── labels (DB, never hardcoded) ─────────────────────────────

    async def refresh_labels(self) -> Dict[int, str]:
        """Pull slot -> team labels from the DB via the label provider."""
        try:
            labels = await self._label_provider()
        except Exception as exc:
            self._log.error(f"label provider failed: {exc}")
            return {}
        updated: Dict[int, str] = {}
        async with self._lock:
            for slot_no, (team_id, team_name) in labels.items():
                slot = self._slots.get(slot_no)
                if slot is None:
                    continue
                slot.team_id = team_id
                slot.team_name = team_name
                config = self._grabber.captures.get(slot.source_id)
                if config is not None:
                    config.team_id = team_id or ""
                updated[slot_no] = slot.display_label
        return updated

    # ── on-air selection (drives W2's video layer) ───────────────

    async def set_on_air(
        self, slot_number: int, set_by: str = "director"
    ) -> Dict[str, Any]:
        """Director picks which observer slot is on air.

        Any slot 1..16 may be selected (a disconnected slot can be
        pre-armed); the response always includes the slot's current feed
        status so the director UI can warn honestly. Broadcasts
        ``video.on_air`` to all listeners (WS fan-out via main.py wiring).
        """
        if not 1 <= slot_number <= self.max_slots:
            raise ValueError(
                f"slot_number must be 1..{self.max_slots}, got {slot_number}"
            )
        async with self._lock:
            self._on_air_slot = slot_number
            slot = self._slots[slot_number]
            payload = {
                "slot_number": slot_number,
                "team_id": slot.team_id,
                "team_name": slot.team_name,
                "label": slot.display_label,
                "feed_status": self._slot_status_locked(slot, set()).value,
                "set_by": set_by,
                "at": self._utcnow_iso(),
            }
        await self._broadcast({"type": WS_ON_AIR_TYPE, "data": payload})
        self._log.info(f"on-air -> slot {slot_number}", label=slot.display_label)
        return payload

    def get_on_air(self) -> Optional[int]:
        return self._on_air_slot

    # ── status & thumbnails ──────────────────────────────────────

    def _slot_status_locked(
        self, slot: SlotFeed, seen_serials: set
    ) -> FeedStatus:
        source_state = self._grabber.get_source_state(slot.source_id)
        return _map_source_state(
            source_state,
            device_seen=bool(slot.serial) and slot.serial in seen_serials,
            assigned=bool(slot.serial),
        )

    async def slot_status(self, slot_number: int) -> Dict[str, Any]:
        """Public per-slot status dict (also used by GET /video/status)."""
        slot = self._slots.get(slot_number)
        if slot is None:
            raise ValueError(f"unknown slot {slot_number}")
        seen = await self._seen_serials()
        status = self._slot_status_locked(slot, seen)
        return self._slot_dict(slot, status)

    def _slot_dict(self, slot: SlotFeed, status: FeedStatus) -> Dict[str, Any]:
        stats = self._grabber.get_stats().get("sources", {}).get(
            slot.source_id, {}
        )
        return {
            "slot_number": slot.slot_number,
            "team_id": slot.team_id,
            "team_name": slot.team_name,
            "label": slot.display_label,
            "status": status.value,
            "serial": slot.serial or None,
            "model": slot.model or None,
            "connection_type": slot.connection_type or None,
            "on_air": self._on_air_slot == slot.slot_number,
            "thumbnail_url": (
                f"/api/v1/video/thumbnail/{slot.slot_number}"
                if status == FeedStatus.CONNECTED
                else None
            ),
            "fps_target": stats.get("fps_target"),
            "frames_captured": stats.get("frames_captured", 0),
            "last_frame_age_s": stats.get("last_frame_age_s"),
            "last_error": stats.get("last_error") or None,
            "adb_misses": slot.consecutive_misses,
        }

    async def get_status(self) -> Dict[str, Any]:
        """Full director-wall payload for GET /video/status."""
        seen = await self._seen_serials()
        async with self._lock:
            slots = [
                self._slot_dict(self._slots[n], self._slot_status_locked(self._slots[n], seen))
                for n in range(1, self.max_slots + 1)
            ]
        counts: Dict[str, int] = {}
        for s in slots:
            counts[s["status"]] = counts.get(s["status"], 0) + 1
        return {
            "capture_running": self._running,
            "capture_backend": self.capture_backend,
            "on_air_slot": self._on_air_slot,
            "generated_at": self._utcnow_iso(),
            "counts": counts,
            "slots": slots,
        }

    async def thumbnail_jpeg(self, slot_number: int) -> Optional[bytes]:
        """Latest frame for a slot, downscaled, JPEG-encoded.

        Returns None when there is no live frame -- the router answers 404
        (honest "no feed", never a placeholder image).
        """
        slot = self._slots.get(slot_number)
        if slot is None:
            raise ValueError(f"unknown slot {slot_number}")
        frame = self._grabber.get_latest_frame(slot.source_id)
        if frame is None or frame.image is None:
            return None

        def _encode() -> bytes:
            import cv2

            img = frame.image
            h, w = img.shape[:2]
            if w > self.thumbnail_width:
                scale = self.thumbnail_width / w
                img = cv2.resize(
                    img, (self.thumbnail_width, int(h * scale)),
                    interpolation=cv2.INTER_AREA,
                )
            ok, buf = cv2.imencode(
                ".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70]
            )
            if not ok:
                raise RuntimeError("cv2.imencode failed")
            return bytes(buf)

        return await asyncio.to_thread(_encode)

    async def program_jpeg(
        self, slot_number: int, quality: int = 80
    ) -> Optional[bytes]:
        """Latest frame for a slot at FULL capture resolution, JPEG-encoded.

        Phase 17 W2: the program feed (/video/feed/{slot} MJPEG) needs the
        full game image, not the downscaled director-wall thumbnail.
        Returns None when there is no live frame (router serves an honest
        NO FEED slate, never a placeholder image).
        """
        slot = self._slots.get(slot_number)
        if slot is None:
            raise ValueError(f"unknown slot {slot_number}")
        frame = self._grabber.get_latest_frame(slot.source_id)
        if frame is None or frame.image is None:
            return None

        def _encode() -> bytes:
            import cv2

            ok, buf = cv2.imencode(
                ".jpg",
                frame.image,
                [cv2.IMWRITE_JPEG_QUALITY, max(30, min(95, quality))],
            )
            if not ok:
                raise RuntimeError("cv2.imencode failed")
            return bytes(buf)

        return await asyncio.to_thread(_encode)

    # ── supervisor ───────────────────────────────────────────────

    async def _supervisor_loop(self) -> None:
        """Periodic reconcile: ADB discovery, label refresh, change events."""
        self._log.info("supervisor loop started")
        try:
            while self._running:
                try:
                    before = self._status_signature()
                    await self.discover_and_map()
                    await self.refresh_labels()
                    after = self._status_signature()
                    if before != after:
                        await self._broadcast_feeds(reason="feeds_changed")
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    self._log.error(f"supervisor tick failed: {exc}")
                await asyncio.sleep(self.supervisor_interval_s)
        finally:
            self._log.info("supervisor loop stopped")

    def _status_signature(self) -> Tuple:
        """Cheap change detector: (serial, team, grabber-state) per slot."""
        sig = []
        for n in range(1, self.max_slots + 1):
            slot = self._slots[n]
            state = self._grabber.get_source_state(slot.source_id)
            sig.append(
                (slot.serial, slot.team_id, state.value if state else None)
            )
        return tuple(sig)

    async def _broadcast_feeds(self, reason: str) -> None:
        status = await self.get_status()
        status["reason"] = reason
        await self._broadcast({"type": WS_FEEDS_TYPE, "data": status})


# ────────────────────────────────────────────────────────────────
# W2 coordination contract (read this before touching the video layer)
# ────────────────────────────────────────────────────────────────
# * Source of truth for "which slot is on air": this manager.
#   ``GET /api/v1/video/status`` returns ``on_air_slot`` + per-slot status.
# * WS events on /ws/live (wired in app/main.py lifespan):
#     {"type": "video.on_air", "data": {slot_number, team_name, ...}}
#     {"type": "video.feeds",  "data": {<full status payload>}}
# * Thumbnails: GET /api/v1/video/thumbnail/{slot} (JPEG, 404 = no feed).
# * Do NOT create FrameGrabber sources directly -- ask the manager
#   (or the video router) so slot mapping stays consistent.
