# backend/app/services/observer_pilot/service.py
"""Observer Auto-Pilot service (Phase 29, W4) — the integration layer.

W1 reads POV state per feed, W2's rules engine decides, W3's executor
taps. THIS service is the wiring between them and the rest of AEGIS:

* a ~1 s loop over every monitored feed: ``monitor.pov_state(slot)``
  -> :class:`PilotSignals` -> ``rules.decide_with_note()`` -> the
  MODE POLICY of docs/OBSERVER_AUTO_PILOT.md §6, implemented exactly:

    full_auto / clone (not grabbed) -> auto-execute via the executor
    semi_auto                       -> pending suggestion (per-feed
                                       one, same-target dedupe, TTL),
                                       executes only on approve
    manual / clone grabbed          -> shadow only (journal, no tap,
                                       no suggestion)
    kill-switch OFF (any mode)      -> NO tap, NO suggestion; decisions
                                       are still evaluated for the
                                       status surface / shadow record

* killfeed involvements come from the REAL event bus (kill/knock
  topics, the same records LiveDirector consumes): killer/victim
  NAMES are mapped to roster indices through the feed's own roster
  (W1's ``match_name_to_roster``). A name that matches no roster on
  any feed is dropped and counted — never guessed.
* ``fight_active_on_feed`` comes from the LiveDirector's engagement
  snapshot (injected callable); ``phase`` from the LiveDirector /
  PhaseTracker (injected callable). Absent providers degrade
  honestly: fight flag False, phase "unknown" (rules treat it as
  non-final).
* ``last_tap_at`` updates ONLY when a tap was actually sent —
  TapResult.taps_sent > 0 (verified and failed_stay both count: the
  tap physically happened; blocked / no_device / no_frame sent
  nothing and do NOT update it).
* every outcome is journaled durably via :class:`POVJournal`.

Post-QA fixes (Phase 29, W5 PASS-WITH-NOTES defects D-1 / D-2) —
service-layer gates ON TOP of the mode policy above; the rules
engine stays stateless and untouched:

* D-2 failed-tap cooldown: an execution ending ``failed_stay``
  starts a per-feed cooldown (``failed_retry_cooldown_s``, default
  30 s). While it runs, NEW decisions on that feed are neither
  executed nor suggested — each suppression is journaled
  (``suppressed_failed_cooldown``) with the remaining cooldown —
  so a broken calibration cannot make the pilot poke a live phone
  every cycle. A ``verified`` execution clears the cooldown. A
  manual override is NEVER blocked by the cooldown (a human may
  retry on purpose), but an override's own ``failed_stay``
  restarts it. This gate sits ABOVE the dwell gate and does not
  change dwell / ``last_tap_at`` semantics.
* D-1 DEAD confirmation: ``TeamStatusReader`` (hud.py) can misread
  a low-HP ALIVE card as DEAD (reader defect, out of scope here).
  Mitigation at this layer only: an ``R1_SPECTATED_DEAD`` action
  (execute or suggest) requires the spectated player's DEAD
  reading in ``dead_confirm_evals`` (default 2) CONSECUTIVE
  evaluations of that feed. The first unconfirmed reading holds
  the action and is journaled (``dead_unconfirmed``); an
  ALIVE/KNOCKED reading resets the streak. The state is never
  relabelled — status shows the DEAD reading as unconfirmed,
  not as ALIVE. No other rule is gated, and KNOCKED readings
  need no confirmation (the defect is DEAD-only).

Loop cadence: 1 s. The monitor samples each feed on its own cadence
(Phase 26/28: a full 16-feed cycle is seconds-scale), so polling POV
state faster than ~1 s would only re-evaluate identical states; the
per-feed ``observed_at`` guard below skips states already evaluated.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import structlog

from app.core.event_names import canonical_name

from .pov_journal import POVJournal, set_pov_journal
from .roster import CachedRosterProvider
from .rules import RULE_SPECTATED_DEAD, POVPilotRules, PilotConfig, PilotSignals
from .state import match_name_to_roster
from .types import POVPlayerStatus

logger = structlog.get_logger("aegis.observer_pilot.service")

SUBSCRIBER_ID = "observer_pilot"
PILOT_EVENT_TYPES = ["kill_event", "knock_event"]

#: Engagement states that mean "this team is in a fight right now".
_FIGHT_ENGAGEMENT_STATES = frozenset({"ACTIVE_COMBAT", "CLUTCH"})

#: How long a killfeed involvement is retained per feed. The rules'
#: quiet window is seconds-scale; the retention only needs to cover
#: ``last_fight_signal_at`` bookkeeping, so it is minutes-scale and
#: pruned on every ingest.
_INVOLVEMENT_KEEP_S = 300.0


@dataclass
class PilotServiceConfig:
    tick_s: float = 1.0
    suggestion_ttl_s: float = 20.0
    pilot_config: PilotConfig = field(default_factory=PilotConfig)
    #: D-2 (Phase 29 QA): seconds a feed is held after an execution
    #: ends ``failed_stay`` before a NEW decision may execute /
    #: suggest on it again. 0 disables the gate (until == now is
    #: never in the future). Manual overrides are never blocked.
    failed_retry_cooldown_s: float = 30.0
    #: D-1 (Phase 29 QA): consecutive evaluations whose spectated
    #: DEAD reading must agree before an R1_SPECTATED_DEAD action
    #: (execute or suggest) is taken. 1 restores the pre-fix
    #: immediate behaviour. Only R1 is gated; the rules engine is
    #: unchanged — the streak is service state, like ``last_tap_at``.
    dead_confirm_evals: int = 2


def _default_mode_provider() -> Tuple[str, bool]:
    """(mode, clone_grabbed) from the DirectorService singleton."""
    try:
        from app.services.director_service import director_service

        snap = director_service.snapshot()
        return str(snap.get("mode") or "manual"), bool(snap.get("clone_grabbed"))
    except Exception:  # pragma: no cover - defensive: shadow is the safe default
        return "manual", False


def _default_engagement_provider() -> Optional[Dict[str, Any]]:
    try:
        from app.services.live_director import get_live_director

        director = get_live_director()
        return director.engagement_snapshot() if director is not None else None
    except Exception:  # pragma: no cover - defensive
        return None


def _default_phase_provider() -> Optional[str]:
    try:
        from app.services.live_director import get_live_director

        director = get_live_director()
        if director is not None:
            return director._phase()
    except Exception:  # pragma: no cover - defensive
        pass
    try:
        from app.services.phase_tracker import phase_tracker

        return phase_tracker.current_phase()
    except Exception:  # pragma: no cover - defensive
        return None


class _NullExecutor:
    """Honest stand-in when W3's executor module is unavailable:
    every attempt reports no_device and sends nothing."""

    async def execute(self, decision, now):  # pragma: no cover - fallback path
        from types import SimpleNamespace

        return SimpleNamespace(
            status="no_device", taps_sent=0,
            reason="POV tap executor unavailable in this process",
            slot_number=getattr(decision, "slot_number", None),
            target_index=getattr(decision, "to_index", None),
        )

    async def execute_target(self, slot_number, source_id, target_index, reason, now):  # pragma: no cover
        from types import SimpleNamespace

        return SimpleNamespace(
            status="no_device", taps_sent=0,
            reason="POV tap executor unavailable in this process",
            slot_number=slot_number, target_index=target_index,
        )


def _result_field(result: Any, name: str, default: Any = None) -> Any:
    if isinstance(result, dict):
        return result.get(name, default)
    return getattr(result, name, default)


class ObserverPilotService:
    """Mode-policy + execution wiring over rules / executor / journal.

    All collaborators are injectable (tests use fakes):
      monitor           — pov_state(slot) / pov_states()
      executor          — W3 POVTapExecutor contract (execute /
                          execute_target); its PilotControl is read
                          for the kill-switch when no explicit
                          ``control`` is injected
      control           — PilotControl (enabled + disabled_slots)
      mode_provider     — () -> (mode, clone_grabbed)
      engagement_provider — () -> LiveDirector engagement snapshot dict
      phase_provider    — () -> phase string
      roster_provider   — the CachedRosterProvider (refresh driven here)
      journal           — POVJournal
    """

    def __init__(
        self,
        monitor: Any = None,
        executor: Any = None,
        control: Any = None,
        *,
        rules: Optional[POVPilotRules] = None,
        config: Optional[PilotServiceConfig] = None,
        mode_provider: Optional[Callable[[], Tuple[str, bool]]] = None,
        engagement_provider: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
        phase_provider: Optional[Callable[[], Optional[str]]] = None,
        roster_provider: Optional[CachedRosterProvider] = None,
        journal: Optional[POVJournal] = None,
        bus: Any = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._monitor = monitor
        self._executor = executor
        self._control = control
        self.config = config or PilotServiceConfig()
        self._rules = rules or POVPilotRules(self.config.pilot_config)
        self._mode_provider = mode_provider or _default_mode_provider
        self._engagement_provider = engagement_provider or _default_engagement_provider
        self._phase_provider = phase_provider or _default_phase_provider
        self._roster_provider = roster_provider
        self.journal = journal or POVJournal()
        self._bus = bus
        self._clock = clock

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._subscribed = False
        # Per-feed pilot state.
        self._last_tap_at: Dict[int, float] = {}
        self._last_fight_signal_at: Dict[int, float] = {}
        self._involvements: Dict[int, List[Tuple[int, str, float]]] = {}
        self._evaluated_at: Dict[int, float] = {}
        self._last_decision: Dict[int, Dict[str, Any]] = {}
        self._last_note: Dict[int, Dict[str, Any]] = {}
        self._pending: Dict[int, Dict[str, Any]] = {}  # slot -> suggestion
        # D-2: slot -> epoch until which new decisions are suppressed
        # because the feed's last execution ended failed_stay.
        self._failed_cooldown_until: Dict[int, float] = {}
        # D-1: slot -> consecutive DEAD readings of the spectated
        # player, and the spectated index that streak belongs to
        # (a different spectated index starts a new streak).
        self._dead_streak: Dict[int, int] = {}
        self._dead_streak_index: Dict[int, Optional[int]] = {}
        self.stats: Dict[str, int] = {
            "ticks": 0,
            "evaluations": 0,
            "decisions": 0,
            "executed": 0,
            "suggested": 0,
            "shadowed": 0,
            "overrides": 0,
            "killfeed_events": 0,
            "killfeed_dropped_unmatched": 0,
            "suggestions_expired": 0,
            "suppressed_failed_cooldown": 0,
            "dead_unconfirmed": 0,
            "errors": 0,
        }

    # ── collaborators ────────────────────────────────────────────

    @property
    def executor(self) -> Any:
        return self._executor

    @property
    def roster_provider(self) -> Optional[CachedRosterProvider]:
        return self._roster_provider

    @property
    def control(self) -> Any:
        """The PilotControl in force: explicit, else the executor's."""
        if self._control is not None:
            return self._control
        return getattr(self._executor, "control", None)

    def kill_switch_enabled(self) -> Optional[bool]:
        """True/False when a PilotControl exists; None when there is
        no control object at all (reported honestly as unknown)."""
        control = self.control
        if control is None:
            return None
        return bool(getattr(control, "enabled", True))

    def _control_allows(self, slot_number: int) -> bool:
        control = self.control
        if control is None:
            return True
        allows = getattr(control, "allows", None)
        if callable(allows):
            try:
                return bool(allows(int(slot_number)))
            except Exception:  # pragma: no cover - defensive
                return False
        if not bool(getattr(control, "enabled", True)):
            return False
        disabled = getattr(control, "disabled_slots", None) or set()
        return int(slot_number) not in disabled

    def set_kill_switch(self, enabled: bool) -> bool:
        """Flip the pilot kill-switch. Returns the resulting state.

        With no PilotControl wired there is nothing to flip — returns
        the current (unknown->treated as enabled) state unchanged and
        the API surfaces that honestly."""
        control = self.control
        if control is None:
            return True
        set_enabled = getattr(control, "set_enabled", None)
        if callable(set_enabled):
            return bool(set_enabled(bool(enabled)))
        control.enabled = bool(enabled)
        return bool(control.enabled)

    def _mode(self) -> Tuple[str, bool]:
        try:
            mode, grabbed = self._mode_provider()
            return str(mode or "manual"), bool(grabbed)
        except Exception:  # pragma: no cover - defensive
            return "manual", False

    @staticmethod
    def behaviour_for(mode: str, grabbed: bool, kill_enabled: Optional[bool]) -> str:
        """The §6 mode-policy behaviour name for a mode/grab/kill state."""
        if kill_enabled is False:
            return "off_kill_switch"
        if mode == "manual" or (mode == "clone" and grabbed):
            return "shadow"
        if mode == "semi_auto":
            return "suggest"
        if mode in ("full_auto", "clone"):
            return "auto_execute"
        return "shadow"  # unknown mode: the safe behaviour is shadow

    # ── lifecycle ────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Subscribe to the bus (if any) + start the loop. Idempotent."""
        if self._running:
            return
        self._running = True
        if self._bus is not None:
            try:
                self._bus.subscribe(
                    subscriber_id=SUBSCRIBER_ID,
                    callback=self.handle_record,
                    event_types=list(PILOT_EVENT_TYPES),
                )
                self._subscribed = True
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("pilot_subscribe_failed", error=str(exc)[:200])
        try:
            self._task = asyncio.get_running_loop().create_task(
                self._run(), name="aegis-observer-pilot"
            )
        except RuntimeError:
            self._task = None  # tick() remains directly callable (tests)

    def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._subscribed and self._bus is not None:
            try:
                self._bus.unsubscribe(SUBSCRIBER_ID)
            except Exception:  # pragma: no cover - defensive
                pass
            self._subscribed = False

    async def _run(self) -> None:
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # the loop itself never dies
                self.stats["errors"] += 1
                logger.warning("pilot_tick_failed", error=str(exc)[:200])
            try:
                await asyncio.sleep(self.config.tick_s)
            except asyncio.CancelledError:
                raise

    # ── bus intake: killfeed involvements ────────────────────────

    async def handle_record(self, record: Any) -> None:
        """Bus callback: one kill/knock record -> per-feed involvements.

        Mirrors LiveDirector.handle_record's record shape. Never raises.
        Names are matched against each monitored feed's roster; an
        event whose names match NO feed's roster is dropped + counted.
        """
        try:
            event = getattr(record, "event", None)
            raw_type = getattr(event, "event_type", "") if event else ""
            data = getattr(event, "data", None) if event else None
            if not isinstance(data, dict):
                return
            event_type = canonical_name(raw_type)
            if event_type not in ("kill.occurred", "knock.occurred"):
                return
            is_knock = event_type == "knock.occurred"
            self.stats["killfeed_events"] += 1
            at = self._event_ts(data)
            killer = data.get("killer")
            victim = data.get("victim")
            states = self._pov_states()
            matched_any = False
            for slot, state in states.items():
                roster = [(p.player_id or "", p.name or "") for p in state.players]
                if not any(name for _pid, name in roster):
                    continue
                involvements: List[Tuple[int, str, float]] = []
                killer_idx = match_name_to_roster(
                    str(killer) if killer else None, roster
                )
                victim_idx = match_name_to_roster(
                    str(victim) if victim else None, roster
                )
                if killer_idx is not None:
                    involvements.append(
                        (killer_idx, "knocked" if is_knock else "killer", at)
                    )
                if victim_idx is not None:
                    involvements.append((victim_idx, "victim", at))
                if not involvements:
                    continue
                matched_any = True
                bucket = self._involvements.setdefault(slot, [])
                bucket.extend(involvements)
                self._last_fight_signal_at[slot] = max(
                    self._last_fight_signal_at.get(slot, 0.0), at
                )
            if not matched_any:
                self.stats["killfeed_dropped_unmatched"] += 1
        except Exception as exc:  # pragma: no cover - defensive
            self.stats["errors"] += 1
            logger.warning("pilot_handle_record_failed", error=str(exc)[:200])

    def _event_ts(self, data: Dict[str, Any]) -> float:
        raw = data.get("timestamp")
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            try:
                return float(raw)
            except ValueError:
                pass
        return self._clock()

    # ── signal assembly ──────────────────────────────────────────

    def _pov_states(self) -> Dict[int, Any]:
        if self._monitor is None:
            return {}
        try:
            states = self._monitor.pov_states()
            return dict(states) if states else {}
        except Exception:  # pragma: no cover - defensive
            return {}

    def _fight_active(self, state: Any) -> bool:
        if state is None or not getattr(state, "team_id", None):
            return False
        try:
            snapshot = self._engagement_provider()
        except Exception:  # pragma: no cover - defensive
            return False
        if not isinstance(snapshot, dict):
            return False
        teams = snapshot.get("teams") or {}
        entry = teams.get(state.team_id)
        if not isinstance(entry, dict):
            return False
        return str(entry.get("state") or "") in _FIGHT_ENGAGEMENT_STATES

    def _phase(self) -> str:
        try:
            phase = self._phase_provider()
        except Exception:  # pragma: no cover - defensive
            phase = None
        return str(phase) if phase else "unknown"

    # ── D-1 / D-2 service-layer gates ────────────────────────────

    def _dead_required(self) -> int:
        """Consecutive DEAD readings R1 needs (config knob, >= 1)."""
        try:
            return max(1, int(self.config.dead_confirm_evals))
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return 2

    def _update_dead_streak(self, state: Any, slot: int) -> int:
        """Track consecutive spectated-DEAD readings for one feed.

        Mirrors the rules engine's spectated lookup (roster_index
        keyed). DEAD increments the streak for THAT spectated index;
        any other reading — ALIVE / KNOCKED / UNKNOWN, an unreadable
        spectated player, unknown identity — resets it to 0. The
        reading itself is never altered, only counted."""
        spectated = None
        if getattr(state, "identity_known", False) and state.spectated_index is not None:
            by_index = {p.roster_index: p for p in state.players}
            spectated = by_index.get(state.spectated_index)
        if spectated is not None and spectated.status == POVPlayerStatus.DEAD:
            if self._dead_streak_index.get(slot) == state.spectated_index:
                streak = self._dead_streak.get(slot, 0) + 1
            else:
                streak = 1
            self._dead_streak[slot] = streak
            self._dead_streak_index[slot] = state.spectated_index
            return streak
        self._dead_streak[slot] = 0
        self._dead_streak_index[slot] = (
            state.spectated_index if spectated is not None else None
        )
        return 0

    def _cooldown_remaining(self, slot: int, now: float) -> float:
        """Seconds of failed-tap cooldown left on a feed (0 = none).

        An expired cooldown is cleared lazily here, so the feed
        returns to normal behaviour on the first evaluation at or
        after ``failed_cooldown_until``."""
        until = self._failed_cooldown_until.get(int(slot))
        if until is None:
            return 0.0
        remaining = float(until) - float(now)
        if remaining <= 0:
            del self._failed_cooldown_until[int(slot)]
            return 0.0
        return remaining

    def _note_execution_outcome(self, slot: int, status: Any, now: float) -> None:
        """D-2 bookkeeping for EVERY execution path (auto-execute,
        approved suggestion, manual override — all funnel through
        ``_execute`` / ``_execute_target``):

        * ``failed_stay`` -> (re)start the feed's cooldown at
          ``now + failed_retry_cooldown_s``;
        * ``verified``    -> clear the feed's cooldown;
        * anything else (no tap attempted / blocked) -> no change.
        """
        slot = int(slot)
        if status == "failed_stay":
            cooldown = float(self.config.failed_retry_cooldown_s or 0.0)
            if cooldown > 0:
                self._failed_cooldown_until[slot] = float(now) + cooldown
            else:
                self._failed_cooldown_until.pop(slot, None)
        elif status == "verified":
            self._failed_cooldown_until.pop(slot, None)

    def _journal_gate(
        self, decision: Any, state: Any, *, mode: str, fate: str,
        reason: str, extra: Dict[str, Any],
    ) -> None:
        """Journal one gate-held decision (D-1 / D-2 note row).

        Same row shape as ``_journal`` but with the gate's own
        reason text, ``taps_sent=0`` and no verification — no tap
        was attempted, and the row must not be mistaken for one."""
        evidence = dict(decision.evidence or {})
        evidence.update(extra)
        self.journal.record({
            "ts": float(decision.decided_at),
            "slot_number": int(decision.slot_number),
            "source_id": decision.source_id,
            "team_id": getattr(state, "team_id", None),
            "from_index": decision.from_index,
            "to_index": decision.to_index,
            "rule": decision.rule,
            "reason": reason,
            "evidence": evidence,
            "mode": mode,
            "fate": fate,
            "verification": None,
            "taps_sent": 0,
        })

    # ── the tick ─────────────────────────────────────────────────

    async def tick(self, now: Optional[float] = None) -> None:
        """One pilot pass over every monitored feed."""
        self.stats["ticks"] += 1
        if self._roster_provider is not None and self._roster_provider.due_for_refresh(
            self._clock()
        ):
            await self._roster_provider.refresh()
        self._expire_suggestions(self._clock())
        states = self._pov_states()
        for slot in sorted(states):
            try:
                await self.evaluate_feed(states[slot], now=now)
            except Exception as exc:  # one feed never blocks the others
                self.stats["errors"] += 1
                logger.warning(
                    "pilot_feed_failed", slot=slot, error=str(exc)[:200]
                )

    async def evaluate_feed(self, state: Any, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Evaluate ONE feed's POV state through rules + mode policy.

        Returns the outcome dict for this evaluation (also kept as the
        feed's last decision/note for the status surface), or None when
        the state was already evaluated (same observed_at)."""
        now = float(now if now is not None else self._clock())
        slot = int(state.slot_number)
        if self._evaluated_at.get(slot) == state.observed_at:
            return None
        self._evaluated_at[slot] = state.observed_at
        self.stats["evaluations"] += 1
        # D-1: count this fresh evaluation's spectated reading
        # toward the DEAD-confirmation streak BEFORE the rules run,
        # so the gate below sees the streak including this reading.
        dead_streak = self._update_dead_streak(state, slot)

        # Prune stale involvements (kept list is the rules' evidence).
        bucket = [
            inv for inv in self._involvements.get(slot, [])
            if now - inv[2] <= _INVOLVEMENT_KEEP_S
        ]
        self._involvements[slot] = bucket

        fight_active = self._fight_active(state)
        if fight_active:
            self._last_fight_signal_at[slot] = max(
                self._last_fight_signal_at.get(slot, 0.0), now
            )
        signals = PilotSignals(
            killfeed_involvements=list(bucket),
            fight_active_on_feed=fight_active,
            phase=self._phase(),
            now=now,
        )
        decision, note = self._rules.decide_with_note(
            state,
            signals,
            self._last_tap_at.get(slot),
            self._last_fight_signal_at.get(slot),
        )
        mode, grabbed = self._mode()
        kill_enabled = self.kill_switch_enabled()
        behaviour = self.behaviour_for(mode, grabbed, kill_enabled)

        if note is not None:
            self._last_note[slot] = {
                "slot_number": slot,
                "source_id": state.source_id,
                "reason": note.reason,
                "blocked_rule": note.blocked_rule,
                "evidence": note.evidence,
                "at": note.at,
            }
        if decision is None:
            return {"slot_number": slot, "outcome": "no_action", "note": self._last_note.get(slot)}

        self.stats["decisions"] += 1
        decision_dict = self._decision_dict(decision, state)
        self._last_decision[slot] = decision_dict

        # ── D-1 / D-2 gates ──────────────────────────────────────
        # Both gates hold only ACTION behaviours (auto_execute /
        # suggest). Shadow and kill-switch-off paths take no action
        # anyway — their journaling is unchanged, and the status
        # surface still shows the gate state per feed. D-1 is
        # checked first: an unconfirmed DEAD reading is held for
        # lack of confirmation, whatever the cooldown says.
        if behaviour in ("auto_execute", "suggest"):
            if decision.rule == RULE_SPECTATED_DEAD:
                required = self._dead_required()
                if required > 1 and dead_streak < required:
                    reason = (
                        f"dead_unconfirmed: spectated player at roster "
                        f"index {decision.from_index} read DEAD in "
                        f"{dead_streak} of {required} required "
                        f"consecutive evaluations; holding the "
                        f"R1_SPECTATED_DEAD action until the DEAD "
                        f"reading is confirmed (a low-HP ALIVE card "
                        f"can be misread as DEAD — the reading is "
                        f"shown as DEAD, unconfirmed, not relabelled)."
                    )
                    self._last_note[slot] = {
                        "slot_number": slot,
                        "source_id": state.source_id,
                        "reason": reason,
                        "blocked_rule": decision.rule,
                        "evidence": {
                            "dead_unconfirmed": True,
                            "dead_confirm_count": dead_streak,
                            "dead_confirm_evals": required,
                            "spectated_index": decision.from_index,
                            "spectated_status": "dead",
                        },
                        "at": now,
                    }
                    self._journal_gate(
                        decision, state, mode=mode, fate="dead_unconfirmed",
                        reason=reason,
                        extra={
                            "dead_unconfirmed": True,
                            "dead_confirm_count": dead_streak,
                            "dead_confirm_evals": required,
                        },
                    )
                    self.stats["dead_unconfirmed"] += 1
                    return {
                        "slot_number": slot,
                        "outcome": "dead_unconfirmed",
                        "decision": decision_dict,
                        "note": self._last_note.get(slot),
                        "dead_confirm_count": dead_streak,
                        "dead_confirm_evals": required,
                    }
            cooldown_remaining = self._cooldown_remaining(slot, now)
            if cooldown_remaining > 0:
                cooldown_until = self._failed_cooldown_until.get(slot)
                reason = (
                    f"suppressed_failed_cooldown: holding new pilot "
                    f"actions on this feed — the last execution ended "
                    f"failed_stay and {cooldown_remaining:.1f}s of the "
                    f"{float(self.config.failed_retry_cooldown_s):.1f}s "
                    f"failed-tap cooldown remain (until "
                    f"{cooldown_until}). Decision held: {decision.reason}"
                )
                self._last_note[slot] = {
                    "slot_number": slot,
                    "source_id": state.source_id,
                    "reason": reason,
                    "blocked_rule": decision.rule,
                    "evidence": {
                        "suppressed_failed_cooldown": True,
                        "suppressed_by": "failed_cooldown",
                        "failed_cooldown_until": cooldown_until,
                        "cooldown_remaining_s": cooldown_remaining,
                    },
                    "at": now,
                }
                # fate is "suppressed" (not the long marker): the
                # journal's fate column is String(16) — migration
                # 016 — so "suppressed_failed_cooldown" (26 chars)
                # cannot BE the fate without a migration; the exact
                # marker lives in the reason + evidence + outcome.
                self._journal_gate(
                    decision, state, mode=mode, fate="suppressed",
                    reason=reason,
                    extra={
                        "suppressed_failed_cooldown": True,
                        "suppressed_by": "failed_cooldown",
                        "failed_cooldown_until": cooldown_until,
                        "cooldown_remaining_s": cooldown_remaining,
                    },
                )
                self.stats["suppressed_failed_cooldown"] += 1
                return {
                    "slot_number": slot,
                    "outcome": "suppressed_failed_cooldown",
                    "decision": decision_dict,
                    "note": self._last_note.get(slot),
                    "failed_cooldown_until": cooldown_until,
                    "failed_cooldown_remaining_s": cooldown_remaining,
                }

        if behaviour == "off_kill_switch":
            # §6: evaluated for display, but execution AND suggestions
            # are suppressed. Recorded as shadow so the audit trail
            # shows what WOULD have happened while the switch was off.
            self._journal(
                decision, state, mode=mode, fate="shadow",
                extra={"suppressed_by": "kill_switch"},
            )
            self.stats["shadowed"] += 1
            return {"slot_number": slot, "outcome": "suppressed_kill_switch",
                    "decision": decision_dict}
        if behaviour == "shadow":
            self._journal(decision, state, mode=mode, fate="shadow")
            self.stats["shadowed"] += 1
            return {"slot_number": slot, "outcome": "shadow", "decision": decision_dict}
        if behaviour == "suggest":
            suggestion = self._make_suggestion(decision, state, mode, now)
            return {"slot_number": slot, "outcome": "suggested",
                    "decision": decision_dict, "suggestion": suggestion}
        # auto_execute
        result = await self._execute(decision, now)
        fate = "executed" if _result_field(result, "status") in (
            "verified", "failed_stay"
        ) else "failed"
        self._journal(
            decision, state, mode=mode, fate=fate,
            verification=_result_field(result, "status"),
            taps_sent=_result_field(result, "taps_sent"),
            extra={
                "tap_result": {
                    "status": _result_field(result, "status"),
                    "verified_index": _result_field(result, "verified_index"),
                    "calibrated": _result_field(result, "calibrated"),
                    "reason": _result_field(result, "reason")
                    or _result_field(result, "detail"),
                }
            },
        )
        if fate == "executed":
            self.stats["executed"] += 1
        return {"slot_number": slot, "outcome": fate, "decision": decision_dict,
                "verification": _result_field(result, "status")}

    # ── execution ────────────────────────────────────────────────

    async def _execute(self, decision: Any, now: float) -> Any:
        """Run one decision through the executor; update last_tap_at
        ONLY when a tap was actually sent (taps_sent > 0).

        W3's real executor is ASYNC (``async def execute``); sync
        fakes are also accepted — awaitable results are awaited."""
        import inspect

        executor = self._executor or _NullExecutor()
        result = executor.execute(decision, now)
        if inspect.isawaitable(result):
            result = await result
        taps = _result_field(result, "taps_sent", 0) or 0
        if int(taps) > 0:
            self._last_tap_at[int(decision.slot_number)] = float(now)
        # D-2: failed_stay (re)starts / verified clears the feed's
        # failed-tap cooldown, on every execution path alike.
        self._note_execution_outcome(
            int(decision.slot_number), _result_field(result, "status"), now
        )
        return result

    async def _execute_target(
        self, slot_number: int, source_id: str, target_index: int,
        reason: str, now: float,
    ) -> Any:
        import inspect

        executor = self._executor or _NullExecutor()
        result = executor.execute_target(
            int(slot_number), source_id, int(target_index), reason, now
        )
        if inspect.isawaitable(result):
            result = await result
        taps = _result_field(result, "taps_sent", 0) or 0
        if int(taps) > 0:
            self._last_tap_at[int(slot_number)] = float(now)
        # D-2: an override is never BLOCKED by the cooldown (it
        # reaches this path freely), but its outcome still counts —
        # failed_stay restarts the cooldown, verified clears it.
        self._note_execution_outcome(
            int(slot_number), _result_field(result, "status"), now
        )
        return result

    # ── suggestions (semi_auto) ──────────────────────────────────

    def _make_suggestion(
        self, decision: Any, state: Any, mode: str, now: float
    ) -> Dict[str, Any]:
        slot = int(decision.slot_number)
        existing = self._pending.get(slot)
        if existing is not None and existing["to_index"] == decision.to_index:
            # Same-target dedupe: refresh the evidence, keep the id
            # (and do NOT journal a duplicate suggestion row).
            existing["decision"] = self._decision_dict(decision, state)
            existing["reason"] = decision.reason
            return existing
        if existing is not None:
            self._expire_suggestion(existing, now, by="superseded")
        suggestion = {
            "id": str(uuid.uuid4()),
            "slot_number": slot,
            "source_id": decision.source_id,
            "team_id": state.team_id,
            "from_index": decision.from_index,
            "to_index": decision.to_index,
            "rule": decision.rule,
            "reason": decision.reason,
            "decision": self._decision_dict(decision, state),
            "mode": mode,
            "created_at": now,
            "expires_at": now + self.config.suggestion_ttl_s,
            "status": "pending",
        }
        self._pending[slot] = suggestion
        self.stats["suggested"] += 1
        self.journal.record({
            "ts": now,
            "slot_number": slot,
            "source_id": decision.source_id,
            "team_id": state.team_id,
            "from_index": decision.from_index,
            "to_index": decision.to_index,
            "rule": decision.rule,
            "reason": decision.reason,
            "evidence": decision.evidence,
            "mode": mode,
            "fate": "suggested",
            "suggestion_id": suggestion["id"],
        })
        return suggestion

    def _expire_suggestion(
        self, suggestion: Dict[str, Any], now: float, by: str = "ttl"
    ) -> None:
        slot = int(suggestion["slot_number"])
        if self._pending.get(slot) is suggestion:
            del self._pending[slot]
        self.stats["suggestions_expired"] += 1
        self.journal.record({
            "ts": now,
            "slot_number": slot,
            "source_id": suggestion.get("source_id"),
            "team_id": suggestion.get("team_id"),
            "from_index": suggestion.get("from_index"),
            "to_index": suggestion.get("to_index"),
            "rule": suggestion.get("rule"),
            "reason": suggestion.get("reason"),
            "evidence": (suggestion.get("decision") or {}).get("evidence"),
            "mode": suggestion.get("mode"),
            "fate": "expired",
            "suggestion_id": suggestion.get("id"),
            "decided_by": by,
        })

    def _expire_suggestions(self, now: float) -> None:
        for suggestion in list(self._pending.values()):
            if now >= float(suggestion["expires_at"]):
                self._expire_suggestion(suggestion, now)

    def pending_suggestions(self) -> List[Dict[str, Any]]:
        self._expire_suggestions(self._clock())
        return [dict(s) for s in self._pending.values()]

    def pending_for_slot(self, slot_number: int) -> Optional[Dict[str, Any]]:
        self._expire_suggestions(self._clock())
        pending = self._pending.get(int(slot_number))
        return dict(pending) if pending is not None else None

    def _find_suggestion(self, suggestion_id: str) -> Optional[Dict[str, Any]]:
        self._expire_suggestions(self._clock())
        for suggestion in self._pending.values():
            if suggestion["id"] == str(suggestion_id):
                return suggestion
        return None

    async def approve_suggestion(
        self, suggestion_id: str, by: Optional[str] = None
    ) -> Dict[str, Any]:
        """Approve a pending suggestion: execute its decision NOW.

        The kill-switch is re-checked at execution time (§6 invariant):
        OFF -> the tap is blocked, the suggestion stays pending, and
        the refusal is returned honestly (status blocked_kill_switch).
        Raises KeyError for an unknown/decided/expired suggestion."""
        suggestion = self._find_suggestion(suggestion_id)
        if suggestion is None:
            raise KeyError(f"unknown or expired pilot suggestion {suggestion_id!r}")
        now = self._clock()
        if not self._control_allows(int(suggestion["slot_number"])):
            return {
                "suggestion": dict(suggestion),
                "status": "blocked_kill_switch",
                "executed": False,
            }
        decision_dict = suggestion["decision"]
        decision = self._decision_from_dict(decision_dict)
        result = await self._execute(decision, now)
        status = _result_field(result, "status")
        slot = int(suggestion["slot_number"])
        del self._pending[slot]
        self.journal.record({
            "ts": now,
            "slot_number": slot,
            "source_id": suggestion.get("source_id"),
            "team_id": suggestion.get("team_id"),
            "from_index": suggestion.get("from_index"),
            "to_index": suggestion.get("to_index"),
            "rule": suggestion.get("rule"),
            "reason": suggestion.get("reason"),
            "evidence": decision_dict.get("evidence"),
            "mode": suggestion.get("mode"),
            "fate": "approved",
            "suggestion_id": suggestion["id"],
            "verification": status,
            "taps_sent": _result_field(result, "taps_sent"),
            "decided_by": by,
        })
        return {
            "suggestion": dict(suggestion),
            "status": status,
            "executed": bool((_result_field(result, "taps_sent", 0) or 0) > 0),
            "taps_sent": _result_field(result, "taps_sent"),
        }

    async def reject_suggestion(
        self, suggestion_id: str, by: Optional[str] = None
    ) -> Dict[str, Any]:
        """Reject a pending suggestion: fate journaled, never executed.
        Raises KeyError for an unknown/decided/expired suggestion."""
        suggestion = self._find_suggestion(suggestion_id)
        if suggestion is None:
            raise KeyError(f"unknown or expired pilot suggestion {suggestion_id!r}")
        now = self._clock()
        slot = int(suggestion["slot_number"])
        del self._pending[slot]
        self.journal.record({
            "ts": now,
            "slot_number": slot,
            "source_id": suggestion.get("source_id"),
            "team_id": suggestion.get("team_id"),
            "from_index": suggestion.get("from_index"),
            "to_index": suggestion.get("to_index"),
            "rule": suggestion.get("rule"),
            "reason": suggestion.get("reason"),
            "evidence": (suggestion.get("decision") or {}).get("evidence"),
            "mode": suggestion.get("mode"),
            "fate": "rejected",
            "suggestion_id": suggestion["id"],
            "decided_by": by,
        })
        return {"suggestion": dict(suggestion), "status": "rejected"}

    # ── manual override ──────────────────────────────────────────

    async def override(
        self, slot_number: int, roster_index: int, by: Optional[str] = None
    ) -> Dict[str, Any]:
        """Manual POV override: tap straight to a roster index.

        Blocked by the kill-switch like every other tap (the executor
        is the choke point; this service checks first so the journal
        and the API response are honest about WHY nothing was sent).
        NOT blocked by the D-2 failed-tap cooldown — a human may
        retry on purpose; the override's own outcome still updates
        the cooldown via ``_execute_target`` (failed_stay restarts
        it, verified clears it)."""
        now = self._clock()
        slot = int(slot_number)
        state = None
        if self._monitor is not None:
            try:
                state = self._monitor.pov_state(slot)
            except Exception:  # pragma: no cover - defensive
                state = None
        source_id = getattr(state, "source_id", "") if state is not None else ""
        team_id = getattr(state, "team_id", None) if state is not None else None
        from_index = (
            getattr(state, "spectated_index", None) if state is not None else None
        )
        mode, _grabbed = self._mode()
        reason = (
            f"Manual override by {by or 'operator'}: spectate roster "
            f"index {int(roster_index)} on slot {slot}."
        )
        if not self._control_allows(slot):
            self.journal.record({
                "ts": now, "slot_number": slot, "source_id": source_id,
                "team_id": team_id, "from_index": from_index,
                "to_index": int(roster_index), "rule": "manual_override",
                "reason": reason, "evidence": {"blocked": "kill_switch"},
                "mode": mode, "fate": "failed",
                "verification": "blocked_kill_switch", "taps_sent": 0,
                "decided_by": by,
            })
            return {
                "slot_number": slot, "roster_index": int(roster_index),
                "status": "blocked_kill_switch", "executed": False,
                "taps_sent": 0,
            }
        result = await self._execute_target(
            slot, source_id, int(roster_index), reason, now
        )
        status = _result_field(result, "status")
        taps = _result_field(result, "taps_sent", 0) or 0
        self.stats["overrides"] += 1
        self.journal.record({
            "ts": now, "slot_number": slot, "source_id": source_id,
            "team_id": team_id, "from_index": from_index,
            "to_index": int(roster_index), "rule": "manual_override",
            "reason": reason,
            "evidence": {
                # W3 TapResult calls it `reason`; older fakes used `detail`.
                "detail": _result_field(result, "reason")
                or _result_field(result, "detail"),
                "verified_index": _result_field(result, "verified_index"),
                "calibrated": _result_field(result, "calibrated"),
            },
            "mode": mode,
            "fate": "override" if status in ("verified", "failed_stay") else "failed",
            "verification": status, "taps_sent": taps, "decided_by": by,
        })
        return {
            "slot_number": slot, "roster_index": int(roster_index),
            "status": status, "executed": bool(int(taps) > 0),
            "taps_sent": taps,
        }

    # ── journal / dict helpers ───────────────────────────────────

    def _journal(
        self, decision: Any, state: Any, *, mode: str, fate: str,
        verification: Optional[str] = None, taps_sent: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        evidence = dict(decision.evidence or {})
        if extra:
            evidence.update(extra)
        self.journal.record({
            "ts": float(decision.decided_at),
            "slot_number": int(decision.slot_number),
            "source_id": decision.source_id,
            "team_id": getattr(state, "team_id", None),
            "from_index": decision.from_index,
            "to_index": decision.to_index,
            "rule": decision.rule,
            "reason": decision.reason,
            "evidence": evidence,
            "mode": mode,
            "fate": fate,
            "verification": verification,
            "taps_sent": taps_sent,
        })

    @staticmethod
    def _decision_dict(decision: Any, state: Any) -> Dict[str, Any]:
        return {
            "slot_number": int(decision.slot_number),
            "source_id": decision.source_id,
            "team_id": getattr(state, "team_id", None),
            "from_index": decision.from_index,
            "to_index": decision.to_index,
            "rule": decision.rule,
            "reason": decision.reason,
            "evidence": decision.evidence,
            "decided_at": decision.decided_at,
        }

    @staticmethod
    def _decision_from_dict(data: Dict[str, Any]) -> Any:
        from .rules import POVDecision

        return POVDecision(
            slot_number=int(data["slot_number"]),
            source_id=str(data.get("source_id") or ""),
            from_index=data.get("from_index"),
            to_index=int(data["to_index"]),
            rule=str(data.get("rule") or ""),
            reason=str(data.get("reason") or ""),
            evidence=dict(data.get("evidence") or {}),
            decided_at=float(data.get("decided_at") or time.time()),
        )

    # ── status surface ───────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        mode, grabbed = self._mode()
        kill_enabled = self.kill_switch_enabled()
        behaviour = self.behaviour_for(mode, grabbed, kill_enabled)
        states = self._pov_states()
        status_now = self._clock()
        dead_required = self._dead_required()
        feeds: List[Dict[str, Any]] = []
        for slot in sorted(states):
            state = states[slot]
            cooldown_remaining = self._cooldown_remaining(slot, status_now)
            dead_streak = self._dead_streak.get(slot, 0)
            feeds.append({
                "slot_number": slot,
                "source_id": state.source_id,
                "pov": state.to_dict(),
                "behaviour": behaviour,
                "pending_suggestion": self.pending_for_slot(slot),
                "last_decision": self._last_decision.get(slot),
                "last_note": self._last_note.get(slot),
                "last_tap_at": self._last_tap_at.get(slot),
                "last_fight_signal_at": self._last_fight_signal_at.get(slot),
                # D-2: failed-tap cooldown state for this feed.
                "failed_cooldown_active": cooldown_remaining > 0,
                "failed_cooldown_until": (
                    self._failed_cooldown_until.get(slot)
                    if cooldown_remaining > 0 else None
                ),
                "failed_cooldown_remaining_s": cooldown_remaining,
                # D-1: DEAD-confirmation state — the streak is > 0
                # only while the latest readings are DEAD, so
                # dead_unconfirmed means "spectated reads DEAD but
                # R1 is still held for confirmation". The POV state
                # itself is NOT relabelled.
                "dead_confirm_count": dead_streak,
                "dead_confirm_evals": dead_required,
                "dead_unconfirmed": 0 < dead_streak < dead_required,
            })
        control = self.control
        disabled_slots = []
        if control is not None:
            disabled_slots = sorted(
                int(s) for s in (getattr(control, "disabled_slots", None) or set())
            )
        calibration = self._calibration()
        return {
            "running": self._running,
            "mode": mode,
            "clone_grabbed": grabbed if mode == "clone" else False,
            "behaviour": behaviour,
            "kill_switch": {
                "enabled": kill_enabled,
                "disabled_slots": disabled_slots,
                "control_wired": control is not None,
            },
            "executor_available": self._executor is not None,
            "calibration": calibration,
            "suggestion_ttl_s": self.config.suggestion_ttl_s,
            "failed_retry_cooldown_s": self.config.failed_retry_cooldown_s,
            "dead_confirm_evals": self._dead_required(),
            "feeds": feeds,
            "pending_suggestions": self.pending_suggestions(),
            "stats": dict(self.stats),
            "journal": self.journal.status(),
            "roster": (
                self._roster_provider.status()
                if self._roster_provider is not None
                else None
            ),
        }

    def _calibration(self) -> Dict[str, Any]:
        """Per-device tap-point calibration flags from W3's config.

        W3's real shape: the executor carries ``calibration`` (a
        TapCalibration: profiles / per_serial / calibrated_profiles)
        directly and via ``config.calibration``. Surfaced verbatim:
        which profiles/serials exist and which of them are REALLY
        calibrated (the honesty ledger) — the built-in default
        profile reports calibrated=False until a scrim measures it.
        Nothing is invented when the executor reports nothing."""
        executor = self._executor
        if executor is None:
            return {"available": False, "devices": {}, "profiles": [],
                    "calibrated_profiles": []}
        calibration = getattr(executor, "calibration", None)
        if calibration is None:
            config = getattr(executor, "config", None)
            calibration = getattr(config, "calibration", None)
        if calibration is not None and hasattr(calibration, "profiles"):
            calibrated = {
                str(k) for k in (getattr(calibration, "calibrated_profiles", None) or set())
            }
            per_serial = getattr(calibration, "per_serial", None) or {}
            return {
                "available": True,
                "profiles": sorted(
                    str(k) for k in (calibration.profiles or {})
                ),
                "calibrated_profiles": sorted(calibrated),
                "default_calibrated": "default" in calibrated,
                "devices": {
                    str(serial): {"calibrated": str(serial) in calibrated}
                    for serial in per_serial
                },
            }
        return {"available": False, "devices": {}, "profiles": [],
                "calibrated_profiles": []}


# ── process-wide singleton + production builder ──────────────────

_service: Optional[ObserverPilotService] = None


def get_pilot_service() -> Optional[ObserverPilotService]:
    return _service


def set_pilot_service(service: Optional[ObserverPilotService]) -> None:
    global _service
    _service = service


def _frame_provider_for_monitor(monitor: Any):
    """W3 frame_provider_for over the monitor's FeedMonitor specs:
    the feed's own frame_provider (the grabber's latest frame for
    that source). None when the feed is not monitored — the executor
    then honestly reports no_frame."""

    def _provide(slot_number: int, source_id: str):
        feed = None
        get_feed = getattr(monitor, "get_feed", None)
        if callable(get_feed) and source_id:
            feed = get_feed(source_id)
        if feed is None:
            states = getattr(monitor, "_feeds", {}) or {}
            for candidate in states.values():
                spec = getattr(candidate, "spec", None)
                if spec is not None and int(spec.slot_number) == int(slot_number):
                    feed = candidate
                    break
        if feed is None:
            return None
        provider = getattr(getattr(feed, "spec", None), "frame_provider", None)
        if not callable(provider):
            return None
        try:
            return provider()
        except Exception:  # pragma: no cover - defensive
            return None

    return _provide


def _spectated_reader():
    """W3 spectated_reader over W1's POVReader: the highlight index
    read from one frame image (None = identity not readable)."""
    try:
        from app.vision.detectors.pov_reader import POVReader

        reader = POVReader()
    except Exception:  # pragma: no cover - defensive
        return None

    def _read(image: Any) -> Optional[int]:
        try:
            return reader.read(image).highlight_index
        except Exception:  # pragma: no cover - defensive
            return None

    return _read


def build_observer_pilot(
    monitor: Any,
    bus: Any = None,
    session_factory: Optional[Callable[[], Any]] = None,
    roster_provider: Optional[CachedRosterProvider] = None,
    multi_device_manager: Any = None,
) -> ObserverPilotService:
    """Production wiring (lifespan, failure-isolated by the caller).

    The lifespan creates ONE ``CachedRosterProvider``, threads it into
    ``build_multi_feed_monitor(roster_provider=...)`` (so the POV
    trackers bind identities) AND passes it here (so this service's
    loop drives its 30 s refresh). When no provider is passed, one is
    created here. W3's executor/control are built from their own
    builder when the module is present; absent W3, the service still
    runs (shadow/status/journal) with taps honestly reporting
    no_device."""
    roster = roster_provider or CachedRosterProvider(
        session_factory=session_factory
    )
    journal = POVJournal(session_factory=session_factory)
    set_pov_journal(journal)

    executor = None
    control = None
    try:
        # W3's real wiring (executor.py has no builder of its own —
        # W4 owns this assembly, per the executor's audit note):
        #   adb              — the Phase 14 ADBController (async tap)
        #   device_resolver  — from the multi-device capture manager
        #                      (the only slot->serial binding); absent
        #                      manager -> W3's no_device_resolver, so
        #                      every tap honestly ends no_device
        #   frame_provider   — the monitor feeds' own frame providers
        #   spectated_reader — POVReader's highlight read
        #   config           — ExecutorConfig() loads the calibration
        #                      JSON from AEGIS_POV_CALIBRATION_PATH
        from app.vision.capture.adb_controller import ADBController

        from .config import ExecutorConfig
        from .executor import (
            POVTapExecutor,
            PilotControl,
            device_resolver_from_multi_device_manager,
        )

        control = PilotControl()
        resolver = (
            device_resolver_from_multi_device_manager(multi_device_manager)
            if multi_device_manager is not None
            else None
        )
        executor = POVTapExecutor(
            adb=ADBController(),
            config=ExecutorConfig(),
            control=control,
            device_resolver=resolver,
            frame_provider_for=_frame_provider_for_monitor(monitor),
            spectated_reader=_spectated_reader(),
        )
    except Exception as exc:  # W3 unavailable: honest degrade
        logger.warning("pilot_executor_unavailable", error=str(exc)[:200])
        executor = None
        control = None

    service = ObserverPilotService(
        monitor=monitor,
        executor=executor,
        control=control,
        roster_provider=roster,
        journal=journal,
        bus=bus,
    )
    set_pilot_service(service)
    return service


__all__ = [
    "ObserverPilotService",
    "PilotServiceConfig",
    "build_observer_pilot",
    "get_pilot_service",
    "set_pilot_service",
    "PILOT_EVENT_TYPES",
    "SUBSCRIBER_ID",
]
