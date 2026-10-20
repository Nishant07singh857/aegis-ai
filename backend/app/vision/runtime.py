# backend/app/vision/runtime.py
"""
AEGIS AI - Vision Pipeline Runtime Service (Phase 14 W4 integration).

Runs the Phase 14 capture -> detect pipeline as a background service and
pushes detector output into the game-feed adapter as NormalizedEvents:

    FrameGrabber (adb_poll / scrcpy / mss)
        -> FramePipeline (preprocess + parallel detector dispatch,
           per-detector failure isolation, honest no-feed skipping)
        -> KillFeedDetector  -> NormalizedEvent kill/knock
        -> ZoneTimerReader    -> NormalizedEvent zone_update (timer-reset edge)
        -> AliveCountReader   -> NormalizedEvent alive_count (on change only)
        -> TeamStatusReader   -> NormalizedEvent team_status (on change only)
        -> HPBarDetector      -> NormalizedEvent hp_update (per-slot, on change)
        -> UtilityDetector    -> NormalizedEvent utility (confirmed + deduped)
        -> MinimapReader      -> NormalizedEvent position_update (on change)
        -> ZoneDetector       -> NormalizedEvent zone_update (minimap geometry,
                                 on change; distinct phase_source from timer)
        -> HealingDetector    -> NormalizedEvent healing (start/completion edges)
        -> LootDetector       -> NormalizedEvent loot (temporally confirmed)
        -> ingest(event)      (the VisionFeedAdapter.ingest -> event bus)

Phase 21 CV wiring notes:
- The heavier Gen-1 detectors do NOT run every frame: CaptureConfig
  detector_intervals gates them in the FramePipeline (hp every 2nd
  frame, utility/healing every 3rd, minimap/zone-geometry/loot every
  5th, staggered), protecting the ~2.3 FPS CPU budget.
- Emission is change/edge-based for every new detector, mirroring the
  Phase 14 readers: first sighting establishes a baseline and emits
  nothing; a detector that finds nothing emits nothing.
- Detectors deliberately NOT wired (they cannot produce truthful
  output in this build -- see Phase 21 report): vehicle_detector and
  player_tracker (need ultralytics, not installed; COCO classes cannot
  identify BGMI vehicles/players), weapon_detector and knock_detector
  and the legacy alive_counter (need PaddleOCR, not installed, and
  duplicate the live Tesseract kill-feed / alive-count readers).

Honest-degradation guarantees (inherited from W1/W2/W3, preserved here):
- No feed (no device, capture backend "none", dropped frames) -> the loop
  idles, emits NOTHING, and reports the no-feed state. Never fake frames,
  never fake events.
- OCR unclear / region unreadable -> the reader reports UNKNOWN and emits
  nothing. The kill-feed detector skips low-confidence lines.
- One detector raising never kills the loop: the FramePipeline isolates
  detector failures per frame, and each ingest is individually guarded.

Color note: the pipeline's CaptureConfig defaults to grayscale
preprocessing, but the Phase 14 detectors need COLOR frames (the team
reader samples HSV, the OCR readers expect BGR). This service therefore
forces grayscale=False on its pipeline config copy and logs that it did.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import threading
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

import structlog

from .capture.config import CaptureConfig, load_capture_config
from .capture.frame_grabber import (
    CapturedFrame,
    FrameGrabber,
    FrameSource,
    SourceState,
    StreamConfig,
)
from .capture.pipeline import FramePipeline, PipelineResult
from .detectors.healing_detector import HealingDetector
from .detectors.hp_bar_detector import HPBarDetector
from .detectors.hud import (
    AliveCountReader,
    TeamStatusReader,
    ZoneTimerReader,
)
from .detectors.killfeed import KillFeedDetector
from .detectors.loot_detector import LootDetector
from .detectors.minimap_reader import MinimapReader
from .detectors.utility_detector import UtilityDetector
from .detectors.zone_detector import ZoneDetector

log = structlog.get_logger("aegis.vision.runtime")

# NormalizedEvent / FeedEventType are imported lazily so this module stays
# importable in minimal environments (schemas.py only needs pydantic, but
# importing app.services at module top would drag SQLAlchemy via
# app.services.__init__ -> match_service).
_schemas = None
_schemas_lock = threading.Lock()


def _default_killfeed_detector():
    """
    Phase-15 wiring: when VISION_MODEL_PATH points at a valid kill-feed
    ONNX model, the detector consults the trained model FIRST for entry
    localisation (hybrid trained+OCR); otherwise it is the plain Phase-14
    OCR heuristic — identical behaviour to before. The import is lazy so
    the vision pipeline never pays for the ml stack at import time.
    """
    from app.ml.vision_wiring import build_killfeed_detector

    return build_killfeed_detector()


def _feed_schemas():
    global _schemas
    if _schemas is not None:
        return _schemas
    # Serialized: two detector worker threads racing the first import can
    # otherwise deadlock on the module import lock. (The service __init__
    # also warms this import eagerly, so the race should never happen here.)
    with _schemas_lock:
        if _schemas is not None:
            return _schemas
        try:
            from app.services.feed_adapter import schemas as mod
        except ImportError:  # pragma: no cover - repo-layout guard
            import importlib.util
            import pathlib
            import sys

            path = (
                pathlib.Path(__file__).resolve().parent.parent
                / "services"
                / "feed_adapter"
                / "schemas.py"
            )
            name = "aegis_feed_adapter_schemas_rt"
            spec = importlib.util.spec_from_file_location(name, str(path))
            if spec is None or spec.loader is None:  # pragma: no cover
                raise
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        _schemas = mod
    return _schemas


#: Async callable the service uses to publish one NormalizedEvent.
#: (The VisionFeedAdapter passes its own ingest().)
IngestFn = Callable[[Any], Awaitable[str]]


class VisionCaptureService:
    """
    Background capture -> detect -> ingest loop.

    Parameters
    ----------
    ingest:
        Async callable receiving one NormalizedEvent and returning the bus
        event id. Raising is allowed -- the loop logs and continues.
    config:
        CaptureConfig (env-loaded when omitted).
    match_id:
        Attached to every emitted event (env AEGIS_VISION_MATCH_ID default).
    observer_slot:
        Phase 22: the observer slot (1..16) this capture device belongs
        to (env AEGIS_VISION_OBSERVER_SLOT default). When set, every
        emitted event payload carries ``observer_slot`` so the
        vision-intelligence subscriber can attribute the event to the
        team that slot observes (observer_slots DB). When unset, events
        carry no slot and team-scoped consumers must drop them — the
        slot is never guessed.
    frame_provider:
        Optional zero-arg callable returning the latest CapturedFrame (or
        None). Defaults to the owned FrameGrabber's latest frame. Tests
        inject synthetic frames here.
    killfeed / alive / zone / team:
        Optional pre-built detector instances (e.g. with a pinned OCR
        engine). Defaults are constructed here.
    hp / utility / minimap / zone_geom / healing / loot:
        Optional pre-built instances of the Phase 21 CV detectors.
        Defaults are constructed here (see the module docstring for
        which Gen-1 detectors are deliberately NOT wired).
    """

    SOURCE_ID = "vision0"

    def __init__(
        self,
        ingest: IngestFn,
        config: Optional[CaptureConfig] = None,
        match_id: Optional[str] = None,
        observer_slot: Optional[int] = None,
        frame_provider: Optional[Callable[[], Optional[CapturedFrame]]] = None,
        killfeed: Optional[KillFeedDetector] = None,
        alive: Optional[AliveCountReader] = None,
        zone: Optional[ZoneTimerReader] = None,
        team: Optional[TeamStatusReader] = None,
        hp: Optional[HPBarDetector] = None,
        utility: Optional[UtilityDetector] = None,
        minimap: Optional[MinimapReader] = None,
        zone_geom: Optional[ZoneDetector] = None,
        healing: Optional[HealingDetector] = None,
        loot: Optional[LootDetector] = None,
    ) -> None:
        self._ingest = ingest
        base_config = config or load_capture_config()
        # The Phase 14 detectors need COLOR frames; never let an env default
        # silently grayscale them (the team reader's HSV sampling and the
        # OCR readers' BGR expectations would break).
        self.config = dataclasses.replace(base_config, grayscale=False)
        if base_config.grayscale:
            log.info(
                "vision_pipeline_forced_color",
                reason="Phase 14 detectors require BGR frames; "
                "AEGIS_CAPTURE_GRAYSCALE is ignored by this service",
            )
        self._match_id = match_id or os.getenv("AEGIS_VISION_MATCH_ID", "").strip() or None
        # Phase 22: observer-slot attribution config (see class docstring).
        if observer_slot is None:
            raw_slot = os.getenv("AEGIS_VISION_OBSERVER_SLOT", "").strip()
            if raw_slot:
                try:
                    observer_slot = int(raw_slot)
                except ValueError:
                    log.warning(
                        "vision_observer_slot_invalid", value=raw_slot,
                        hint="expected an integer slot 1..16; events will "
                        "carry no observer slot",
                    )
                    observer_slot = None
        if observer_slot is not None and not 1 <= int(observer_slot) <= 16:
            log.warning(
                "vision_observer_slot_out_of_range", value=observer_slot,
                hint="slots are 1..16; events will carry no observer slot",
            )
            observer_slot = None
        self._observer_slot = int(observer_slot) if observer_slot is not None else None

        # Warm the lazy feed-adapter schema imports NOW, in this thread.
        # The detectors import schemas lazily on first use; if two worker
        # threads race that first import, CPython's module import lock can
        # deadlock ("deadlock detected by _ModuleLock"). Warming here --
        # before any detector thread runs -- makes later lazy imports
        # plain sys.modules lookups.
        _feed_schemas()

        self._grabber = FrameGrabber()
        self._pipeline = FramePipeline(config=self.config)
        self._killfeed = killfeed or _default_killfeed_detector()
        self._alive = alive or AliveCountReader()
        self._zone = zone or ZoneTimerReader()
        self._team = team or TeamStatusReader()
        self._hp = hp or HPBarDetector()
        self._utility = utility or UtilityDetector()
        self._minimap = minimap or MinimapReader()
        self._zone_geom = zone_geom or ZoneDetector()
        self._healing = healing or HealingDetector()
        self._loot = loot or LootDetector()
        # Phase 14 detectors first (they run every frame), then the
        # heavier Phase 21 CV detectors (frame-skip gated by the
        # pipeline via CaptureConfig.detector_intervals; registration
        # order also staggers their first runs).
        for det in (
            self._killfeed,
            self._alive,
            self._zone,
            self._team,
            self._hp,
            self._utility,
            self._minimap,
            self._zone_geom,
            self._healing,
            self._loot,
        ):
            self._pipeline.register_detector(det)

        # --- Phase 21 emission baselines (change/edge semantics) ---
        # player_id -> (hp_pct, is_knocked); first sighting is a baseline.
        self._hp_state: Dict[str, tuple] = {}
        # Last emitted minimap position signature.
        self._positions_sig: Optional[tuple] = None
        # Last emitted zone-geometry signature.
        self._zone_geom_sig: Optional[tuple] = None
        # utility events: (type, position-bucket) -> last emit time,
        # plus first-sighting candidates for the persistent phenomena
        # (smoke ~25s, molotov fire ~7s), which must be seen on two
        # passes before being reported.
        self._utility_emitted: Dict[tuple, float] = {}
        self._utility_candidates: Dict[tuple, float] = {}
        # healing: heal_type -> active seen in a previous pass;
        # plus a throttle timestamp for progress (non-completion) emits.
        self._heal_active: Dict[str, bool] = {}
        self._last_heal_emit = 0.0
        # loot: temporal confirmation. candidates: [(type, x, y, first_seen)]
        # before an object is reported it must re-appear on a later pass
        # (real death crates persist; noise hits do not).
        self._loot_candidates: List[tuple] = []
        # (type, x, y) -> emit time, to report each episode only once.
        self._loot_emitted: Dict[tuple, float] = {}

        self._frame_provider = frame_provider or (
            lambda: self._grabber.get_latest_frame(self.SOURCE_ID)
        )
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._source_added = False

        # Stats (honest counters, no sampling tricks).
        self._started_at: Optional[float] = None
        self._frames_seen = 0
        self._frames_no_feed = 0
        self._events_emitted = 0
        self._ingest_errors = 0
        self._last_error = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Add the capture source, start grabbing, spawn the loop."""
        if self._running:
            log.warning("vision_service_already_running")
            return
        if self._add_source_from_config():
            # start_capture spawns the grabber's resilient loop; a missing
            # device / bad handle becomes NO_FEED + backoff inside the
            # grabber, never an exception here.
            try:
                await self._grabber.start_capture(self.SOURCE_ID)
            except Exception as exc:  # pragma: no cover - defensive
                self._last_error = str(exc)[:300]
                log.error("vision_capture_start_failed", error=self._last_error)
        self._running = True
        self._started_at = time.time()
        self._task = asyncio.create_task(self._loop(), name="aegis-vision-loop")
        log.info(
            "vision_service_started",
            backend=self.config.backend,
            target_fps=self.config.target_fps,
            match_id=self._match_id,
        )

    async def stop(self) -> None:
        """Stop the loop and the capture source. Idempotent."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        try:
            await self._grabber.stop_all()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("vision_grabber_stop_failed", error=str(exc)[:200])
        try:
            self._pipeline.shutdown()
        except Exception:  # pragma: no cover - defensive
            pass
        log.info("vision_service_stopped")

    @property
    def running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Source wiring
    # ------------------------------------------------------------------

    def _add_source_from_config(self) -> bool:
        """Add the FrameGrabber source matching the capture backend.

        Returns True when a source was added (False for backend "none").
        """
        backend = (self.config.backend or "none").strip().lower()
        if backend == "none":
            # Honest "capture disabled": no source, the loop idles and the
            # status reports no-feed. Never synthesize frames.
            log.warning(
                "vision_capture_disabled",
                hint="set AEGIS_CAPTURE_BACKEND=adb_poll|scrcpy|mss",
            )
            return False
        if backend == "mss":
            source_type = FrameSource.SCREEN_CAPTURE
        else:  # adb_poll | scrcpy (anything else falls back to adb_poll)
            source_type = FrameSource.ADB_DEVICE
            if backend not in ("adb_poll", "scrcpy"):
                log.warning("vision_unknown_backend", backend=backend,
                            fallback="adb_poll")
                backend = "adb_poll"
        cfg = StreamConfig(
            source_id=self.SOURCE_ID,
            source_type=source_type,
            target_fps=max(1, int(self.config.target_fps)),
            capture_backend=backend,
            device_id=self.config.adb_serial,
            adb_path=self.config.adb_path,
            scrcpy_path=self.config.scrcpy_path,
            scrcpy_max_size=self.config.scrcpy_max_size,
            scrcpy_max_fps=self.config.scrcpy_max_fps,
            reconnect_attempts=self.config.max_reconnect_attempts,
            reconnect_delay=self.config.reconnect_delay_s,
            feed_timeout_s=self.config.feed_timeout_s,
        )
        self._grabber.add_source(cfg)
        self._source_added = True
        return True

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        period = 1.0 / max(0.5, float(self.config.target_fps))
        while self._running:
            loop_start = time.perf_counter()
            try:
                frame = self._frame_provider()
                if frame is None or getattr(frame, "image", None) is None:
                    # Honest no-feed: idle, emit nothing.
                    self._frames_no_feed += 1
                    await asyncio.sleep(0.25)
                    continue
                result = await self._pipeline.process_frame(frame)
                self._frames_seen += 1
                for event in self._extract_events(result):
                    try:
                        await self._ingest(event)
                        self._events_emitted += 1
                    except Exception as exc:
                        # Ingest must never silently vanish AND must never
                        # kill the loop: record loudly, keep going.
                        self._ingest_errors += 1
                        self._last_error = str(exc)[:300]
                        log.error(
                            "vision_ingest_failed",
                            error=self._last_error,
                            event_type=getattr(event, "event_type", "?"),
                        )
            except asyncio.CancelledError:
                break
            except Exception as exc:  # the loop itself never dies
                self._last_error = str(exc)[:300]
                log.error("vision_loop_error", error=self._last_error)
            elapsed = time.perf_counter() - loop_start
            await asyncio.sleep(max(0.0, period - elapsed))

    # ------------------------------------------------------------------
    # Event extraction: pipeline outcomes -> NormalizedEvent
    # ------------------------------------------------------------------

    def _extract_events(self, result: PipelineResult) -> List[Any]:
        """Pull NormalizedEvents out of one pipeline pass.

        Only SUCCESS outcomes with real detector output produce events;
        failed/skipped outcomes contribute nothing (no guessing).
        """
        schemas = _feed_schemas()
        events: List[Any] = []

        def _extra(detector_name: str) -> Dict[str, Any]:
            oc = result.get(detector_name)
            if oc is None or oc.status != "success" or not oc.result_dict:
                return {}
            return (oc.result_dict.get("extra") or {})

        # --- kill feed: NormalizedEvents already built by the detector ---
        for dumped in _extra(self._killfeed.name).get("events", []):
            try:
                event = schemas.NormalizedEvent(**dumped)
            except Exception as exc:
                log.warning("vision_bad_killfeed_event", error=str(exc)[:200])
                continue
            events.append(self._with_match_id(event))

        # --- zone timer: reset-edge event embedded by detect() ---
        zone_dump = _extra(self._zone.name).get("zone", {}).get("event")
        if zone_dump:
            try:
                events.append(
                    self._with_match_id(schemas.NormalizedEvent(**zone_dump))
                )
            except Exception as exc:
                log.warning("vision_bad_zone_event", error=str(exc)[:200])

        # --- alive count: change-only update -> ALIVE_COUNT event ---
        alive_change = _extra(self._alive.name).get("alive", {}).get("change")
        if alive_change:
            events.append(
                schemas.NormalizedEvent(
                    event_type=schemas.FeedEventType.ALIVE_COUNT,
                    match_id=self._match_id,
                    payload={
                        "players_alive": alive_change.get("players"),
                        "confidence": alive_change.get("confidence"),
                        "ocr_engine": alive_change.get("engine"),
                        "detector": self._alive.name,
                    },
                )
            )

        # --- team status: change-only update -> TEAM_STATUS event ---
        team_change = _extra(self._team.name).get("team", {}).get("change")
        if team_change:
            events.append(
                schemas.NormalizedEvent(
                    event_type=schemas.FeedEventType.TEAM_STATUS,
                    match_id=self._match_id,
                    payload={
                        "slots": team_change.get("slots"),
                        "alive_count": team_change.get("alive_count"),
                        "confidences": team_change.get("confidences"),
                        "detector": self._team.name,
                    },
                )
            )

        events.extend(self._extract_hp_events(schemas, result))
        events.extend(self._extract_utility_events(schemas, result))
        events.extend(self._extract_minimap_events(schemas, result))
        events.extend(self._extract_zone_geom_events(schemas, result))
        events.extend(self._extract_healing_events(schemas, result))
        events.extend(self._extract_loot_events(schemas, result))

        # Phase 22: stamp the capture device's observer slot so the
        # intelligence subscriber can attribute events to a team. Only
        # when configured — an absent slot stays absent (never guessed).
        if self._observer_slot is not None:
            for event in events:
                if "observer_slot" not in event.payload:
                    event.payload["observer_slot"] = self._observer_slot

        return events

    # ------------------------------------------------------------------
    # Phase 21: Gen-1 CV detector -> NormalizedEvent helpers
    #
    # Shared honesty rules (mirroring the Phase 14 readers): a skipped /
    # failed / not_found outcome emits NOTHING, first sightings are
    # baselines, and payloads carry only what the detector actually
    # reported (no invented player/team attribution -- the Gen-1
    # detectors work in screen space and do not know team identity).
    # ------------------------------------------------------------------

    def _extra_of(self, result: PipelineResult, detector_name: str) -> Dict[str, Any]:
        oc = result.get(detector_name)
        if oc is None or oc.status != "success" or not oc.result_dict:
            return {}
        return (oc.result_dict.get("extra") or {})

    def _detector_status(self, result: PipelineResult, detector_name: str) -> str:
        oc = result.get(detector_name)
        if oc is None or not oc.result_dict:
            return ""
        return str(oc.result_dict.get("status") or "")

    def _extract_hp_events(self, schemas: Any, result: PipelineResult) -> List[Any]:
        """HP bars: one hp_update per screen slot whose HP changed.

        The detector reports positional slots ("player_1" = top HP bar)
        with no team identity, so the payload keeps the slot label
        honestly instead of inventing player/team attribution. First
        sighting of a slot establishes a baseline and emits nothing.
        """
        events: List[Any] = []
        for player in self._extra_of(result, self._hp.name).get("players", []):
            pid = str(player.get("player_id") or "")
            if not pid:
                continue
            hp_pct = player.get("hp_pct")
            knocked = bool(player.get("is_knocked"))
            prev = self._hp_state.get(pid)
            self._hp_state[pid] = (hp_pct, knocked)
            if prev is None:
                continue  # baseline: no event
            prev_pct, prev_knocked = prev
            if prev_pct == hp_pct and prev_knocked == knocked:
                continue
            try:
                delta = round(float(hp_pct) - float(prev_pct), 1)
            except (TypeError, ValueError):
                delta = None
            events.append(
                schemas.NormalizedEvent(
                    event_type=schemas.FeedEventType.HP_UPDATE,
                    match_id=self._match_id,
                    payload={
                        "player_id": pid,
                        "slot_label": player.get("player_name"),
                        "hp": player.get("hp"),
                        "hp_pct": hp_pct,
                        "previous_hp_pct": prev_pct,
                        "hp_delta_pct": delta,
                        "armor": player.get("armor"),
                        "armor_type": player.get("armor_type"),
                        "boost": player.get("boost"),
                        "is_knocked": knocked,
                        "was_knocked": prev_knocked,
                        "confidence": player.get("confidence"),
                        "detector": self._hp.name,
                    },
                )
            )
        return events

    def _extract_utility_events(self, schemas: Any, result: PipelineResult) -> List[Any]:
        """Utility (grenade explosion flash / smoke / molotov fire).

        Smoke and molotov fire persist for seconds, so they are only
        reported after a second sighting in the same ~50px bucket
        (and then deduped per bucket for the phenomenon's duration).
        An explosion flash is transient, so grenades are reported on
        first sighting, deduped for 3s. The detector does not know who
        threw the item, so no player/team is claimed.
        """
        events: List[Any] = []
        now = time.time()
        dedup_window = {"grenade": 3.0, "molotov": 8.0, "smoke": 10.0}
        needs_confirmation = {"smoke", "molotov"}
        for det_event in self._extra_of(result, self._utility.name).get("events", []):
            utype = str(det_event.get("type") or "")
            if not utype:
                continue
            pos = det_event.get("position") or (0.0, 0.0)
            try:
                bucket = (utype, round(float(pos[0]) / 50), round(float(pos[1]) / 50))
            except (TypeError, ValueError, IndexError):
                continue
            window = dedup_window.get(utype, 5.0)
            last = self._utility_emitted.get(bucket, 0.0)
            if now - last < window:
                continue
            if utype in needs_confirmation:
                first_seen = self._utility_candidates.get(bucket)
                if first_seen is None or now - first_seen >= window:
                    self._utility_candidates[bucket] = now
                    continue
                del self._utility_candidates[bucket]
            self._utility_emitted[bucket] = now
            events.append(
                schemas.NormalizedEvent(
                    event_type=schemas.FeedEventType.UTILITY,
                    match_id=self._match_id,
                    payload={
                        "utility_type": utype,
                        "position": [pos[0], pos[1]],
                        "position_space": "frame_pixels",
                        "confidence": det_event.get("confidence"),
                        "detector": self._utility.name,
                    },
                )
            )
        # Opportunistic prune so the dedup map cannot grow unbounded.
        if len(self._utility_emitted) > 500:
            cutoff = now - 60.0
            self._utility_emitted = {
                k: v for k, v in self._utility_emitted.items() if v > cutoff
            }
        return events

    def _extract_minimap_events(self, schemas: Any, result: PipelineResult) -> List[Any]:
        """Minimap: position_update when the marker layout changes.

        The reader always returns SUCCESS (even for an empty minimap),
        so an event is emitted only when at least one marker was found
        AND the layout differs from the last emitted one. Positions are
        minimap-region pixels with detector-internal keys
        ("teammate_0", "enemy_1"...), NOT world coordinates and NOT
        team identities -- the payload says so.
        """
        extra = self._extra_of(result, self._minimap.name)
        positions = extra.get("player_positions") or {}
        if not positions:
            return []
        try:
            sig = tuple(
                sorted(
                    (str(k), round(float(v[0])), round(float(v[1])))
                    for k, v in positions.items()
                )
            )
        except (TypeError, ValueError, IndexError):
            return []
        if sig == self._positions_sig:
            return []
        self._positions_sig = sig
        summary = extra.get("minimap") or {}
        zone = summary.get("zone") or {}
        return [
            schemas.NormalizedEvent(
                event_type=schemas.FeedEventType.POSITION_UPDATE,
                match_id=self._match_id,
                payload={
                    "player_positions": {
                        str(k): [v[0], v[1]] for k, v in positions.items()
                    },
                    "marker_count": len(positions),
                    "vehicle_markers": summary.get("vehicles"),
                    "airdrop_markers": summary.get("air_drops"),
                    "zone": {
                        "safe": zone.get("safe"),
                        "safe_radius": zone.get("safe_radius"),
                        "blue": zone.get("blue"),
                        "blue_radius": zone.get("blue_radius"),
                    },
                    "coordinate_space": "minimap_region_pixels",
                    "confidence": extra.get("confidence"),
                    "detector": self._minimap.name,
                },
            )
        ]

    def _extract_zone_geom_events(self, schemas: Any, result: PipelineResult) -> List[Any]:
        """Zone geometry from the minimap -> zone_update on change only.

        Reuses ZONE_UPDATE (bus "zone_event") with
        phase_source="minimap_geometry" so consumers can tell it apart
        from the timer reader's reset-edge events. The detector returns
        NOT_FOUND (zeroed payload) when no circle was actually seen --
        those passes emit nothing.
        """
        if self._detector_status(result, self._zone_geom.name) != "success":
            return []
        zone = self._extra_of(result, self._zone_geom.name).get("zone") or {}
        sig = (
            zone.get("phase"),
            zone.get("safe_center"),
            round(float(zone.get("safe_radius") or 0), 1),
        )
        if sig == self._zone_geom_sig or sig[2] <= 0:
            return []
        self._zone_geom_sig = sig
        return [
            schemas.NormalizedEvent(
                event_type=schemas.FeedEventType.ZONE_UPDATE,
                match_id=self._match_id,
                payload={
                    "zone_phase": zone.get("phase"),
                    "phase_source": "minimap_geometry",
                    "safe_center": list(zone.get("safe_center") or (0, 0)),
                    "safe_radius": zone.get("safe_radius"),
                    "blue_center": list(zone.get("blue_center") or (0, 0)),
                    "is_shrinking": zone.get("is_shrinking"),
                    "zone_damage": zone.get("damage"),
                    "confidence": zone.get("confidence"),
                    "detector": self._zone_geom.name,
                },
            )
        ]

    def _extract_healing_events(self, schemas: Any, result: PipelineResult) -> List[Any]:
        """Healing: edges only -- a heal 'started' sighting (throttled)
        and a 'completed' event, and only when that heal type was seen
        ACTIVE on an earlier pass (a lone bright frame is not a heal).
        """
        events: List[Any] = []
        now = time.time()
        for det_event in self._extra_of(result, self._healing.name).get("events", []):
            heal_type = str(det_event.get("type") or "")
            if not heal_type:
                continue
            if det_event.get("cancelled"):
                self._heal_active[heal_type] = False
                continue
            if det_event.get("completed"):
                if self._heal_active.get(heal_type):
                    self._heal_active[heal_type] = False
                    self._last_heal_emit = now
                    events.append(
                        schemas.NormalizedEvent(
                            event_type=schemas.FeedEventType.HEALING,
                            match_id=self._match_id,
                            payload={
                                "heal_type": heal_type,
                                "progress": det_event.get("progress"),
                                "completed": True,
                                "confidence": det_event.get("confidence"),
                                "detector": self._healing.name,
                            },
                        )
                    )
                continue
            # Active (in-progress) sighting.
            first_sighting = not self._heal_active.get(heal_type)
            self._heal_active[heal_type] = True
            if first_sighting and now - self._last_heal_emit >= 5.0:
                self._last_heal_emit = now
                events.append(
                    schemas.NormalizedEvent(
                        event_type=schemas.FeedEventType.HEALING,
                        match_id=self._match_id,
                        payload={
                            "heal_type": heal_type,
                            "progress": det_event.get("progress"),
                            "completed": False,
                            "confidence": det_event.get("confidence"),
                            "detector": self._healing.name,
                        },
                    )
                )
        return events

    def _extract_loot_events(self, schemas: Any, result: PipelineResult) -> List[Any]:
        """Loot: temporally confirmed episodes only.

        A death-box / air-drop candidate must re-appear within 12px on a
        later pass (within 15s) before it is reported -- real crates
        persist on screen, single-frame colour noise does not. Each
        confirmed episode is reported once.
        """
        events: List[Any] = []
        now = time.time()
        objects = self._extra_of(result, self._loot.name).get("objects", [])
        for obj in objects:
            loot_type = str(obj.get("type") or "")
            pos = obj.get("position") or (0.0, 0.0)
            try:
                x, y = float(pos[0]), float(pos[1])
            except (TypeError, ValueError, IndexError):
                continue
            if not loot_type:
                continue
            # Already reported this episode?
            reported = any(
                t == loot_type
                and abs(ex - x) <= 12
                and abs(ey - y) <= 12
                and now - ts < 15.0
                for (t, ex, ey), ts in self._loot_emitted.items()
            )
            if reported:
                continue
            # Second sighting of a candidate -> confirmed episode.
            match_idx = None
            for idx, (t, cx, cy, ts) in enumerate(self._loot_candidates):
                if (
                    t == loot_type
                    and abs(cx - x) <= 12
                    and abs(cy - y) <= 12
                    and now - ts < 15.0
                ):
                    match_idx = idx
                    break
            if match_idx is None:
                self._loot_candidates.append((loot_type, x, y, now))
                continue
            del self._loot_candidates[match_idx]
            self._loot_emitted[(loot_type, x, y)] = now
            events.append(
                schemas.NormalizedEvent(
                    event_type=schemas.FeedEventType.LOOT,
                    match_id=self._match_id,
                    payload={
                        "loot_type": loot_type,
                        "position": [x, y],
                        "position_space": "frame_pixels",
                        "confidence": obj.get("confidence"),
                        "detector": self._loot.name,
                    },
                )
            )
        # Prune stale bookkeeping.
        self._loot_candidates = [
            c for c in self._loot_candidates if now - c[3] < 15.0
        ]
        if len(self._loot_emitted) > 500:
            self._loot_emitted = {
                k: v for k, v in self._loot_emitted.items() if now - v < 60.0
            }
        return events

    def _with_match_id(self, event: Any) -> Any:
        if self._match_id and not event.match_id:
            event.match_id = self._match_id
        return event

    # ------------------------------------------------------------------
    # Status / diagnostics
    # ------------------------------------------------------------------

    def _ocr_report(self) -> Dict[str, Any]:
        report: Dict[str, Any] = {}
        try:
            report["killfeed_tesseract"] = self._killfeed.ocr_available()
        except Exception:  # pragma: no cover - defensive
            report["killfeed_tesseract"] = False
        for name, reader in (
            ("alive", self._alive),
            ("zone", self._zone),
        ):
            ocr = getattr(reader, "ocr", None)
            report[f"{name}_ocr_engine"] = getattr(ocr, "name", "?")
            try:
                report[f"{name}_ocr_available"] = bool(ocr.available)
            except Exception:  # pragma: no cover - defensive
                report[f"{name}_ocr_available"] = False
        report["team_engine"] = "color_threshold"
        return report

    def status(self) -> Dict[str, Any]:
        """Honest runtime status for operators and GET /feed/vision/status."""
        uptime = (time.time() - self._started_at) if self._started_at else 0.0
        measured_fps = (self._frames_seen / uptime) if uptime > 1e-6 else 0.0
        source_state: Optional[str] = None
        if self._source_added:
            state = self._grabber.get_source_state(self.SOURCE_ID)
            source_state = state.value if isinstance(state, SourceState) else (
                str(state) if state is not None else None
            )
        else:
            source_state = "disabled" if self._running else SourceState.STOPPED.value
        return {
            "running": self._running,
            "backend": self.config.backend,
            "target_fps": self.config.target_fps,
            "measured_fps": round(measured_fps, 2),
            "match_id": self._match_id,
            "observer_slot": self._observer_slot,
            "capture_source_state": source_state,
            "frames_processed": self._frames_seen,
            "frames_no_feed": self._frames_no_feed,
            "events_emitted": self._events_emitted,
            "ingest_errors": self._ingest_errors,
            "last_error": self._last_error,
            "detectors": self._pipeline.detector_names,
            "pipeline": self._pipeline.get_stats(),
            "ocr": self._ocr_report(),
            "uptime_s": round(uptime, 1),
        }
