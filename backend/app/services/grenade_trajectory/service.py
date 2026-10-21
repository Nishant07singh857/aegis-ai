# backend/app/services/grenade_trajectory/service.py
"""Grenade trajectory bus consumer (Phase 23, W4).

Subscribes to the event bus and produces ESTIMATED thrower->explosion
arcs (see engine.py and docs/GRENADE_TRAJECTORY.md) — but ONLY when
both endpoints are actually known. The honesty rules are structural,
not cosmetic:

  * No record is ever created without BOTH a thrower position and an
    explosion position, in the SAME declared coordinate space.
  * Positions are never invented, defaulted to (0, 0), or converted
    between coordinate spaces (no honest conversion exists between
    e.g. minimap-region pixels and the analyst schematic space — the
    minimap is a zoomed viewport, not the whole map).
  * When a kill/knock's explosion endpoint comes from the victim's
    position, the record says so: ``position_basis`` is
    ``victim_position_proxy...`` — a grenade kills at blast radius, so
    the victim's spot is a proxy for the blast centre, not the blast
    centre itself.
  * Explosion-only observations (today's production UTILITY events:
    a frame-pixel flash position, thrower unknown) produce NO record.
    They are counted in the stats so the gap is visible, not hidden.

Consumed sources (tolerant of W2/W3 payload evolution — extra keys are
ignored, several equivalent key spellings are accepted):

  * ``kill_event`` / ``knock_event`` whose ``weapon`` indicates a frag
    grenade. Endpoint fields, first match wins:
      thrower:   thrower_position | killer_position | attacker_position
                 | thrower.position (nested dict)
      explosion: explosion_position | blast_position       -> observed
                 victim_position                           -> victim proxy
      fallback:  last-known positions from position tracking
                 (PositionBook, freshness-windowed).
  * ``utility_event`` with utility_type grenade/frag/explosion:
      explosion: position
      thrower:   thrower_position | thrower.position | PositionBook
                 lookup by the event's thrower/player name.
  * ``position_update``: feeds the PositionBook from
    ``positions_by_player`` (named positions, when a feed provides
    them) and ``player_positions`` (marker keys — stored under their
    key names; they can only ever match an event that references the
    same key, never a player name they don't equal).

A coordinate space must be declared (event-level ``coordinate_space``
/ ``position_space``, or per-position ``<key>_space``) and both
endpoints must resolve to the SAME space string. Undeclared or
mismatched spaces produce no record.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

import structlog

from app.core.event_bus import EventBus, EventRecord

from .engine import as_point, estimate_arc
from .store import TrajectoryStore, trajectory_store

logger = structlog.get_logger("aegis.grenade_trajectory")

SUBSCRIBER_ID = "grenade_trajectory"

#: Bus event types this consumer listens to.
CONSUMED_EVENT_TYPES = [
    "kill_event",
    "knock_event",
    "utility_event",
    "position_update",
]

#: utility_type values (normalized) that mean a frag/explosion event.
GRENADE_UTILITY_TYPES = {
    "grenade",
    "fraggrenade",
    "frag",
    "explosion",
    "grenadeexplosion",
    "fraggrenadeexplosion",
}

_THROWER_KEYS = ("thrower_position", "killer_position", "attacker_position")
_EXPLOSION_KEYS = ("explosion_position", "blast_position")
_NON_ALNUM = re.compile(r"[^a-z0-9]")


def _normalize_token(value: Any) -> str:
    return _NON_ALNUM.sub("", str(value or "").lower())


def is_grenade_weapon(weapon: Any) -> bool:
    """True when a kill/knock weapon string indicates a frag grenade.

    Tolerant of W2's spelling ("frag_grenade", "Frag Grenade", ...).
    Deliberately narrow: a molotov or generic "explosive" is not a
    frag-grenade arc source on the kill path.
    """
    token = _normalize_token(weapon)
    if not token or token == "unknown":
        return False
    # Non-lethal throwables are never frag-grenade arc sources, even
    # when their label contains the word "grenade" ("smoke_grenade").
    if any(t in token for t in ("smoke", "molotov", "stun", "flash", "decoy")):
        return False
    return "grenade" in token or token in {"frag", "nade"}


def is_grenade_utility(utility_type: Any) -> bool:
    token = _normalize_token(utility_type)
    return token in GRENADE_UTILITY_TYPES or "grenade" in token


def _env_float(name: str) -> Optional[float]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


@dataclass
class _TrackedPosition:
    point: Tuple[float, float]
    space: str
    ts: float


class PositionBook:
    """Last-known positions by (match_id, name), freshness-windowed.

    Names are whatever key the producing feed used (a player name from
    a named-position feed, or a minimap marker key). A lookup only ever
    returns an entry stored under the EXACT name requested — identities
    are never mapped or guessed across naming schemes.
    """

    def __init__(self, freshness_s: float = 15.0):
        self.freshness_s = freshness_s
        self._entries: Dict[Tuple[str, str], _TrackedPosition] = {}

    def update(
        self,
        match_id: Optional[str],
        name: str,
        point: Tuple[float, float],
        space: str,
        ts: float,
    ) -> None:
        if not name or not space:
            return
        self._entries[(str(match_id or "unknown"), str(name))] = (
            _TrackedPosition(point=point, space=space, ts=ts)
        )
        # Opportunistic bound: tracking maps for a match are small, but
        # never let a pathological feed grow this without limit.
        if len(self._entries) > 4096:
            oldest = sorted(self._entries, key=lambda k: self._entries[k].ts)
            for key in oldest[:1024]:
                del self._entries[key]

    def lookup(
        self, match_id: Optional[str], name: Optional[str], ts: float
    ) -> Optional[_TrackedPosition]:
        if not name:
            return None
        entry = self._entries.get((str(match_id or "unknown"), str(name)))
        if entry is None:
            return None
        age = ts - entry.ts
        # Small positive tolerance for cross-device clock skew.
        if age < -2.0 or age > self.freshness_s:
            return None
        return entry


@dataclass
class ConsumerStats:
    received: int = 0
    produced: int = 0
    explosion_only: int = 0
    skipped_missing_endpoint: int = 0
    skipped_space: int = 0
    skipped_degenerate: int = 0
    handler_errors: int = 0
    by_source: Dict[str, int] = field(default_factory=dict)


class GrenadeTrajectoryService:
    """Event-bus subscriber producing estimated grenade arcs."""

    def __init__(
        self,
        bus: Optional[EventBus] = None,
        store: Optional[TrajectoryStore] = None,
        position_book: Optional[PositionBook] = None,
        units_per_meter: Optional[float] = None,
        clock: Callable[[], float] = time.time,
    ):
        self._bus = bus
        self.store = store if store is not None else trajectory_store
        self.positions = position_book or PositionBook()
        # Calibrated scale (coordinate units per meter) for SI outputs.
        # Unset = uncalibrated: distances stay in coordinate units and
        # no flight time / launch speed is reported. Never guessed.
        self.units_per_meter = (
            units_per_meter
            if units_per_meter is not None
            else _env_float("AEGIS_GRENADE_UNITS_PER_METER")
        )
        self._clock = clock
        self.stats = ConsumerStats()
        self._seen_event_ids: set = set()
        self._started = False
        self._last_error = ""

    # ── lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        """Subscribe to the bus. Idempotent; no-op without a bus."""
        if self._started or self._bus is None:
            return
        self._bus.subscribe(
            subscriber_id=SUBSCRIBER_ID,
            callback=self.handle_record,
            event_types=list(CONSUMED_EVENT_TYPES),
        )
        self._started = True
        logger.info(
            "grenade_trajectory_subscribed",
            event_types=CONSUMED_EVENT_TYPES,
            units_per_meter=self.units_per_meter,
        )

    def stop(self) -> None:
        if not self._started or self._bus is None:
            return
        try:
            self._bus.unsubscribe(SUBSCRIBER_ID)
        except Exception:  # pragma: no cover - defensive
            pass
        self._started = False

    @property
    def running(self) -> bool:
        return self._started

    # ── dispatch ─────────────────────────────────────────────────

    async def handle_record(self, record: EventRecord) -> None:
        """Bus callback: route one event record. Never raises."""
        event = getattr(record, "event", None)
        event_type = getattr(event, "event_type", "") if event else ""
        data = getattr(event, "data", None) if event else None
        if not isinstance(data, dict):
            return
        match_id = getattr(event, "match_id", None) or data.get("match_id")
        ts = self._event_ts(data)
        self.stats.received += 1
        try:
            if event_type == "position_update":
                self._on_position_update(data, match_id, ts)
            elif event_type in ("kill_event", "knock_event"):
                self._on_kill(data, match_id, ts, event_type)
            elif event_type == "utility_event":
                self._on_utility(data, match_id, ts)
        except Exception as exc:
            self.stats.handler_errors += 1
            self._last_error = str(exc)[:300]
            logger.error(
                "grenade_trajectory_handler_error",
                event_type=event_type,
                error=self._last_error,
            )

    def _event_ts(self, data: Dict[str, Any]) -> float:
        raw = data.get("timestamp")
        if isinstance(raw, (int, float)) and raw > 0:
            # Bus payloads carry epoch seconds for feed events but the
            # simulator family uses milliseconds in some payloads; a
            # value beyond ~year-2100-in-seconds is read as ms.
            return float(raw) / 1000.0 if raw > 4_102_444_800 else float(raw)
        return self._clock()

    # ── position extraction helpers ──────────────────────────────

    @staticmethod
    def _space_of(data: Dict[str, Any], key: Optional[str] = None) -> Optional[str]:
        if key:
            specific = data.get(f"{key}_space")
            if specific:
                return str(specific)
        for candidate in ("coordinate_space", "position_space"):
            value = data.get(candidate)
            if value:
                return str(value)
        return None

    def _field_point(
        self, data: Dict[str, Any], keys: Tuple[str, ...]
    ) -> Optional[Tuple[Tuple[float, float], Optional[str], str]]:
        """First present position among ``keys`` (+ nested thrower dict).

        Returns (point, space, key_used) or None.
        """
        for key in keys:
            point = as_point(data.get(key))
            if point is not None:
                return point, self._space_of(data, key), key
        nested = data.get("thrower")
        if isinstance(nested, dict) and "thrower_position" in keys:
            point = as_point(nested.get("position"))
            if point is not None:
                space = nested.get("coordinate_space") or self._space_of(data)
                return point, (str(space) if space else None), "thrower.position"
        return None

    def _resolve_pair(
        self,
        data: Dict[str, Any],
        match_id: Optional[str],
        ts: float,
        *,
        thrower_name: Optional[str],
        victim_name: Optional[str],
        allow_victim_proxy: bool,
        explosion_keys: Tuple[str, ...] = _EXPLOSION_KEYS,
    ) -> Optional[Dict[str, Any]]:
        """Resolve both endpoints or return None (counting the reason).

        Returns {thrower_point, thrower_basis, explosion_point,
        explosion_basis, space}.
        """
        thrower_hit = self._field_point(data, _THROWER_KEYS)
        explosion_hit = self._field_point(data, explosion_keys)
        explosion_basis = "observed_event_field"
        if explosion_hit is None and allow_victim_proxy:
            victim_hit = self._field_point(data, ("victim_position",))
            if victim_hit is not None:
                explosion_hit = victim_hit
                explosion_basis = "victim_position_proxy_event_field"

        thrower_point = thrower_hit[0] if thrower_hit else None
        thrower_space = thrower_hit[1] if thrower_hit else None
        thrower_basis = "event_field" if thrower_hit else None
        explosion_point = explosion_hit[0] if explosion_hit else None
        explosion_space = explosion_hit[1] if explosion_hit else None

        # PositionBook fallbacks (exact-name, freshness-windowed).
        if thrower_point is None and thrower_name:
            tracked = self.positions.lookup(match_id, thrower_name, ts)
            if tracked is not None:
                thrower_point, thrower_space = tracked.point, tracked.space
                thrower_basis = "last_known_position_tracking"
        if explosion_point is None and allow_victim_proxy and victim_name:
            tracked = self.positions.lookup(match_id, victim_name, ts)
            if tracked is not None:
                explosion_point, explosion_space = tracked.point, tracked.space
                explosion_basis = "victim_position_proxy_last_known_tracking"

        if thrower_point is None or explosion_point is None:
            if explosion_point is not None and thrower_point is None:
                self.stats.explosion_only += 1
            self.stats.skipped_missing_endpoint += 1
            return None
        if not thrower_space or not explosion_space or thrower_space != explosion_space:
            self.stats.skipped_space += 1
            logger.info(
                "grenade_trajectory_skipped_space",
                thrower_space=thrower_space,
                explosion_space=explosion_space,
                reason="endpoints must share one declared coordinate space; "
                "spaces are never converted or assumed",
            )
            return None
        return {
            "thrower_point": thrower_point,
            "thrower_basis": thrower_basis,
            "explosion_point": explosion_point,
            "explosion_basis": explosion_basis,
            "space": thrower_space,
        }

    # ── record construction ──────────────────────────────────────

    def _produce(
        self,
        *,
        match_id: Optional[str],
        ts: float,
        source: str,
        source_event_id: Optional[str],
        pair: Dict[str, Any],
        thrower_name: Optional[str],
        thrower_team: Optional[str],
        victim_name: Optional[str],
        victim_team: Optional[str],
        weapon: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        dedupe_key = (str(match_id or "unknown"), source_event_id)
        if source_event_id and dedupe_key in self._seen_event_ids:
            return None
        arc = estimate_arc(
            pair["thrower_point"],
            pair["explosion_point"],
            coordinate_space=pair["space"],
            units_per_meter=self.units_per_meter,
        )
        if arc is None:
            self.stats.skipped_degenerate += 1
            return None
        if source_event_id:
            self._seen_event_ids.add(dedupe_key)
            if len(self._seen_event_ids) > 8192:
                self._seen_event_ids.clear()

        record: Dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "match_id": match_id,
            "ts": ts,
            "estimated": True,
            "method": arc["method"],
            "confidence_basis": arc["confidence_basis"],
            "source": source,
            "source_event_id": source_event_id,
            "weapon": weapon,
            "thrower": {
                "player": thrower_name,
                "team": thrower_team,
                "position": [pair["thrower_point"][0], pair["thrower_point"][1]],
                "position_basis": pair["thrower_basis"],
            },
            "explosion": {
                "position": [
                    pair["explosion_point"][0],
                    pair["explosion_point"][1],
                ],
                "position_basis": pair["explosion_basis"],
            },
            "victim": (
                {"player": victim_name, "team": victim_team}
                if victim_name
                else None
            ),
            "coordinate_space": arc["coordinate_space"],
            "distance": arc["distance"],
            "distance_units": arc["distance_units"],
            "launch_angle_deg": arc["launch_angle_deg"],
            "launch_angle_basis": arc["launch_angle_basis"],
            "apex_height": arc["apex_height"],
            "apex_units": arc["apex_units"],
            "flight_time_s": arc["flight_time_s"],
            "launch_speed_mps": arc["launch_speed_mps"],
            "arc_points": arc["arc_points"],
            "note": (
                "ESTIMATED arc derived from the thrower and explosion "
                "endpoints only. The projectile was not tracked; angle, "
                "apex and (when shown) flight time are outputs of an "
                "assumed projectile model, not measurements."
            ),
        }
        self.store.add(record)
        self.stats.produced += 1
        self.stats.by_source[source] = self.stats.by_source.get(source, 0) + 1
        return record

    # ── handlers ─────────────────────────────────────────────────

    def _on_position_update(
        self, data: Dict[str, Any], match_id: Optional[str], ts: float
    ) -> None:
        space = self._space_of(data)
        if not space:
            return
        for container_key in ("positions_by_player", "player_positions"):
            container = data.get(container_key)
            if not isinstance(container, dict):
                continue
            for name, raw in container.items():
                point = as_point(raw)
                if point is not None:
                    self.positions.update(match_id, str(name), point, space, ts)

    def _on_kill(
        self,
        data: Dict[str, Any],
        match_id: Optional[str],
        ts: float,
        event_type: str,
    ) -> None:
        if not is_grenade_weapon(data.get("weapon")):
            return
        # A kill event carrying explicit positions also refreshes the
        # book, so a slightly later correlated event can still pair up.
        self._learn_positions_from_kill(data, match_id, ts)
        pair = self._resolve_pair(
            data,
            match_id,
            ts,
            thrower_name=_clean_name(data.get("killer")),
            victim_name=_clean_name(data.get("victim")),
            allow_victim_proxy=True,
        )
        if pair is None:
            return
        self._produce(
            match_id=match_id,
            ts=ts,
            source=f"{event_type}.weapon",
            source_event_id=_event_id(data),
            pair=pair,
            thrower_name=_clean_name(data.get("killer")),
            thrower_team=_clean_name(data.get("killerTeam")),
            victim_name=_clean_name(data.get("victim")),
            victim_team=_clean_name(data.get("victimTeam")),
            weapon=str(data.get("weapon")),
        )

    def _on_utility(
        self, data: Dict[str, Any], match_id: Optional[str], ts: float
    ) -> None:
        if not is_grenade_utility(data.get("utility_type")):
            return
        thrower_name = _thrower_name(data)
        pair = self._resolve_pair(
            data,
            match_id,
            ts,
            thrower_name=thrower_name,
            victim_name=None,
            allow_victim_proxy=False,
            # The vision runtime reports the blast flash under "position"
            # (position_space="frame_pixels"); W3 world-side events may
            # use the explicit explosion keys instead. Both accepted.
            explosion_keys=("position",) + _EXPLOSION_KEYS,
        )
        if pair is None:
            return
        self._produce(
            match_id=match_id,
            ts=ts,
            source="utility_event",
            source_event_id=_event_id(data),
            pair=pair,
            thrower_name=thrower_name,
            thrower_team=_clean_name(data.get("thrower_team") or data.get("team")),
            victim_name=None,
            victim_team=None,
            weapon=str(data.get("utility_type")),
        )

    def _learn_positions_from_kill(
        self, data: Dict[str, Any], match_id: Optional[str], ts: float
    ) -> None:
        for name_value, keys in (
            (data.get("killer"), _THROWER_KEYS),
            (data.get("victim"), ("victim_position",) + _EXPLOSION_KEYS),
        ):
            name = _clean_name(name_value)
            if not name:
                continue
            hit = self._field_point(data, keys)
            if hit is not None and hit[1]:
                self.positions.update(match_id, name, hit[0], hit[1], ts)

    # ── status ───────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        return {
            "running": self._started,
            "subscriber_id": SUBSCRIBER_ID,
            "event_types": list(CONSUMED_EVENT_TYPES),
            "units_per_meter": self.units_per_meter,
            "units": (
                "meters (calibrated scale)"
                if self.units_per_meter
                else "coordinate units (uncalibrated — no flight time or "
                "launch speed is reported)"
            ),
            "stored_records": self.store.count(),
            "stats": {
                "received": self.stats.received,
                "produced": self.stats.produced,
                "explosion_only_observations": self.stats.explosion_only,
                "skipped_missing_endpoint": self.stats.skipped_missing_endpoint,
                "skipped_space": self.stats.skipped_space,
                "skipped_degenerate": self.stats.skipped_degenerate,
                "handler_errors": self.stats.handler_errors,
                "by_source": dict(self.stats.by_source),
                "last_error": self._last_error,
            },
        }


def _clean_name(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "Unknown":
        return None
    return text


def _thrower_name(data: Dict[str, Any]) -> Optional[str]:
    """Thrower identity from a utility payload, tolerating a nested
    {"player"/"name": ...} thrower dict. Never stringifies a dict."""
    raw = data.get("thrower")
    if isinstance(raw, dict):
        raw = raw.get("player") or raw.get("name")
    if not isinstance(raw, str):
        raw = data.get("player")
    return _clean_name(raw)


def _event_id(data: Dict[str, Any]) -> Optional[str]:
    raw = data.get("id") or data.get("event_id")
    return str(raw) if raw else None


# ── production wiring ────────────────────────────────────────────

_service: Optional[GrenadeTrajectoryService] = None


def get_grenade_trajectory_service() -> Optional[GrenadeTrajectoryService]:
    """The process-wide service built by build_grenade_trajectory_service."""
    return _service


def build_grenade_trajectory_service(
    bus: EventBus,
) -> GrenadeTrajectoryService:
    """Build the production consumer over the shared store singleton."""
    global _service
    _service = GrenadeTrajectoryService(bus=bus, store=trajectory_store)
    return _service
