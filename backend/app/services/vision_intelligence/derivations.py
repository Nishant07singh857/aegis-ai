# backend/app/services/vision_intelligence/derivations.py
"""Director derivations from spectator-observable signals (Phase 24, W2).

Builds intelligence ONLY from what a spectator can see, on top of the
Phase 22 subscriber's attributed streams:

  * ``ZoneClock``          — zone phase timing from ``zone_event``
                             payloads (``timer_seconds`` on timer-reset
                             edges). No timer read -> no countdown, and
                             every consumer of the countdown outputs
                             nothing rather than estimating one.
  * ``RotationTracker``    — position history, bearing, speed, rotation
                             start classification vs zone phase,
                             rotation urgency, vehicle-mode inference
                             and team shape (spread / split / isolated)
                             for tracked teams. Tracked = the observed
                             slot's team (own-marker centroid) plus any
                             team-labelled position stream. Anonymous
                             markers are NEVER tracked under a team.
  * ``FightForecaster``    — third-party alerts (>=3 distinct killer
                             teams in one fight cluster), rolling
                             decayed fight heat, gatekeep opportunities
                             (only when positions actually support it).
  * ``AirdropTracker``     — air-drop crate log from ``loot`` events +
                             spotter ``airdrop_spotted`` events, with an
                             honest "spotted on Team X's feed" semantics
                             and a contested flag from same-slot combat.
  * ``KnockSessionTracker``— per-slot knock sessions, revive inference
                             from KNOCKED->ALIVE team-status transitions
                             (the caller wires the bus event + the
                             TeamTracker.record_revive call), bleed-out
                             and finish outcomes, bleed-out urgency.

Honesty rules (standing): missing input -> no output; every inferred
value carries ``inferred: true`` + ``source: ...``; distances stay in
minimap pixels unless a calibrated ``px_per_meter`` exists.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import structlog

from .geometry import (
    GATEKEEP_EDGE_BAND_PX,
    ROTATION_PENDING_DISPLACEMENT_PX,
    ROTATION_QUIET_S,
    ROTATION_START_DISPLACEMENT_PX,
    SPLIT_THRESHOLD_PX,
    VEHICLE_MIN_SAMPLES,
    VEHICLE_SPEED_PX_PER_S,
    VEHICLE_WINDOW_S,
    bearing_degrees,
    bleed_thresholds,
    bleed_urgency_level,
    centroid,
    classify_rotation_start,
    distance,
    is_normalized_map_space,
    isolated_marker,
    max_pairwise_distance,
    urgency_band,
)

logger = structlog.get_logger("aegis.vision_intelligence.derivations")

FIGHT_CLUSTER_WINDOW_S = 60.0
HEAT_HALF_LIFE_S = 60.0
HEAT_WINDOW_S = 300.0
AIRDROP_CONTEST_WINDOW_S = 60.0
GATEKEEP_COOLDOWN_S = 60.0
MAX_PATH_SAMPLES = 500
MAX_ALERTS = 100


def _clean_team(value: Any) -> Optional[str]:
    """A real team label, or None for missing/'Unknown' placeholders."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "Unknown":
        return None
    return text


def get_poi_database_lazy() -> Any:
    """The W4 POI database singleton, or None when not built yet.

    Imported lazily (W4 builds ``services/poi`` concurrently); a missing
    module degrades POI naming to the honest vicinity fallback.
    """
    try:
        from app.services.poi.poi_database import get_poi_database

        return get_poi_database()
    except Exception:
        return None


# ── zone clock ───────────────────────────────────────────────────


class ZoneClock:
    """Zone phase timing derived from zone_event timer reads."""

    def __init__(self) -> None:
        self.phase: Optional[int] = None
        self.phase_started_at: Optional[float] = None
        self.phase_duration_s: Optional[float] = None
        self._timer_ts: Optional[float] = None
        self._timer_seconds: Optional[float] = None
        # Previous phase record, for rotations that started in it.
        self._prev_phase: Optional[Dict[str, Any]] = None

    def observe(self, payload: Dict[str, Any], ts: float) -> None:
        timer = payload.get("timer_seconds")
        if isinstance(timer, (int, float)) and timer > 0:
            self._timer_ts = float(ts)
            self._timer_seconds = float(timer)
        phase = payload.get("zone_phase")
        if phase is None:
            return
        try:
            phase_int = int(phase)
        except (TypeError, ValueError):
            return
        if phase_int != self.phase:
            if self.phase is not None:
                self._prev_phase = {
                    "phase": self.phase,
                    "started_at": self.phase_started_at,
                    "duration_s": self.phase_duration_s,
                }
            self.phase = phase_int
            self.phase_started_at = float(ts)
            # The timer read riding the reset edge is the new phase's
            # countdown at its start — the best available duration.
            self.phase_duration_s = (
                float(timer)
                if isinstance(timer, (int, float)) and timer > 0
                else None
            )

    def time_to_close(self, now: float) -> Optional[float]:
        """Seconds until zone close, from the latest real timer read."""
        if self._timer_ts is None or self._timer_seconds is None:
            return None
        remaining = self._timer_seconds - (now - self._timer_ts)
        return max(0.0, remaining)

    def phase_info_for(self, ts: float) -> Tuple[Optional[float], Optional[float]]:
        """(phase_start, phase_duration) of the phase containing ts."""
        for record in (
            {
                "started_at": self.phase_started_at,
                "duration_s": self.phase_duration_s,
            },
            self._prev_phase or {},
        ):
            start = record.get("started_at")
            duration = record.get("duration_s")
            if start is None:
                continue
            if duration and start <= ts <= start + duration:
                return start, duration
            if not duration and ts >= start and record is not self._prev_phase:
                return start, None
        return self.phase_started_at, self.phase_duration_s


# ── rotations + team shape ───────────────────────────────────────


class RotationTracker:
    """Position history / rotation intelligence for tracked teams."""

    def __init__(
        self,
        zone_clock: ZoneClock,
        px_per_meter: Optional[float] = None,
        clock: Callable[[], float] = time.time,
    ):
        self._zone_clock = zone_clock
        self.px_per_meter = px_per_meter
        self._clock = clock
        # team_id -> state dict (see _new_state)
        self.teams: Dict[str, Dict[str, Any]] = {}
        #: Completed + started rotation records, in order.
        self.rotation_events: List[Dict[str, Any]] = []

    def _new_state(self, team_id: str, team_name: Optional[str]) -> Dict[str, Any]:
        state = {
            "team_id": team_id,
            "team_name": team_name,
            "samples": [],  # [{ts, x, y, source}]
            "anchor": None,  # (x, y, ts) settled position
            "pending_start_ts": None,
            "last_move_ts": None,
            "active_rotation": None,
            "vehicle_series": [],  # [{ts, count}] observed team only
            "speed_samples": [],  # [{ts, speed_px_s}]
            "shape": None,  # last team-shape snapshot (observed team)
            "map_id": None,
            "coordinate_space": None,
        }
        self.teams[team_id] = state
        return state

    # ── ingestion ────────────────────────────────────────────────

    def add_position(
        self,
        team_id: str,
        x: float,
        y: float,
        ts: float,
        source: str,
        team_name: Optional[str] = None,
        map_id: Optional[str] = None,
        coordinate_space: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Add one centroid sample. Returns a rotation_started event
        dict when this sample starts a rotation, else None."""
        state = self.teams.get(team_id) or self._new_state(team_id, team_name)
        if team_name:
            state["team_name"] = team_name
        if map_id:
            state["map_id"] = map_id
        if coordinate_space:
            state["coordinate_space"] = coordinate_space
        samples = state["samples"]
        prev = samples[-1] if samples else None
        sample = {"ts": float(ts), "x": float(x), "y": float(y), "source": source}
        samples.append(sample)
        if len(samples) > MAX_PATH_SAMPLES:
            del samples[: len(samples) - MAX_PATH_SAMPLES]

        if prev is not None:
            dt = float(ts) - prev["ts"]
            moved = distance((float(x), float(y)), (prev["x"], prev["y"]))
            if dt > 0:
                state["speed_samples"].append(
                    {"ts": float(ts), "speed_px_s": moved / dt}
                )
                state["speed_samples"] = [
                    s for s in state["speed_samples"] if ts - s["ts"] <= 60.0
                ]
            if moved >= 2.0:
                state["last_move_ts"] = float(ts)

        # Close a stale active rotation before evaluating a new start.
        self._maybe_close_rotation(state, float(ts))

        started_event = None
        if state["anchor"] is None:
            state["anchor"] = (float(x), float(y), float(ts))
        elif state["active_rotation"] is None:
            ax, ay, _ = state["anchor"]
            disp = distance((float(x), float(y)), (ax, ay))
            if disp > ROTATION_PENDING_DISPLACEMENT_PX and state["pending_start_ts"] is None:
                state["pending_start_ts"] = float(ts)
            if disp >= ROTATION_START_DISPLACEMENT_PX:
                started_at = state["pending_start_ts"] or float(ts)
                phase_start, phase_duration = self._zone_clock.phase_info_for(started_at)
                classification = classify_rotation_start(
                    started_at, phase_start, phase_duration
                )
                rotation = {
                    "team_id": team_id,
                    "team_name": state["team_name"],
                    "started_at": started_at,
                    "from": [round(ax, 1), round(ay, 1)],
                    "classification": classification,
                    "classification_label": "heuristic" if classification else None,
                    "inferred": True,
                    "source": "position_history_vs_zone_phase",
                    "ended_at": None,
                }
                state["active_rotation"] = rotation
                self.rotation_events.append(rotation)
                started_event = rotation
        return started_event

    def _maybe_close_rotation(self, state: Dict[str, Any], now: float) -> None:
        rotation = state.get("active_rotation")
        if rotation is None:
            return
        last_move = state.get("last_move_ts")
        if last_move is not None and now - last_move >= ROTATION_QUIET_S:
            rotation["ended_at"] = last_move
            state["active_rotation"] = None
            samples = state["samples"]
            if samples:
                last = samples[-1]
                state["anchor"] = (last["x"], last["y"], last["ts"])
            state["pending_start_ts"] = None

    def add_vehicle_markers(self, team_id: str, count: Optional[int], ts: float) -> None:
        """Record the minimap vehicle-marker count for one slot's team."""
        if count is None:
            return
        state = self.teams.get(team_id)
        if state is None:
            return
        series = state["vehicle_series"]
        series.append({"ts": float(ts), "count": int(count)})
        state["vehicle_series"] = [s for s in series if ts - s["ts"] <= 120.0]

    def set_shape(self, team_id: str, shape: Dict[str, Any]) -> None:
        state = self.teams.get(team_id)
        if state is not None:
            state["shape"] = shape

    # ── derived values ───────────────────────────────────────────

    def current_speed(self, state: Dict[str, Any]) -> Optional[float]:
        samples = state["samples"]
        if len(samples) < 2:
            return None
        a, b = samples[-2], samples[-1]
        dt = b["ts"] - a["ts"]
        if dt <= 0:
            return None
        return distance((b["x"], b["y"]), (a["x"], a["y"])) / dt

    def current_bearing(self, state: Dict[str, Any]) -> Optional[float]:
        samples = state["samples"]
        if len(samples) < 2:
            return None
        a, b = samples[-2], samples[-1]
        return bearing_degrees((a["x"], a["y"]), (b["x"], b["y"]))

    def rotation_mode(self, state: Dict[str, Any], now: float) -> Optional[str]:
        """'vehicle' / 'on_foot' for the observed team (inferred).

        Vehicle: vehicle_markers >= 1 recently AND sustained centroid
        speed above VEHICLE_SPEED_PX_PER_S across >= VEHICLE_MIN_SAMPLES
        speed samples inside the window. Missing either leg -> the
        mode is only claimed when a real speed exists ('on_foot');
        with no speed data at all the mode is None (no output).
        """
        recent_speeds = [
            s["speed_px_s"]
            for s in state["speed_samples"]
            if now - s["ts"] <= VEHICLE_WINDOW_S
        ]
        if not recent_speeds:
            return None
        recent_vehicles = [
            s for s in state["vehicle_series"] if now - s["ts"] <= VEHICLE_WINDOW_S
        ]
        has_vehicle = any(s["count"] >= 1 for s in recent_vehicles)
        sustained_fast = (
            len(recent_speeds) >= VEHICLE_MIN_SAMPLES
            and sum(recent_speeds) / len(recent_speeds) > VEHICLE_SPEED_PX_PER_S
        )
        if has_vehicle and sustained_fast:
            return "vehicle"
        return "on_foot"

    def snapshot(
        self,
        now: float,
        zone_distances_for: Callable[[str], Optional[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        teams_out: Dict[str, Any] = {}
        time_to_close = self._zone_clock.time_to_close(now)
        for team_id, state in self.teams.items():
            self._maybe_close_rotation(state, now)
            samples = state["samples"]
            if not samples:
                continue
            speed = self.current_speed(state)
            dists = zone_distances_for(team_id)
            edge_px = dists.get("distance_to_edge_px") if dists else None
            eta = None
            if edge_px is not None and edge_px < 0 and speed:
                eta = abs(edge_px) / speed
            urgency = urgency_band(edge_px, time_to_close, eta)
            mode = self.rotation_mode(state, now)
            last = samples[-1]
            entry: Dict[str, Any] = {
                "team_id": team_id,
                "team_name": state["team_name"],
                "current_position": [round(last["x"], 1), round(last["y"], 1)],
                "position_source": last["source"],
                "coordinate_space": state["coordinate_space"],
                "bearing_deg": self.current_bearing(state),
                "speed_px_s": round(speed, 2) if speed is not None else None,
                "path": [
                    {"ts": s["ts"], "x": round(s["x"], 1), "y": round(s["y"], 1)}
                    for s in samples[-100:]
                ],
                "active_rotation": state["active_rotation"],
                "urgency": urgency,
                "urgency_label": "heuristic" if urgency else None,
                "urgency_inputs": {
                    "distance_to_edge_px": edge_px,
                    "time_to_zone_close_s": (
                        round(time_to_close, 1) if time_to_close is not None else None
                    ),
                    "eta_to_edge_s": round(eta, 1) if eta is not None else None,
                },
                "rotation_mode": mode,
                "shape": state["shape"],
                "updated_at": last["ts"],
            }
            if speed is not None and self.px_per_meter:
                entry["speed_m_s"] = round(speed / self.px_per_meter, 2)
            if mode is not None:
                entry["rotation_mode_inferred"] = True
                entry["rotation_mode_source"] = (
                    "centroid_speed+vehicle_markers"
                )
            teams_out[team_id] = entry
        return {
            "teams": teams_out,
            "rotation_events": list(self.rotation_events[-50:]),
            "time_to_zone_close_s": (
                round(time_to_close, 1) if time_to_close is not None else None
            ),
        }


# ── team shape (pure computation over own markers) ────────────────


def compute_team_shape(
    markers: Dict[str, Tuple[float, float]],
    px_per_meter: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Spread / split / isolated for the observed team's OWN markers.

    ``markers`` maps marker keys (``teammate_0``, ``self_0``...) to
    minimap-pixel coordinates — per-marker data MinimapReader emits in
    ``position_update.player_positions``. Enemy markers must already
    be excluded by the caller (they are anonymous, never shaped).
    Returns None with fewer than 2 own markers (no shape exists).
    """
    own = {k: v for k, v in (markers or {}).items() if not str(k).startswith("enemy")}
    if len(own) < 2:
        return None
    points = list(own.values())
    centre = centroid(points)
    spread = max_pairwise_distance(points)
    iso = isolated_marker(own)
    shape: Dict[str, Any] = {
        "markers": len(own),
        "centroid": [round(centre[0], 1), round(centre[1], 1)] if centre else None,
        "spread_px": round(spread, 1) if spread is not None else None,
        "is_split": bool(spread is not None and spread > SPLIT_THRESHOLD_PX),
        "split_threshold_px": SPLIT_THRESHOLD_PX,
        "isolated": iso,
        "units": "minimap_pixels",
    }
    if spread is not None and px_per_meter:
        shape["spread_m"] = round(spread / px_per_meter, 1)
    return shape


# ── fight forecasting ────────────────────────────────────────────


class FightForecaster:
    """Third-party alerts, fight heat and gatekeep opportunities."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self.events: List[Dict[str, Any]] = []  # kill/knock, windowed
        self.heat: Dict[str, Dict[str, Any]] = {}  # area label -> entry
        self._third_party_alerted_set: Optional[frozenset] = None
        self._gatekeep_active = False
        self._gatekeep_last_alert = 0.0

    def record_combat(
        self,
        ts: float,
        kind: str,
        killer_team: Optional[str],
        victim_team: Optional[str],
        area_label: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Record one kill/knock. Returns a third_party alert dict
        when this event completes a >=3-distinct-killer-team cluster
        inside the rolling window, else None."""
        self.events.append(
            {
                "ts": float(ts),
                "kind": kind,
                "killer_team": killer_team,
                "victim_team": victim_team,
            }
        )
        cutoff = ts - HEAT_WINDOW_S
        self.events = [e for e in self.events if e["ts"] >= cutoff]

        if area_label:
            entry = self.heat.setdefault(
                area_label,
                {"area": area_label, "events": [], "last_event_ts": None},
            )
            entry["events"].append(float(ts))
            entry["events"] = [t for t in entry["events"] if t >= cutoff]
            entry["last_event_ts"] = float(ts)

        window = [e for e in self.events if ts - e["ts"] <= FIGHT_CLUSTER_WINDOW_S]
        killers = {e["killer_team"] for e in window if e["killer_team"]}
        if len(killers) < 3:
            self._third_party_alerted_set = None
            return None
        signature = frozenset(killers)
        if signature == self._third_party_alerted_set:
            return None
        self._third_party_alerted_set = signature
        involved = sorted(
            {t for e in window for t in (e["killer_team"], e["victim_team"]) if t}
        )
        return {
            "kind": "third_party",
            "teams": involved,
            "killer_teams": sorted(killers),
            "window_s": FIGHT_CLUSTER_WINDOW_S,
            "event_count": len(window),
            "inferred": False,
            "source": "killfeed_team_attribution_cluster",
            "timestamp": float(ts),
        }

    def check_active_fights(
        self, fights: List[Dict[str, Any]], ts: float
    ) -> Optional[Dict[str, Any]]:
        """Third-party from the fight detector's active fights: one
        promoted fight already involving >=3 distinct real teams."""
        for fight in fights or []:
            teams = [t for t in fight.get("teams", []) if t]
            distinct = sorted(set(teams))
            if len(distinct) >= 3:
                signature = frozenset(distinct)
                if signature == self._third_party_alerted_set:
                    return None
                self._third_party_alerted_set = signature
                return {
                    "kind": "third_party",
                    "teams": distinct,
                    "killer_teams": distinct,
                    "fight_id": fight.get("fight_id"),
                    "inferred": False,
                    "source": "fight_detector_active_fight_teams",
                    "timestamp": float(ts),
                }
        return None

    def evaluate_gatekeep(
        self,
        ts: float,
        observed_team_id: Optional[str],
        observed_distances: Optional[Dict[str, Any]],
        fight_team_distances: Dict[str, Optional[Dict[str, Any]]],
    ) -> Optional[Dict[str, Any]]:
        """Gatekeep alert — ONLY when positions support it.

        Requires ALL of: the observed team is inside the zone within
        the edge band; at least one team involved in an active fight
        has a labelled position OUTSIDE the zone. Any missing leg ->
        None (no call). Edge-triggered with a cooldown.
        """
        conditions = False
        outside_teams: List[str] = []
        if (
            observed_team_id
            and observed_distances
            and observed_distances.get("inside_zone")
            and 0 <= observed_distances.get("distance_to_edge_px", -1) <= GATEKEEP_EDGE_BAND_PX
        ):
            outside_teams = [
                tid
                for tid, d in fight_team_distances.items()
                if tid != observed_team_id
                and d is not None
                and not d.get("inside_zone")
            ]
            conditions = bool(outside_teams)
        if not conditions:
            self._gatekeep_active = False
            return None
        if self._gatekeep_active and ts - self._gatekeep_last_alert < GATEKEEP_COOLDOWN_S:
            return None
        self._gatekeep_active = True
        self._gatekeep_last_alert = float(ts)
        return {
            "kind": "gatekeep",
            "team_id": observed_team_id,
            "outside_teams": sorted(outside_teams),
            "observed_distance_to_edge_px": observed_distances.get(
                "distance_to_edge_px"
            ),
            "edge_band_px": GATEKEEP_EDGE_BAND_PX,
            "inferred": True,
            "source": "observed_edge_position+labelled_fight_positions",
            "timestamp": float(ts),
        }

    def heat_snapshot(self, now: float) -> List[Dict[str, Any]]:
        """Rolling fight heat per area, exponential decay applied."""
        out = []
        for label, entry in self.heat.items():
            recent = [t for t in entry["events"] if now - t <= HEAT_WINDOW_S]
            if not recent:
                continue
            weight = sum(0.5 ** ((now - t) / HEAT_HALF_LIFE_S) for t in recent)
            out.append(
                {
                    "area": label,
                    "heat": round(weight, 2),
                    "event_count": len(recent),
                    "last_event_ts": max(recent),
                    "decay_half_life_s": HEAT_HALF_LIFE_S,
                }
            )
        out.sort(key=lambda e: e["heat"], reverse=True)
        return out


# ── airdrops ─────────────────────────────────────────────────────


class AirdropTracker:
    """Air-drop crate log with honest spotting semantics."""

    def __init__(self) -> None:
        self.log: List[Dict[str, Any]] = []
        self._combat_by_slot: Dict[int, List[float]] = {}

    def record_crate(
        self,
        ts: float,
        slot_number: Optional[int],
        team_id: Optional[str],
        team_name: Optional[str],
        source: str,
        loot_type: str = "air_drop",
        position: Any = None,
        location_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        spotted_on = None
        if team_name:
            # Honest semantics: the crate was seen ON this team's feed,
            # which places it near that team's spectated player — it is
            # NOT a claim the team owns or reached the crate.
            spotted_on = f"spotted on {team_name}'s feed / near {team_name}"
        entry = {
            "timestamp": float(ts),
            "loot_type": loot_type,
            "slot_number": slot_number,
            "team_id": team_id,
            "team_name": team_name,
            "spotted_on": spotted_on,
            "source": source,
            "position": position,
            "location_name": location_name,
            "contested": self._contested(slot_number, float(ts)),
            "inferred": False,
        }
        self.log.append(entry)
        if len(self.log) > 100:
            self.log = self.log[-100:]
        return entry

    def note_combat(self, slot_number: Optional[int], ts: float) -> None:
        """A kill/knock seen on a slot: marks recent crates contested."""
        if slot_number is None:
            return
        times = self._combat_by_slot.setdefault(int(slot_number), [])
        times.append(float(ts))
        self._combat_by_slot[int(slot_number)] = [
            t for t in times if ts - t <= 3600.0
        ]
        for entry in self.log:
            if entry["slot_number"] == int(slot_number) and not entry["contested"]:
                if 0 <= ts - entry["timestamp"] <= AIRDROP_CONTEST_WINDOW_S:
                    entry["contested"] = True

    def _contested(self, slot_number: Optional[int], ts: float) -> bool:
        if slot_number is None:
            return False
        return any(
            0 <= ts - t <= AIRDROP_CONTEST_WINDOW_S
            for t in self._combat_by_slot.get(int(slot_number), [])
        )

    def snapshot(self) -> List[Dict[str, Any]]:
        return list(reversed(self.log))


# ── knock sessions + revive inference ────────────────────────────


class KnockSessionTracker:
    """Per-player knock sessions from team-status / HP knock signals.

    Sessions are keyed by tracker player key (``<team>_player_<slot>``)
    for the observed team, and by player name for killfeed-named
    victims. Outcomes:
      * KNOCKED -> ALIVE with no death in between = revive (INFERRED,
        labelled ``source: team_status_transition``) — the caller emits
        the bus event and wires ``TeamTracker.record_revive``.
      * KNOCKED -> DEAD = bled out (INFERRED, same source).
      * A kill event naming the session's player = finished.
    """

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self.active: Dict[str, Dict[str, Any]] = {}
        self.completed: List[Dict[str, Any]] = []
        self._knock_counts: Dict[str, int] = {}
        self._alerted_thresholds: Dict[str, set] = {}

    # ── lifecycle ────────────────────────────────────────────────

    def start(
        self,
        key: str,
        ts: float,
        team_id: Optional[str],
        team_name: Optional[str],
        player_label: Optional[str],
        source: str,
    ) -> Optional[Dict[str, Any]]:
        if key in self.active:
            return None
        prior = self._knock_counts.get(key, 0)
        session = {
            "key": key,
            "team_id": team_id,
            "team_name": team_name,
            "player": player_label,
            "started_at": float(ts),
            "prior_knocks": prior,
            "source": source,
            "outcome": None,
        }
        self._knock_counts[key] = prior + 1
        self.active[key] = session
        self._alerted_thresholds[key] = set()
        return session

    def close(
        self, key: str, ts: float, outcome: str, inferred: bool, source: str
    ) -> Optional[Dict[str, Any]]:
        session = self.active.pop(key, None)
        if session is None:
            return None
        session["ended_at"] = float(ts)
        session["duration_s"] = round(float(ts) - session["started_at"], 1)
        session["outcome"] = outcome
        session["inferred"] = inferred
        session["outcome_source"] = source
        self.completed.append(session)
        if len(self.completed) > 100:
            self.completed = self.completed[-100:]
        self._alerted_thresholds.pop(key, None)
        return session

    def finish_by_kill(self, player_name: Optional[str], ts: float) -> Optional[Dict[str, Any]]:
        """Close a named session because a kill event finished them."""
        if not player_name:
            return None
        for key, session in list(self.active.items()):
            if session.get("player") == player_name:
                return self.close(key, ts, "finished", False, "kill_event")
        return None

    # ── urgency ──────────────────────────────────────────────────

    def urgency_alerts(self, now: float) -> List[Dict[str, Any]]:
        """Bleed-out urgency alerts for sessions crossing thresholds.

        One alert per threshold per session. Heuristic (BGMI bleed
        accelerates per knock; thresholds scale with prior knocks).
        """
        alerts = []
        for key, session in self.active.items():
            duration = now - session["started_at"]
            level = bleed_urgency_level(duration, session.get("prior_knocks", 0))
            if level is None:
                continue
            fired = self._alerted_thresholds.setdefault(key, set())
            if level in fired:
                continue
            fired.add(level)
            warn, critical = bleed_thresholds(session.get("prior_knocks", 0))
            alerts.append(
                {
                    "kind": "bleedout_urgency",
                    "level": level,
                    "team_id": session.get("team_id"),
                    "team_name": session.get("team_name"),
                    "player": session.get("player"),
                    "knock_duration_s": round(duration, 1),
                    "prior_knocks": session.get("prior_knocks", 0),
                    "thresholds_s": {"warn": warn, "critical": critical},
                    "inferred": True,
                    "source": "knock_duration_thresholds",
                    "label": "heuristic",
                    "timestamp": float(now),
                }
            )
        return alerts

    def snapshot(self, now: float) -> Dict[str, Any]:
        active = []
        for session in self.active.values():
            duration = now - session["started_at"]
            warn, critical = bleed_thresholds(session.get("prior_knocks", 0))
            active.append(
                {
                    **session,
                    "duration_s": round(duration, 1),
                    "urgency": bleed_urgency_level(
                        duration, session.get("prior_knocks", 0)
                    ),
                    "urgency_thresholds_s": {"warn": warn, "critical": critical},
                    "urgency_label": "heuristic",
                }
            )
        return {"active": active, "completed": list(reversed(self.completed[-50:]))}


# ── facade ───────────────────────────────────────────────────────


class DirectorDerivations:
    """Facade holding all Phase 24 derivation state for one service."""

    def __init__(
        self,
        px_per_meter: Optional[float] = None,
        clock: Callable[[], float] = time.time,
        poi_database: Any = None,
    ):
        self._clock = clock
        self.px_per_meter = px_per_meter
        self.zone_clock = ZoneClock()
        self.rotations = RotationTracker(
            self.zone_clock, px_per_meter=px_per_meter, clock=clock
        )
        self.fights = FightForecaster(clock=clock)
        self.airdrops = AirdropTracker()
        self.knocks = KnockSessionTracker(clock=clock)
        self._poi = poi_database  # None -> lazy contract lookup
        self.alerts: List[Dict[str, Any]] = []
        # team_id -> latest real position context (for area naming)
        self._position_context: Dict[str, Dict[str, Any]] = {}

    # ── POI naming (contract: services/poi/poi_database.py) ─────

    def poi_database(self) -> Any:
        if self._poi is None:
            self._poi = get_poi_database_lazy()
        return self._poi

    def poi_name_for(
        self,
        map_id: Optional[str],
        x: Optional[float],
        y: Optional[float],
        coordinate_space: Optional[str],
    ) -> Optional[str]:
        """Nearest POI name — ONLY for normalized map coordinates.

        Minimap-region pixels are a local zoomed view, not full-map
        coordinates; feeding them to the POI contract would name a
        wrong place, so they return None (caller falls back honestly).
        """
        if not map_id or x is None or y is None:
            return None
        if not is_normalized_map_space(coordinate_space):
            return None
        db = self.poi_database()
        if db is None:
            return None
        try:
            poi = db.nearest(map_id, float(x), float(y))
        except Exception as exc:
            logger.debug("poi_lookup_failed", error=str(exc)[:120])
            return None
        if isinstance(poi, dict):
            return poi.get("name")
        return None

    def note_position_context(
        self,
        team_id: str,
        x: float,
        y: float,
        map_id: Optional[str],
        coordinate_space: Optional[str],
    ) -> None:
        self._position_context[team_id] = {
            "x": float(x),
            "y": float(y),
            "map_id": map_id,
            "coordinate_space": coordinate_space,
        }

    def area_label_for(
        self, team_id: Optional[str], team_name: Optional[str]
    ) -> Optional[str]:
        """Fight-heat area label for one attributed team.

        POI name when a normalized position + POI database are
        available; otherwise "<Team> vicinity"; None when the team
        itself is unknown (no area may be claimed).
        """
        if not team_id or not team_name:
            return None
        ctx = self._position_context.get(team_id)
        if ctx:
            poi = self.poi_name_for(
                ctx.get("map_id"), ctx.get("x"), ctx.get("y"),
                ctx.get("coordinate_space"),
            )
            if poi:
                return poi
        return f"{team_name} vicinity"

    # ── alerts ───────────────────────────────────────────────────

    def add_alert(self, alert: Dict[str, Any]) -> Dict[str, Any]:
        self.alerts.append(alert)
        if len(self.alerts) > MAX_ALERTS:
            self.alerts = self.alerts[-MAX_ALERTS:]
        return alert

    def recent_alerts(self, kinds: Optional[set] = None) -> List[Dict[str, Any]]:
        if kinds is None:
            return list(reversed(self.alerts))
        return [a for a in reversed(self.alerts) if a.get("kind") in kinds]
