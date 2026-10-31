# backend/app/services/live_director/engagement.py
"""Per-team engagement-state tracker (Phase 25, P1-5).

WHY THIS MODULE LIVES HERE (not in vision_intelligence): the state is
a DIRECTOR concept — it exists to feed the live director's unified
score (its ``precombat`` term) and PVW pre-positioning. The vision
layer already publishes every input it can honestly produce
(promoted fights, intel alerts, rotations, position snapshots); this
tracker fuses those published signals per team. Putting the fusion in
the director package keeps the ownership boundary clean: vision
produces signals, the director owns engagement state.

States (the spec's enum, spectator version)::

    IDLE  LOOTING  ROTATING  PRE_COMBAT  ACTIVE_COMBAT  CLUTCH

Fused ONLY from real signals:

  * match phase (PhaseTracker vocabulary) -> the baseline:
    plane / parachute / not_started -> IDLE; loot -> LOOTING;
    anything else -> IDLE.
  * RotationTracker movement (an active rotation record for the team)
    -> ROTATING, or PRE_COMBAT when the rotation runs into a POI /
    compound another team already occupies.
  * Convergence -> PRE_COMBAT: FightDetector PotentialFight
    confidence at/above the configured entry threshold, proximity
    readings inside the configured minimap-pixel threshold,
    derivations fight heat, and intel convergence kinds
    (third_party / gatekeep / split / isolated).
  * A promoted fight (fight.detected) -> ACTIVE_COMBAT.
  * A promoted fight carrying a verified clutch flag -> CLUTCH.
    "Verified" means the fight detector's own clutch determination
    rides the promotion payload — this tracker never derives clutch
    from headcounts on its own.

There is deliberately NO sight-line / visibility input: a spectator
screen never renders who can see whom, so no such signal exists and
none is modelled.

Honest decay: every non-baseline state carries the timestamp of its
last refreshing signal. ``tick(now)`` (also applied on every read)
returns a team to its phase-appropriate baseline once its signals go
silent past the configured TTL — a team is never stuck in PRE_COMBAT
(or anywhere else) forever. Every transition is appended to a bounded
log with the signals that triggered it, for QA audit.
"""

from __future__ import annotations

import math
import time
from collections import deque
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence

import structlog

from .config import LiveDirectorConfig

logger = structlog.get_logger("aegis.live_director.engagement")

#: Intel kinds that count as convergence evidence for PRE_COMBAT.
CONVERGENCE_INTEL_KINDS = frozenset(
    {"third_party", "gatekeep", "split", "isolated"}
)

MAX_TRANSITIONS = 200


class EngagementState(str, Enum):
    IDLE = "IDLE"
    LOOTING = "LOOTING"
    ROTATING = "ROTATING"
    PRE_COMBAT = "PRE_COMBAT"
    ACTIVE_COMBAT = "ACTIVE_COMBAT"
    CLUTCH = "CLUTCH"


def baseline_for_phase(phase: Optional[str]) -> EngagementState:
    """Phase-appropriate resting state when no signal is active."""
    p = (phase or "").strip().lower()
    if p == "loot":
        return EngagementState.LOOTING
    return EngagementState.IDLE


def _finite_float(value: Any) -> Optional[float]:
    """Finite float for a numeric enrichment reading, else None.

    Bools and non-numeric / non-finite values (NaN, infinities) are
    rejected: an unmeasurable enrichment field is absent, never a
    stored number that looks measured.
    """
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


class _TeamState:
    __slots__ = (
        "team_id", "state", "state_since", "phase",
        "fight_id", "fight_clutch", "fight_last_activity",
        "precombat_last", "precombat_signals",
        "rotating_last", "last_signals", "rotation_detail",
    )

    def __init__(self, team_id: str, phase: Optional[str]):
        self.team_id = team_id
        self.state = baseline_for_phase(phase)
        self.state_since = 0.0
        self.phase = phase
        self.fight_id: Optional[str] = None
        self.fight_clutch = False
        self.fight_last_activity = 0.0
        self.precombat_last = 0.0
        self.precombat_signals: List[str] = []
        self.rotating_last = 0.0
        self.last_signals: List[str] = []
        #: Latest rotation intake for this team (Phase 35): the
        #: screen-derived enrichment of the most recent active
        #: rotation reading (mode / bearing / speed in px/s, plus
        #: the occupancy/POI audit fields). Audit context ONLY —
        #: never an input to state derivation or scoring. None when
        #: no enriched rotation has been recorded, or once the
        #: rotation signal has gone stale / ended.
        self.rotation_detail: Optional[Dict[str, Any]] = None


class EngagementTracker:
    """Fuses real spectator signals into per-team engagement states."""

    def __init__(
        self,
        config: Optional[LiveDirectorConfig] = None,
        clock: Callable[[], float] = time.time,
        phase_provider: Optional[Callable[[], Optional[str]]] = None,
    ):
        self._config = config or LiveDirectorConfig()
        self._clock = clock
        self._phase_provider = phase_provider
        self._phase: Optional[str] = None
        self._teams: Dict[str, _TeamState] = {}
        self._transitions: deque = deque(maxlen=MAX_TRANSITIONS)
        self.stats = {"transitions": 0, "signals": 0}

    # ── phase ────────────────────────────────────────────────────

    def observe_phase(self, phase: Optional[str]) -> None:
        if phase:
            self._phase = str(phase)

    def _phase_now(self) -> Optional[str]:
        if self._phase_provider is not None:
            try:
                provided = self._phase_provider()
            except Exception:  # pragma: no cover - defensive
                provided = None
            if provided:
                self._phase = str(provided)
        return self._phase

    # ── signal intake (real signals only) ────────────────────────

    def _team(self, team_id: str) -> _TeamState:
        entry = self._teams.get(team_id)
        if entry is None:
            entry = _TeamState(team_id, self._phase_now())
            entry.state_since = self._clock()
            self._teams[team_id] = entry
        return entry

    def note_rotation(
        self,
        team_id: str,
        *,
        active: bool = True,
        occupied_by: Optional[str] = None,
        poi: Optional[str] = None,
        now: Optional[float] = None,
        rotation_mode: Optional[str] = None,
        bearing_deg: Optional[float] = None,
        speed_px_s: Optional[float] = None,
        occupancy_basis: Optional[str] = None,
    ) -> None:
        """RotationTracker movement for one team.

        ``occupied_by`` names a team already holding the destination
        POI/compound (from labelled positions / derivations): a
        rotation into an occupied POI is convergence -> PRE_COMBAT.

        ``rotation_mode`` / ``bearing_deg`` / ``speed_px_s`` are the
        screen-derived enrichment of the rotation reading (Phase 35):
        mode is ``"vehicle"`` / ``"on_foot"`` (inferred), bearing is
        in screen degrees, speed is in minimap pixels per second —
        NEVER physical units. They are stored as the team's latest
        rotation audit context (see :meth:`snapshot`) and are NEVER
        inputs to state derivation or scoring: mode alone never
        creates PRE_COMBAT, and bearing/speed never change a state.
        Invalid enrichment (unknown mode, non-finite or negative
        speed, non-finite bearing) is dropped to None, never stored
        as if measured.
        """
        if not team_id:
            return
        ts = self._clock() if now is None else float(now)
        entry = self._team(team_id)
        self.stats["signals"] += 1
        if not active:
            entry.rotating_last = 0.0
            entry.rotation_detail = None
            self._refresh(entry, ts, ["rotation_ended"])
            return
        entry.rotating_last = ts
        entry.last_signals = ["rotation_active"]
        detail: Dict[str, Any] = {"at": ts}
        if occupied_by and occupied_by != team_id:
            detail["occupied_by"] = occupied_by
        if poi:
            detail["poi"] = poi
        if occupancy_basis:
            detail["occupancy_basis"] = str(occupancy_basis)
        mode = (
            str(rotation_mode).strip()
            if isinstance(rotation_mode, str) and rotation_mode.strip()
            else None
        )
        if mode in ("vehicle", "on_foot"):
            detail["rotation_mode"] = mode
        bearing = _finite_float(bearing_deg)
        if bearing is not None:
            detail["bearing_deg"] = bearing
        speed = _finite_float(speed_px_s)
        if speed is not None and speed >= 0.0:
            detail["speed_px_s"] = speed
        entry.rotation_detail = detail if len(detail) > 1 else None
        if occupied_by and occupied_by != team_id:
            entry.precombat_last = ts
            entry.precombat_signals = [
                "rotation_into_occupied_poi",
                f"occupied_by:{occupied_by}",
            ]
            if poi:
                entry.precombat_signals.append(f"poi:{poi}")
        self._refresh(entry, ts, list(entry.last_signals))

    def note_potential_fight(
        self,
        team_ids: Sequence[str],
        confidence: float,
        *,
        now: Optional[float] = None,
        signals: Optional[Sequence[str]] = None,
    ) -> None:
        """FightDetector PotentialFight confidence for the involved
        teams. At/above the entry threshold this is convergence ->
        PRE_COMBAT; below it, the reading is recorded as a weak signal
        on the team but changes no state."""
        ts = self._clock() if now is None else float(now)
        try:
            conf = float(confidence)
        except (TypeError, ValueError):
            return
        for team_id in team_ids or ():
            if not team_id:
                continue
            entry = self._team(str(team_id))
            self.stats["signals"] += 1
            if conf >= self._config.precombat_confidence_enter:
                entry.precombat_last = ts
                entry.precombat_signals = [
                    f"potential_fight_confidence:{conf:g}",
                    *[str(s) for s in (signals or ())],
                ]
            entry.last_signals = [f"potential_fight_confidence:{conf:g}"]
            self._refresh(entry, ts, entry.precombat_signals or entry.last_signals)

    def note_proximity(
        self,
        team_id: str,
        distance_px: Optional[float] = None,
        *,
        distance_m: Optional[float] = None,
        px_per_meter: Optional[float] = None,
        now: Optional[float] = None,
    ) -> None:
        """Proximity / convergence reading for one team.

        Pixel readings compare against the configured minimap-pixel
        threshold. A metre reading is honoured ONLY when a calibrated
        ``px_per_meter`` accompanies it (converted back to pixels) —
        uncalibrated metres are ignored, never guessed at.
        """
        if not team_id:
            return
        ts = self._clock() if now is None else float(now)
        px: Optional[float] = None
        if distance_px is not None:
            try:
                px = float(distance_px)
            except (TypeError, ValueError):
                px = None
        elif distance_m is not None and px_per_meter:
            px = float(distance_m) * float(px_per_meter)
        if px is None:
            return
        entry = self._team(team_id)
        self.stats["signals"] += 1
        entry.last_signals = [f"proximity_px:{px:g}"]
        if px <= self._config.precombat_proximity_threshold_px:
            entry.precombat_last = ts
            entry.precombat_signals = [
                f"proximity_within_threshold_px:{px:g}",
                f"threshold_px:{self._config.precombat_proximity_threshold_px:g}",
            ]
        self._refresh(entry, ts, entry.precombat_signals or entry.last_signals)

    def note_convergence(
        self,
        team_id: str,
        source: str,
        *,
        now: Optional[float] = None,
        signals: Optional[Sequence[str]] = None,
    ) -> None:
        """A derivations / intel convergence signal (third-party,
        gatekeep, team shape, fight heat) involving one team."""
        if not team_id:
            return
        ts = self._clock() if now is None else float(now)
        entry = self._team(team_id)
        self.stats["signals"] += 1
        entry.precombat_last = ts
        entry.precombat_signals = [
            f"convergence:{source}",
            *[str(s) for s in (signals or ())],
        ]
        entry.last_signals = list(entry.precombat_signals)
        self._refresh(entry, ts, entry.precombat_signals)

    def note_fight_heat(
        self,
        team_id: str,
        heat: float,
        *,
        now: Optional[float] = None,
        area: Optional[str] = None,
    ) -> None:
        """Derivations fight heat for a team's area: heat at/above 1.0
        (one undecayed combat event's worth) is convergence evidence."""
        try:
            value = float(heat)
        except (TypeError, ValueError):
            return
        if value < 1.0:
            return
        self.note_convergence(
            team_id,
            "fight_heat",
            now=now,
            signals=[f"heat:{value:g}"] + ([f"area:{area}"] if area else []),
        )

    def note_promoted_fight(
        self,
        team_id: str,
        fight_id: Optional[str],
        *,
        is_clutch: bool = False,
        now: Optional[float] = None,
    ) -> None:
        """A fight was promoted involving this team -> ACTIVE_COMBAT
        (CLUTCH when the promotion carries a verified clutch flag)."""
        if not team_id:
            return
        ts = self._clock() if now is None else float(now)
        entry = self._team(team_id)
        self.stats["signals"] += 1
        entry.fight_id = fight_id
        entry.fight_clutch = bool(is_clutch)
        entry.fight_last_activity = ts
        entry.last_signals = ["fight_promoted"] + (
            ["clutch_verified"] if is_clutch else []
        )
        self._refresh(entry, ts, entry.last_signals)

    def note_combat_activity(
        self, team_id: str, *, now: Optional[float] = None
    ) -> None:
        """Kill/knock activity refreshes an active fight's clock."""
        if not team_id:
            return
        ts = self._clock() if now is None else float(now)
        entry = self._teams.get(team_id)
        if entry is None or entry.fight_id is None:
            return
        entry.fight_last_activity = ts

    def note_fight_resolved(
        self,
        *,
        fight_id: Optional[str] = None,
        team_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> None:
        """The underlying fight ended: its teams leave ACTIVE_COMBAT /
        CLUTCH and fall back through the normal decay precedence."""
        ts = self._clock() if now is None else float(now)
        targets = []
        if team_id:
            entry = self._teams.get(team_id)
            if entry is not None:
                targets.append(entry)
        if fight_id:
            targets = [
                e for e in self._teams.values() if e.fight_id == fight_id
            ] or targets
        for entry in targets:
            if entry.fight_id is not None and (
                fight_id is None or entry.fight_id == fight_id
            ):
                entry.fight_id = None
                entry.fight_clutch = False
                entry.fight_last_activity = 0.0
                self._refresh(entry, ts, ["fight_resolved"])

    # ── state resolution + decay ─────────────────────────────────

    def _derive(self, entry: _TeamState, now: float) -> EngagementState:
        cfg = self._config
        if (
            entry.fight_id is not None
            and now - entry.fight_last_activity <= cfg.active_combat_ttl_s
        ):
            return (
                EngagementState.CLUTCH
                if entry.fight_clutch
                else EngagementState.ACTIVE_COMBAT
            )
        if (
            entry.precombat_last > 0
            and now - entry.precombat_last <= cfg.precombat_ttl_s
        ):
            return EngagementState.PRE_COMBAT
        if (
            entry.rotating_last > 0
            and now - entry.rotating_last <= cfg.rotating_ttl_s
        ):
            return EngagementState.ROTATING
        return baseline_for_phase(entry.phase or self._phase_now())

    def _refresh(
        self, entry: _TeamState, now: float, signals: Sequence[str]
    ) -> None:
        entry.phase = self._phase_now() or entry.phase
        # A rotation detail whose signal has gone stale is no longer
        # the team's current rotation: drop it rather than let a
        # snapshot reader mistake old enrichment for live movement.
        # (State derivation itself never reads rotation_detail.)
        if (
            entry.rotation_detail is not None
            and entry.rotating_last <= 0
        ):
            entry.rotation_detail = None
        elif (
            entry.rotation_detail is not None
            and now - entry.rotating_last > self._config.rotating_ttl_s
        ):
            entry.rotation_detail = None
        new_state = self._derive(entry, now)
        if new_state != entry.state:
            transition = {
                "team_id": entry.team_id,
                "from": entry.state.value,
                "to": new_state.value,
                "at": float(now),
                "signals": [str(s) for s in (signals or entry.last_signals)],
            }
            self._transitions.append(transition)
            self.stats["transitions"] += 1
            logger.info(
                "engagement_transition",
                team_id=entry.team_id,
                from_state=transition["from"],
                to_state=transition["to"],
                signals=transition["signals"],
            )
            entry.state = new_state
            entry.state_since = float(now)

    def tick(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Apply decay for every tracked team. Returns the transitions
        this tick produced (also appended to the transition log)."""
        ts = self._clock() if now is None else float(now)
        before = len(self._transitions)
        for entry in list(self._teams.values()):
            self._refresh(entry, ts, ["decay_check"])
            # A fight whose activity clock expired is resolved here so
            # its id never lingers as a stale claim.
            if (
                entry.fight_id is not None
                and ts - entry.fight_last_activity
                > self._config.active_combat_ttl_s
            ):
                entry.fight_id = None
                entry.fight_clutch = False
                entry.fight_last_activity = 0.0
                self._refresh(entry, ts, ["fight_activity_expired"])
        return list(self._transitions)[before:]

    # ── reads ────────────────────────────────────────────────────

    def state_of(self, team_id: str) -> EngagementState:
        entry = self._teams.get(team_id)
        if entry is None:
            return baseline_for_phase(self._phase_now())
        self._refresh(entry, self._clock(), ["read"])
        return entry.state

    def precombat_teams(self) -> List[str]:
        now = self._clock()
        out = []
        for entry in self._teams.values():
            self._refresh(entry, now, ["read"])
            if entry.state == EngagementState.PRE_COMBAT:
                out.append(entry.team_id)
        return sorted(out)

    def transitions(self, limit: int = 50) -> List[Dict[str, Any]]:
        entries = list(self._transitions)
        return entries[-max(1, int(limit)):]

    def snapshot(self) -> Dict[str, Any]:
        now = self._clock()
        teams: Dict[str, Any] = {}
        for team_id, entry in self._teams.items():
            self._refresh(entry, now, ["read"])
            teams[team_id] = {
                "team_id": team_id,
                "state": entry.state.value,
                "state_since": entry.state_since,
                "phase": entry.phase,
                "fight_id": entry.fight_id,
                "last_signals": list(entry.last_signals),
                "precombat_signals": list(entry.precombat_signals),
                # Latest rotation enrichment (screen-derived px/deg
                # audit context, or None). Never scored; see
                # note_rotation. Consumers must check "at" against
                # the rotation TTL before treating it as current.
                "rotation_detail": (
                    dict(entry.rotation_detail)
                    if entry.rotation_detail is not None
                    else None
                ),
            }
        return {
            "phase": self._phase_now(),
            "teams": teams,
            "transitions": self.transitions(),
            "stats": dict(self.stats),
        }


def build_engagement_tracker(
    config: Optional[LiveDirectorConfig] = None,
    clock: Callable[[], float] = time.time,
    phase_provider: Optional[Callable[[], Optional[str]]] = None,
) -> EngagementTracker:
    """Builder for coordinator wiring (lifespan): construct the
    tracker standalone and pass it to ``LiveDirector(engagement=...)``,
    or let the director build its own with the same defaults."""
    return EngagementTracker(
        config=config, clock=clock, phase_provider=phase_provider
    )
