# backend/app/services/observer_pilot/executor.py
"""Phase 29 (W3) — POV tap executor: verified ADB taps + kill-switch.

The executor is the ONE choke point through which every pilot tap
flows (rule decisions via :meth:`POVTapExecutor.execute`, manual
overrides via :meth:`POVTapExecutor.execute_target` — same verified
path, no side door). It turns a target roster index into ONE ADB tap
on the feed's own phone, then PROVES the camera moved by re-reading
the spectated index from fresh frames. A tap that cannot be verified
is reported as exactly that — never as a success.

Flow (exact, both entry points):

  1. Kill-switch: ``control.allows(slot)`` False ->
     ``blocked_kill_switch``, ZERO adb calls. Checked first, before
     any resolver/provider/reader call, so a flipped switch can never
     be raced by work already in flight at this layer.
  2. Device: ``device_resolver(slot, source)`` returns None ->
     ``no_device``, zero taps. (See the audit note below: no
     production resolver is wired by default.)
  3. Frame: ``frame_provider_for(slot, source)`` returns None (or a
     frame with no readable shape) -> ``no_frame``, zero taps. The
     frame's shape supplies (width, height) for calibration.
  4. Calibration: ``TapCalibration.resolve(serial, width, height)``
     -> tap-point fractions + the honest ``calibrated`` flag, which
     is stamped on the result. Target index outside the resolved
     profile -> ``failed_stay`` with zero taps (tapping a point that
     does not exist would be a guess).
  5. Tap via ``adb.tap(serial, x, y)``, then VERIFY: poll fresh frames
     for up to ``verify_timeout_s`` (one poll every ~``verify_poll_s``,
     defaults 2.0 s / 0.4 s); ``spectated_reader(frame)`` equal to the
     target -> ``verified``. A reader result of None is "not yet"
     (identity not readable on that frame), never a failure by
     itself — polling continues to the end of the window.
  6. Timeout without the target: exactly ONE retry tap, then a second
     full verification window. Still not the target -> ``failed_stay``
     with ``taps_sent == 2`` and ``verified_index`` set to whatever
     was actually last read (which may be None). A third tap is NEVER
     sent: two misses mean the layout/calibration/identity premise is
     wrong, and more taps would be random poking at a live phone.

The executor touches NO database and keeps NO history: it returns one
:class:`TapResult` per call (W4 journals it) and logs the outcome with
its reason on structlog.

AUDIT — slot -> serial binding (checked 2026-10-22):
  The binding EXISTS, in exactly one place:
  ``MultiDeviceCaptureManager`` (``app.vision.capture.multi_device``)
  keeps ``SlotFeed(slot_number, serial, source_id=
  "aegis-observer-slot-NN")`` per slot, reconciled from ``adb devices``
  by its supervisor. It is NOT exposed as a synchronous resolver to
  the pilot/monitor layer: ``MultiFeedMonitorService``'s
  ``FeedSourceSpec`` carries only (slot, source_id, frame_provider) —
  ``build_multi_feed_monitor`` reads the manager's ``_slots`` only to
  map source -> slot, and drops the serial. ``DeviceManager``
  (``capture.device_manager``) has a slot + serial_number model but no
  live wiring into capture or the monitor, and ``VisionCaptureService``
  (``vision.runtime``) knows a single ``adb_serial`` for its one
  primary source, not a per-slot map. ``feed_adapter`` has no serial
  binding at all.

  Therefore the executor takes ``device_resolver`` as an INJECTED
  callable and its production default is a resolver that returns None
  for every slot (:func:`no_device_resolver`): until W4 explicitly
  binds a real resolver (e.g. :func:`device_resolver_from_multi_device_manager`),
  every execution honestly ends ``no_device`` instead of tapping a
  guessed phone.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Set, Tuple

import structlog

from .config import ExecutorConfig, TapCalibration
from .rules import POVDecision

logger = structlog.get_logger("aegis.observer_pilot.executor")

#: Injected callables (see the module docstring). All are synchronous
#: in the production wiring; awaitable results are also accepted so
#: tests / future async wirings do not need an adapter shim.
DeviceResolver = Callable[[int, str], Optional[str]]
FrameProviderFor = Callable[[int, str], Optional[Any]]
SpectatedReader = Callable[[Any], Optional[int]]

#: TapResult.status values (stable strings — W4 journals them).
STATUS_VERIFIED = "verified"
STATUS_FAILED_STAY = "failed_stay"
STATUS_BLOCKED_KILL_SWITCH = "blocked_kill_switch"
STATUS_NO_DEVICE = "no_device"
STATUS_NO_FRAME = "no_frame"
#: Reserved for a verification read whose identity never resolved
#: inside the window. The current flow reports that outcome as
#: ``failed_stay`` with ``verified_index=None`` (the stay IS the
#: result; the unknown identity is its reason) — this constant names
#: the value so status consumers can recognise the vocabulary.
STATUS_IDENTITY_UNKNOWN = "identity_unknown"


def no_device_resolver(slot_number: int, source_id: str) -> Optional[str]:
    """Production-default device resolver: binds NOTHING.

    No synchronous slot -> serial resolver is exposed by the capture
    layer today (see the audit in the module docstring), so the
    honest default is to resolve no device at all — the executor
    then refuses with ``no_device`` instead of tapping a guess.
    W4 replaces this by injecting a real resolver.
    """
    return None


def device_resolver_from_multi_device_manager(manager: Any) -> DeviceResolver:
    """Build a resolver over a ``MultiDeviceCaptureManager``.

    Reads the manager's slot map (``_slots`` — the ONLY place the
    slot -> serial binding lives today). Slot entries may be
    ``SlotFeed`` objects (``.serial``), dicts (``"serial"``), or plain
    serial strings; an unmapped slot or an empty serial resolves to
    None, never to another slot's device.
    """

    def _resolve(slot_number: int, source_id: str) -> Optional[str]:
        slots = getattr(manager, "_slots", None)
        if slots is None:
            slots = getattr(manager, "slots", None)
        if not isinstance(slots, dict):
            return None
        entry = slots.get(int(slot_number))
        if entry is None:
            return None
        if isinstance(entry, str):
            return entry.strip() or None
        serial = entry.get("serial") if isinstance(entry, dict) else getattr(
            entry, "serial", None
        )
        serial = str(serial).strip() if serial else ""
        return serial or None

    return _resolve


@dataclass
class TapResult:
    """One execution's outcome (brief contract shape, exact field order).

    ``status`` is one of: ``verified`` | ``failed_stay`` |
    ``blocked_kill_switch`` | ``no_device`` | ``no_frame`` |
    ``identity_unknown`` (reserved — see executor.STATUS_*).
    ``calibrated`` is the calibration honesty flag from
    ``TapCalibration.resolve``: False whenever no tap point was
    resolved, and False for the built-in UNCALIBRATED estimates.
    ``verified_index`` is the spectated index actually read during
    verification (the target on ``verified``; the last real read —
    possibly None — on ``failed_stay``; None when no read happened).
    """

    slot_number: int
    source_id: str
    target_index: int
    status: str
    taps_sent: int
    verified_index: Optional[int]
    reason: str
    at: float
    calibrated: bool

    def to_dict(self) -> dict:
        return {
            "slot_number": self.slot_number,
            "source_id": self.source_id,
            "target_index": self.target_index,
            "status": self.status,
            "taps_sent": self.taps_sent,
            "verified_index": self.verified_index,
            "reason": self.reason,
            "at": self.at,
            "calibrated": self.calibrated,
        }


class PilotControl:
    """Pilot kill-switch: one global flag + per-slot disables.

    Thread-safe and deliberately IN-MEMORY only: this is a live
    operator control, not a persistent setting — a service restart
    returns it to ``enabled=True`` with no slots disabled (the
    operator re-flips it if the pilot must stay off). Every tap in
    the system passes ``allows()`` first (see POVTapExecutor).
    """

    def __init__(
        self,
        enabled: bool = True,
        disabled_slots: Optional[Set[int]] = None,
    ) -> None:
        self.enabled: bool = bool(enabled)
        self.disabled_slots: Set[int] = {int(s) for s in (disabled_slots or set())}
        self._lock = threading.Lock()

    def allows(self, slot_number: int) -> bool:
        """True only when the switch is on AND the slot is not disabled."""
        with self._lock:
            return bool(self.enabled and int(slot_number) not in self.disabled_slots)

    def set_enabled(self, enabled: bool) -> bool:
        """Flip the global switch; returns the new state."""
        with self._lock:
            self.enabled = bool(enabled)
            return self.enabled

    def disable_slot(self, slot_number: int) -> None:
        with self._lock:
            self.disabled_slots.add(int(slot_number))

    def enable_slot(self, slot_number: int) -> None:
        with self._lock:
            self.disabled_slots.discard(int(slot_number))

    def snapshot(self) -> dict:
        """Current switch state (status surfaces / W4 journal)."""
        with self._lock:
            return {
                "enabled": bool(self.enabled),
                "disabled_slots": sorted(self.disabled_slots),
            }


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` when it is awaitable, else return it as-is."""
    if inspect.isawaitable(value):
        return await value
    return value


def _frame_image(frame: Any) -> Any:
    """The ndarray inside a frame: a ``CapturedFrame``'s ``.image``,
    or the frame itself when it already is an array-like."""
    image = getattr(frame, "image", None)
    return image if image is not None else frame


def _frame_size(frame: Any) -> Optional[Tuple[int, int]]:
    """(width, height) from a frame's shape, or None when unreadable."""
    shape = getattr(_frame_image(frame), "shape", None)
    if not shape or len(shape) < 2:
        return None
    try:
        height, width = int(shape[0]), int(shape[1])
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _normalize_config(config: Any) -> ExecutorConfig:
    """Accept the executor's config in any of its plausible shapes.

    ``ExecutorConfig`` passes through; a bare ``TapCalibration``
    becomes an ExecutorConfig carrying it (default verify timings);
    None builds the default ExecutorConfig (calibration loaded from
    ``AEGIS_POV_CALIBRATION_PATH``); a duck-typed object contributes
    whichever of (verify_timeout_s, verify_poll_s, calibration) it
    carries. This keeps W4 free to wire whichever object it already
    holds without an adapter, while the executor internally always
    sees the one canonical shape.
    """
    if config is None:
        return ExecutorConfig()
    if isinstance(config, ExecutorConfig):
        return config
    if isinstance(config, TapCalibration):
        return ExecutorConfig(calibration=config)
    kwargs: dict = {}
    for attr in ("verify_timeout_s", "verify_poll_s"):
        value = getattr(config, attr, None)
        if value is not None:
            kwargs[attr] = value
    calibration = getattr(config, "calibration", None)
    if isinstance(calibration, TapCalibration):
        kwargs["calibration"] = calibration
    elif isinstance(config, TapCalibration):  # pragma: no cover - handled above
        kwargs["calibration"] = config
    return ExecutorConfig(**kwargs)


class POVTapExecutor:
    """Verified tap execution for one pilot (all feeds share one
    instance; per-feed identity travels in the decision / arguments).

    Parameters (all collaborators injected):
      ``adb`` — anything with ``async tap(serial, x, y)``
      (production: ``ADBController``).
      ``config`` — an ``ExecutorConfig`` (a bare ``TapCalibration``
      or None are also accepted; see ``_normalize_config``).
      ``control`` — the ``PilotControl`` kill-switch.
      ``device_resolver(slot, source) -> Optional[serial]``;
      None -> :func:`no_device_resolver` (binds nothing, honestly).
      ``frame_provider_for(slot, source) -> Optional[frame]`` — a
      FRESH frame per call (W4 binds the monitor's frame providers);
      None -> a provider that never has a frame.
      ``spectated_reader(frame) -> Optional[int]`` — the spectated
      roster index read from one frame (W4 binds ``POVReader``'s
      highlight read); None -> a reader that never resolves identity.
    """

    #: Hard cap on taps per execution: the first tap + exactly one retry.
    MAX_TAPS = 2

    def __init__(
        self,
        adb: Any,
        config: Any = None,
        control: Optional[PilotControl] = None,
        device_resolver: Optional[DeviceResolver] = None,
        frame_provider_for: Optional[FrameProviderFor] = None,
        spectated_reader: Optional[SpectatedReader] = None,
    ) -> None:
        self.adb = adb
        self.config: ExecutorConfig = _normalize_config(config)
        assert isinstance(self.config.calibration, TapCalibration)  # __post_init__
        self.calibration: TapCalibration = self.config.calibration
        self.control: PilotControl = control if control is not None else PilotControl()
        self.device_resolver: DeviceResolver = device_resolver or no_device_resolver
        self.frame_provider_for: FrameProviderFor = frame_provider_for or (
            lambda slot_number, source_id: None
        )
        self.spectated_reader: SpectatedReader = spectated_reader or (
            lambda frame: None
        )
        self._log = logger.bind(component="pov_tap_executor")

    # ── entry points ─────────────────────────────────────────────

    async def execute(self, decision: POVDecision, now: float) -> TapResult:
        """Execute one rules-engine decision (the verified path)."""
        return await self.execute_target(
            slot_number=int(decision.slot_number),
            source_id=decision.source_id,
            target_index=int(decision.to_index),
            reason=decision.reason,
            now=float(now),
        )

    async def execute_target(
        self,
        slot_number: int,
        source_id: str,
        target_index: int,
        reason: str,
        now: float,
    ) -> TapResult:
        """Execute a tap to ``target_index`` (manual override path).

        Identical verified flow to :meth:`execute` — a manual target
        gets no shortcut around the kill-switch, the device/frame
        checks, or verification. ``reason`` is the caller's reason
        (decision reason / operator note); the result's reason
        appends the execution outcome to it.
        """
        slot_number = int(slot_number)
        target_index = int(target_index)
        at = float(now)
        base_reason = (reason or "").strip()

        def _result(
            status: str,
            taps_sent: int,
            verified_index: Optional[int],
            outcome: str,
            calibrated: bool = False,
        ) -> TapResult:
            full_reason = f"{base_reason} -> {outcome}" if base_reason else outcome
            result = TapResult(
                slot_number=slot_number,
                source_id=source_id,
                target_index=target_index,
                status=status,
                taps_sent=taps_sent,
                verified_index=verified_index,
                reason=full_reason,
                at=at,
                calibrated=calibrated,
            )
            log = self._log.info if status == STATUS_VERIFIED else self._log.warning
            log(
                "pov_tap_result",
                slot=slot_number,
                source_id=source_id,
                target_index=target_index,
                status=status,
                taps_sent=taps_sent,
                verified_index=verified_index,
                calibrated=calibrated,
                reason=full_reason,
            )
            return result

        # 1) Kill-switch — THE choke point. Before everything else.
        if not self.control.allows(slot_number):
            snap = self.control.snapshot()
            return _result(
                STATUS_BLOCKED_KILL_SWITCH,
                0,
                None,
                "blocked by pilot kill-switch "
                f"(enabled={snap['enabled']}, "
                f"disabled_slots={snap['disabled_slots']}); no tap sent",
            )

        # 2) Device binding.
        try:
            serial = await _maybe_await(
                self.device_resolver(slot_number, source_id)
            )
        except Exception as exc:
            self._log.warning(
                "pov_device_resolver_failed", slot=slot_number,
                source_id=source_id, error=str(exc)[:200],
            )
            serial = None
        if not serial:
            return _result(
                STATUS_NO_DEVICE,
                0,
                None,
                "no device bound to this slot (device_resolver returned "
                "None); no tap sent",
            )
        serial = str(serial)

        # 3) A fresh frame supplies the screen size for calibration.
        frame = await self._fresh_frame(slot_number, source_id)
        if frame is None:
            return _result(
                STATUS_NO_FRAME,
                0,
                None,
                "no frame available for this feed; no tap sent",
            )
        size = _frame_size(frame)
        if size is None:
            return _result(
                STATUS_NO_FRAME,
                0,
                None,
                "frame has no readable shape; no tap sent",
            )
        width, height = size

        # 4) Calibration -> pixel tap point.
        points, calibrated = self.calibration.resolve(serial, width, height)
        if not 0 <= target_index < len(points):
            return _result(
                STATUS_FAILED_STAY,
                0,
                None,
                f"calibration profile for {width}x{height} has "
                f"{len(points)} tap points; target index {target_index} "
                "does not exist; no tap sent",
                calibrated=calibrated,
            )
        x, y = points[target_index].to_pixels(width, height)

        # 5) Tap + verify, with exactly ONE retry.
        taps_sent = 0
        last_read: Optional[int] = None
        for attempt in (1, 2):
            taps_sent += 1
            try:
                await _maybe_await(self.adb.tap(serial, x, y))
            except Exception as exc:
                return _result(
                    STATUS_FAILED_STAY,
                    taps_sent,
                    last_read,
                    f"adb tap failed on attempt {attempt}: "
                    f"{str(exc)[:160]}; staying",
                    calibrated=calibrated,
                )
            verified_index, matched = await self._verify_window(
                slot_number, source_id, target_index
            )
            if verified_index is not None:
                last_read = verified_index
            if matched:
                return _result(
                    STATUS_VERIFIED,
                    taps_sent,
                    target_index,
                    f"verified: spectated index read back as {target_index} "
                    f"after tap {attempt} at ({x}, {y}) on {serial}",
                    calibrated=calibrated,
                )
        return _result(
            STATUS_FAILED_STAY,
            taps_sent,
            last_read,
            "verification failed after 2 taps: spectated index read back "
            f"as {last_read} (target {target_index}); staying — no "
            "further taps sent",
            calibrated=calibrated,
        )

    # ── verification ─────────────────────────────────────────────

    async def _fresh_frame(self, slot_number: int, source_id: str) -> Any:
        """One fresh frame from the injected provider (None on any
        provider failure — a broken provider is 'no frame', honestly)."""
        try:
            return await _maybe_await(
                self.frame_provider_for(slot_number, source_id)
            )
        except Exception as exc:
            self._log.warning(
                "pov_frame_provider_failed", slot=slot_number,
                source_id=source_id, error=str(exc)[:200],
            )
            return None

    async def _read_spectated(self, frame: Any) -> Optional[int]:
        """Spectated index from one frame; any reader failure is an
        unreadable frame (None = identity unknown), never a crash
        and never a guessed index."""
        try:
            result = await _maybe_await(self.spectated_reader(_frame_image(frame)))
            return None if result is None else int(result)
        except Exception as exc:
            self._log.warning("pov_spectated_reader_failed", error=str(exc)[:200])
            return None

    async def _verify_window(
        self, slot_number: int, source_id: str, target_index: int
    ) -> Tuple[Optional[int], bool]:
        """Poll fresh frames for one verification window.

        Returns ``(last_index_read, matched)``. The first poll is
        immediate; a None read (identity unknown on that frame) is
        "not yet" and polling continues until the window closes.
        """
        timeout = max(0.0, float(self.config.verify_timeout_s))
        poll = max(0.001, float(self.config.verify_poll_s))
        deadline = time.monotonic() + timeout
        last_read: Optional[int] = None
        while True:
            frame = await self._fresh_frame(slot_number, source_id)
            if frame is not None:
                index = await self._read_spectated(frame)
                if index is not None:
                    last_read = index
                    if index == target_index:
                        return index, True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return last_read, False
            await asyncio.sleep(min(poll, remaining))


__all__ = [
    "STATUS_BLOCKED_KILL_SWITCH",
    "STATUS_FAILED_STAY",
    "STATUS_IDENTITY_UNKNOWN",
    "STATUS_NO_DEVICE",
    "STATUS_NO_FRAME",
    "STATUS_VERIFIED",
    "DeviceResolver",
    "FrameProviderFor",
    "POVTapExecutor",
    "PilotControl",
    "SpectatedReader",
    "TapResult",
    "device_resolver_from_multi_device_manager",
    "no_device_resolver",
]
