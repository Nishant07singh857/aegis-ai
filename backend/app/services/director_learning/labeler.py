# backend/app/services/director_learning/labeler.py
"""Outcome labeler — automatic labels for past director decisions
(Phase 27, W2).

The labeler watches the SAME canonical bus topics the LiveDirector
consumes (``LIVE_DIRECTOR_EVENT_TYPES``), keeps a rolling in-memory
window of combat events, and — once a journal decision's evidence
window has fully closed — evaluates four evidence-backed label rules
against what ACTUALLY happened next:

* ``good_call``       — a switch landed on a fight/team that then
  produced real combat (the cut was worth making).
* ``fizzle``          — a switch landed and the target fight/team
  produced ZERO combat across the whole phase-scaled hold window and
  was never seen active afterwards (the cut went nowhere).
* ``premature_exit``  — a switch LEFT an incumbent fight that then
  erupted within 20 s (the director left too early). Judges the EXIT;
  may coexist with ``good_call`` / ``fizzle`` on the same decision,
  which judge the LANDING.
* ``missed_fight``    — a challenger was turned away
  (``rejected_challenger`` / non-manual ``hold``) and its fight then
  erupted within 30 s while the incumbent's fight stayed quiet.

Honesty rules (non-negotiable)
------------------------------
* A label is a claim backed by real tracked events, or it does not
  exist. Neutral / ambiguous decisions stay UNLABELED — no label is
  ever forced to make the numbers look complete.
* Attribution is taken ONLY as present in event payloads (fight
  ``teams_involved`` / ``teams``; kill/knock ``killerTeam`` /
  ``victimTeam`` and their snake_case variants). An event with no
  team/fight link simply cannot serve as correlated evidence. Team
  strings are matched after a cosmetic normalisation (case /
  spaces / hyphens / underscores removed) so ``"Team B"`` and
  ``"team-b"`` correlate — normalisation never CREATES attribution.
* Coverage: if the tracked-event window does not cover a decision's
  evidence window (labeler started late, bus gap, events pruned),
  the decision is SKIPPED — left unlabeled and counted in
  ``status()["stats"]["skipped_no_coverage"]`` — never guessed.
* Idempotence: one label row per decision per label class, enforced
  in code (the table has no unique constraint). Re-running a pass
  over the same data adds zero rows.

Switch decisions (what counts as an actual cut)
-----------------------------------------------
* ``mode in ("full_auto", "clone")`` with ``outcome == "switch"``
  (Phase 27 clone delta: clone cuts are real switches, treated
  exactly like full_auto cuts), OR
* a ``semi_auto`` / ``clone`` suggestion (``outcome == "suggestion"``)
  whose journal fate is ``approved`` (via ``queries.fate_for_suggestion``).

Unapproved suggestions are NOT switches. Clone-grabbed shadow entries
(``hold`` rows whose rule contains ``"grabbed"``, e.g.
``clone_grabbed_no_write`` — logged while a human holds control inside
clone mode) are NOT decisions for labeling purposes and are excluded
from ``missed_fight`` exactly like manual-mode entries. Synthetic
mode/grab/release transition rows (``outcome == "mode_transition"``)
are never labeled.

Time base: the journal decision's own ``ts``. Event timestamps come
from the event payload's ``timestamp`` (epoch seconds — the
NormalizedEvent bus shape) or ``timestamp_ms`` / ``ts`` when present,
falling back to the bus record's ``timestamp_ms``, then to receipt
time. Windows are ``(decision_ts, decision_ts + W]``.

Constants (all windows in seconds)
-----------------------------------
``EVENT_RETENTION_S = 300``   rolling tracked-event retention
                              (>= the briefed 120 s minimum).
``GOOD_CALL_WINDOW_S = 45``   post-switch observation window.
``GOOD_CALL_MIN_COMBAT = 3``  combat events for F/T in the window, OR
``GOOD_CALL_MIN_KILLS = 2``   kills BY T in the window.
``FIZZLE_MIN_WINDOW_S = 20``  fizzle window W = max(20, the phase's
                              ``min_fight_hold_s`` — imported from the
                              live director, never duplicated).
``PREMATURE_WINDOW_S = 20``   incumbent-eruption window after a switch.
``PREMATURE_MIN_COMBAT = 3``  / ``PREMATURE_MIN_KILLS = 2``.
``MISSED_WINDOW_S = 30``      turned-away challenger window.
``MISSED_MIN_COMBAT = 4``     / ``MISSED_MIN_KILLS = 2``, with the
``MISSED_MAX_INCUMBENT_COMBAT = 1`` incumbent-quiet condition.
``LABEL_INTERVAL_S = 30``     periodic pass cadence when started.

Confidence formulas (band: high >= 0.8, medium 0.5–<0.8, low < 0.5)
------------------------------------------------------------------
* good_call / premature_exit / missed_fight (count-based):
  ``min(0.95, 0.50 + 0.07 * combat + 0.12 * kills)`` over the events
  that fired the rule.
* fizzle: ``0.85`` when the full window elapsed with zero target
  events, ``0.95`` when the target also stayed silent for a further
  30 s of covered time after the window.

Startup wiring is applied by the coordinator at integration (see the
Phase 27 W2 report); :func:`build_outcome_labeler` is the production
builder the lifespan snippet calls.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import structlog
from sqlalchemy import select

from app.core.event_names import canonical_name
from app.services.live_director.service import (
    LIVE_DIRECTOR_EVENT_TYPES,
    min_fight_hold_s,
)

from . import queries
from .models import DirectorDecisionLabel

logger = structlog.get_logger("aegis.director_learning.labeler")

#: Subscriber id on the event bus (distinct from LiveDirector's).
SUBSCRIBER_ID = "director_learning_labeler"

#: Labeler version stamped on every row this implementation writes.
LABELER_VERSION = "w2-v1"

#: Rolling tracked-event retention (briefed minimum: 120 s).
EVENT_RETENTION_S = 300.0

#: Periodic pass cadence for the background loop started by start().
LABEL_INTERVAL_S = 30.0

#: good_call: observation window + thresholds.
GOOD_CALL_WINDOW_S = 45.0
GOOD_CALL_MIN_COMBAT = 3
GOOD_CALL_MIN_KILLS = 2

#: fizzle: window is max(this floor, the phase's min fight hold).
FIZZLE_MIN_WINDOW_S = 20.0

#: premature_exit: incumbent-eruption window + thresholds.
PREMATURE_WINDOW_S = 20.0
PREMATURE_MIN_COMBAT = 3
PREMATURE_MIN_KILLS = 2

#: missed_fight: challenger-eruption window + thresholds + the
#: incumbent-quiet condition.
MISSED_WINDOW_S = 30.0
MISSED_MIN_COMBAT = 4
MISSED_MIN_KILLS = 2
MISSED_MAX_INCUMBENT_COMBAT = 1

#: Modes whose ``switch`` outcome is an actual cut (clone delta:
#: clone cuts are real switches, exactly like full_auto cuts).
SWITCH_MODES = ("full_auto", "clone")

#: Topics that count as combat evidence. ``intel_alert`` and
#: ``alive_count_update`` are tracked (same subscription as the live
#: director) but are NOT combat and never count toward thresholds.
COMBAT_TOPICS = ("fight.detected", "kill.occurred", "knock.occurred")
KILL_TOPIC = "kill.occurred"


def confidence_band(confidence: float) -> str:
    """high >= 0.8, medium 0.5–<0.8, low < 0.5."""
    if confidence >= 0.8:
        return "high"
    if confidence >= 0.5:
        return "medium"
    return "low"


def _count_confidence(combat: int, kills: int) -> float:
    """Count-based confidence shared by good_call / premature_exit /
    missed_fight: grows with evidence, capped at 0.95."""
    return min(0.95, 0.50 + 0.07 * combat + 0.12 * kills)


def _norm_team(value: Any) -> Optional[str]:
    """Cosmetic team-key normalisation for correlation ONLY.

    Lowercases and strips spaces/hyphens/underscores so the same team
    spelled ``"Team B"`` (kill payloads) and ``"team-b"`` (decision
    payloads) correlates. Never creates attribution: an absent or
    ``"Unknown"`` team stays absent.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "Unknown":
        return None
    return "".join(ch for ch in text.lower() if ch not in " -_")


def _clean_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text != "Unknown" else None


def _parse_event_ts(data: Dict[str, Any], record: Any, receipt_ts: float) -> float:
    """Event time: payload timestamp (epoch s / ms / ISO) first, then
    the bus record's timestamp_ms, then receipt time."""
    for key in ("timestamp", "ts"):
        raw = data.get(key)
        if isinstance(raw, (int, float)) and raw > 0:
            # Values past ~year-33658 in seconds are milliseconds.
            return float(raw) / 1000.0 if raw > 1e11 else float(raw)
        if isinstance(raw, str) and raw:
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                return parsed.timestamp()
            except ValueError:
                pass
    raw_ms = data.get("timestamp_ms")
    if isinstance(raw_ms, (int, float)) and raw_ms > 0:
        return float(raw_ms) / 1000.0
    event = getattr(record, "event", None)
    record_ms = getattr(event, "timestamp_ms", None)
    if isinstance(record_ms, (int, float)) and record_ms > 0:
        return float(record_ms) / 1000.0
    return receipt_ts


def _decision_epoch(ts_value: Any) -> Optional[float]:
    if isinstance(ts_value, datetime):
        dt = ts_value if ts_value.tzinfo else ts_value.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    if isinstance(ts_value, str):
        try:
            parsed = datetime.fromisoformat(ts_value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    if isinstance(ts_value, (int, float)):
        return float(ts_value)
    return None


class OutcomeLabeler:
    """Automatic outcome labels for past director decisions (W2).

    ``bus`` is the shared EventBus (the same instance handed to
    ``build_live_director`` in the lifespan). ``session_factory`` is
    injectable for tests, matching the queries/journal contract
    (default: ``app.db.base.AsyncSessionLocal`` via queries/models).
    ``clock`` is an injectable epoch-seconds callable for tests.
    """

    def __init__(
        self,
        bus: Any,
        session_factory: Optional[Callable[[], Any]] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._bus = bus
        self._session_factory = session_factory
        self._clock = clock
        self._started = False
        self._task: Optional[asyncio.Task] = None
        #: Tracked events, oldest first:
        #: {id, ts, topic, fight_id, teams(set of norm keys),
        #:  killer_teams(set of norm keys), raw_teams(list as present)}
        self._events: List[Dict[str, Any]] = []
        #: fight_id -> set of norm team keys, from fight.detected.
        self._fight_teams: Dict[str, Set[str]] = {}
        #: Epoch from which tracked coverage is claimed (start time).
        self._coverage_start: Optional[float] = None
        #: Events with ts below this epoch have been pruned; decisions
        #: whose evidence window starts earlier lack full coverage.
        self._expired_before: float = 0.0
        self.stats: Dict[str, int] = {
            "events_received": 0,
            "events_tracked": 0,
            "passes": 0,
            "decisions_evaluated": 0,
            "labels_written": 0,
            "skipped_no_coverage": 0,
            "skipped_mode_transition": 0,
            "deferred_window_open": 0,
            "handler_errors": 0,
            "pass_errors": 0,
        }
        self._last_pass: Dict[str, Any] = {}
        self._last_error = ""

    # ── lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        """Subscribe to the live director's bus topics + start the
        periodic labeling loop (when a loop is running). Idempotent.
        Coverage is claimed from this moment onward only."""
        if self._started:
            return
        if self._coverage_start is None:
            self._coverage_start = self._clock()
        if self._bus is not None:
            self._bus.subscribe(
                subscriber_id=SUBSCRIBER_ID,
                callback=self.handle_record,
                event_types=list(LIVE_DIRECTOR_EVENT_TYPES),
            )
        self._started = True
        try:
            self._task = asyncio.get_running_loop().create_task(self._run())
        except RuntimeError:
            self._task = None  # run_pass() remains callable manually
        logger.info(
            "outcome_labeler_subscribed",
            event_types=LIVE_DIRECTOR_EVENT_TYPES,
            labeler_version=LABELER_VERSION,
        )

    def stop(self) -> None:
        if not self._started:
            return
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._bus is not None:
            try:
                self._bus.unsubscribe(SUBSCRIBER_ID)
            except Exception:  # pragma: no cover - defensive
                pass
        self._started = False

    @property
    def running(self) -> bool:
        return self._started

    async def _run(self) -> None:
        while self._started:
            try:
                await asyncio.sleep(LABEL_INTERVAL_S)
                await self.run_pass()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                self.stats["pass_errors"] += 1
                self._last_error = str(exc)[:300]
                logger.warning("outcome_labeler_pass_failed", error=self._last_error)

    # ── bus intake ───────────────────────────────────────────────

    async def handle_record(self, record: Any) -> None:
        """Bus callback: track one event in the rolling window.

        Never raises. Attribution is copied from the payload exactly
        as present — nothing is resolved, mapped or guessed.
        """
        try:
            event = getattr(record, "event", None)
            raw_type = getattr(event, "event_type", "") if event else ""
            data = getattr(event, "data", None) if event else None
            if not isinstance(data, dict):
                return
            topic = canonical_name(raw_type or "")
            self.stats["events_received"] += 1
            receipt_ts = self._clock()
            ts = _parse_event_ts(data, record, receipt_ts)

            fight_id = _clean_str(
                data.get("fight_id") or data.get("fightId")
            )
            teams: Set[str] = set()
            killer_teams: Set[str] = set()
            raw_teams: List[str] = []

            def note_team(value: Any, *, killer: bool = False) -> None:
                norm = _norm_team(value)
                if norm is None:
                    return
                teams.add(norm)
                raw_teams.append(str(value))
                if killer:
                    killer_teams.add(norm)

            if topic == "fight.detected":
                for raw in data.get("teams_involved") or data.get("teams") or []:
                    if isinstance(raw, dict):
                        note_team(raw.get("team_id") or raw.get("team"))
                    else:
                        note_team(raw)
                if fight_id and teams:
                    self._fight_teams.setdefault(fight_id, set()).update(teams)
            elif topic in ("kill.occurred", "knock.occurred"):
                note_team(
                    data.get("killerTeam")
                    or data.get("killer_team_id")
                    or data.get("killer_team")
                    or data.get("team"),
                    killer=True,
                )
                note_team(
                    data.get("victimTeam")
                    or data.get("victim_team_id")
                    or data.get("victim_team")
                )
                if data.get("fight_id"):
                    fight_id = _clean_str(data.get("fight_id"))
            # intel_alert / alive_count_update: tracked for the record
            # (topic counts), but they carry no combat attribution this
            # labeler may use, so no teams are extracted from them.

            event_id = getattr(event, "event_id", None) or data.get("id")
            self._events.append(
                {
                    "id": str(event_id) if event_id else f"tracked-{len(self._events)}-{ts}",
                    "ts": ts,
                    "topic": topic,
                    "fight_id": fight_id,
                    "teams": teams,
                    "killer_teams": killer_teams,
                    "raw_teams": raw_teams,
                }
            )
            self.stats["events_tracked"] += 1
            self._prune(receipt_ts)
        except Exception as exc:  # a broken event must not break labeling
            self.stats["handler_errors"] += 1
            self._last_error = str(exc)[:300]
            logger.warning("outcome_labeler_event_failed", error=self._last_error)

    def _prune(self, now: float) -> None:
        cutoff = now - EVENT_RETENTION_S
        if self._events and self._events[0]["ts"] < cutoff:
            self._events = [e for e in self._events if e["ts"] >= cutoff]
            self._expired_before = max(self._expired_before, cutoff)
            live_fights = {e["fight_id"] for e in self._events if e["fight_id"]}
            self._fight_teams = {
                fid: teams
                for fid, teams in self._fight_teams.items()
                if fid in live_fights
            }

    # ── correlation helpers ──────────────────────────────────────

    def _matches(
        self, event: Dict[str, Any], fight_id: Optional[str], team_norm: Optional[str]
    ) -> bool:
        """Event <-> (fight, team) correlation, payload attribution only."""
        if fight_id and event["fight_id"] == fight_id:
            return True
        if team_norm and team_norm in event["teams"]:
            return True
        if fight_id and team_norm is None:
            # Fight-only correlation also reaches kill/knock events by
            # a team the tracked fight.detected named as involved.
            fight_teams = self._fight_teams.get(fight_id) or set()
            if event["teams"] & fight_teams:
                return True
        return False

    def _kill_matches(
        self, event: Dict[str, Any], fight_id: Optional[str], team_norm: Optional[str]
    ) -> bool:
        """Kill BY the target: killer-side attribution only."""
        if event["topic"] != KILL_TOPIC:
            return False
        if team_norm and team_norm in event["killer_teams"]:
            return True
        if fight_id and event["fight_id"] == fight_id:
            return True
        if fight_id:
            fight_teams = self._fight_teams.get(fight_id) or set()
            if event["killer_teams"] & fight_teams:
                return True
        return False

    def _window_events(
        self, start: float, end: float, fight_id: Optional[str], team_norm: Optional[str]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """(combat events, kill events) in (start, end] correlated to
        the target. Non-combat topics never count."""
        combat: List[Dict[str, Any]] = []
        kills: List[Dict[str, Any]] = []
        for event in self._events:
            if not (start < event["ts"] <= end):
                continue
            if event["topic"] not in COMBAT_TOPICS:
                continue
            if self._matches(event, fight_id, team_norm):
                combat.append(event)
            if self._kill_matches(event, fight_id, team_norm):
                kills.append(event)
        return combat, kills

    @staticmethod
    def _evidence(
        *,
        rule: str,
        window_s: float,
        target: Dict[str, Any],
        matched: List[Dict[str, Any]],
        kills: List[Dict[str, Any]],
        inputs: Dict[str, Any],
    ) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for event in matched:
            counts[event["topic"]] = counts.get(event["topic"], 0) + 1
        for event in kills:
            if event not in matched:
                counts[event["topic"]] = counts.get(event["topic"], 0) + 1
        return {
            "rule": rule,
            "window_s": window_s,
            "target": target,
            "event_ids": [e["id"] for e in matched]
            + [e["id"] for e in kills if e not in matched],
            "counts_by_topic": counts,
            "combat_events": len(matched),
            "kill_events": len(kills),
            "inputs": inputs,
        }

    # ── decision shape helpers ───────────────────────────────────

    @staticmethod
    def _target_of(block: Optional[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
        """(fight_id, team_norm) from a challenger/incumbent block."""
        if not isinstance(block, dict):
            return None, None
        return _clean_str(block.get("fight_id")), _norm_team(block.get("team_id"))

    @staticmethod
    def _candidate_fight_for(
        decision: Dict[str, Any], team_norm: Optional[str]
    ) -> Optional[str]:
        """Fight id recorded on the decision's candidate row for a
        team (incumbent blocks carry no fight_id of their own)."""
        if team_norm is None:
            return None
        for candidate in decision.get("candidates") or []:
            if isinstance(candidate, dict) and _norm_team(candidate.get("team_id")) == team_norm:
                fight_id = _clean_str(candidate.get("fight_id"))
                if fight_id:
                    return fight_id
        return None

    # ── the pass ─────────────────────────────────────────────────

    def _factory(self):
        if self._session_factory is not None:
            return self._session_factory
        from app.db.base import AsyncSessionLocal

        return AsyncSessionLocal

    async def _existing_label_classes(self, decision_id: str) -> Set[str]:
        async with self._factory()() as session:
            query = select(DirectorDecisionLabel.label).where(
                DirectorDecisionLabel.decision_id == str(decision_id)
            )
            rows = (await session.execute(query)).scalars().all()
            return {str(r) for r in rows}

    async def _persist_labels(
        self, decision_id: str, specs: List[Dict[str, Any]]
    ) -> int:
        """Insert label rows, one per label class, skipping classes
        that already exist for this decision (idempotence enforced in
        code — the table has no unique constraint)."""
        existing = await self._existing_label_classes(decision_id)
        fresh = [s for s in specs if s["label"] not in existing]
        if not fresh:
            return 0
        async with self._factory()() as session:
            for spec in fresh:
                session.add(
                    DirectorDecisionLabel(
                        decision_id=str(decision_id),
                        label=spec["label"],
                        confidence=spec["confidence"],
                        confidence_band=confidence_band(spec["confidence"]),
                        evidence=spec["evidence"],
                        labeler_version=LABELER_VERSION,
                    )
                )
            await session.commit()
        return len(fresh)

    async def run_pass(self) -> Dict[str, Any]:
        """One labeling pass over unlabeled decisions whose evidence
        windows have fully closed. Directly callable (tests / ops);
        also driven periodically by start(). Returns a pass summary.
        Never raises into the caller on a per-decision failure."""
        now = self._clock()
        self._prune(now)
        self.stats["passes"] += 1
        summary: Dict[str, Any] = {
            "evaluated": 0,
            "labels_written": 0,
            "skipped_no_coverage": 0,
            "skipped_mode_transition": 0,
            "deferred_window_open": 0,
        }
        now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
        try:
            decisions = await queries.unlabeled_decisions(
                before_ts=now_dt,
                limit=200,
                session_factory=self._session_factory,
            )
        except Exception as exc:
            self.stats["pass_errors"] += 1
            self._last_error = str(exc)[:300]
            logger.warning("outcome_labeler_query_failed", error=self._last_error)
            self._last_pass = {**summary, "error": self._last_error}
            return self._last_pass

        for decision in decisions:
            try:
                await self._label_decision(decision, now, summary)
            except Exception as exc:  # one bad decision never blocks the pass
                self.stats["pass_errors"] += 1
                self._last_error = str(exc)[:300]
                logger.warning(
                    "outcome_labeler_decision_failed",
                    decision_id=str(decision.get("id")),
                    error=self._last_error,
                )
        self.stats["decisions_evaluated"] += summary["evaluated"]
        self.stats["labels_written"] += summary["labels_written"]
        self.stats["skipped_no_coverage"] += summary["skipped_no_coverage"]
        self.stats["skipped_mode_transition"] += summary["skipped_mode_transition"]
        self.stats["deferred_window_open"] += summary["deferred_window_open"]
        summary["at"] = now
        self._last_pass = summary
        return summary

    async def _label_decision(
        self, decision: Dict[str, Any], now: float, summary: Dict[str, Any]
    ) -> None:
        outcome = str(decision.get("outcome") or "")
        mode = str(decision.get("mode") or "")
        rule = str(decision.get("rule") or "")

        # Synthetic mode/grab/release transitions are journal rows,
        # not director decisions — never labeled.
        if outcome == "mode_transition":
            summary["skipped_mode_transition"] += 1
            return

        ts = _decision_epoch(decision.get("ts"))
        if ts is None:
            summary["skipped_no_coverage"] += 1
            return

        # Coverage: the labeler must have been tracking since at or
        # before the decision, and the decision's window must not
        # reach back into pruned-away history.
        if (
            self._coverage_start is None
            or ts < self._coverage_start
            or ts < self._expired_before
        ):
            summary["skipped_no_coverage"] += 1
            return

        # ── is this a switch decision? ──
        is_switch = False
        if outcome == "switch" and mode in SWITCH_MODES:
            is_switch = True
        elif outcome == "suggestion" and decision.get("suggestion_id"):
            fate = await queries.fate_for_suggestion(
                str(decision["suggestion_id"]),
                session_factory=self._session_factory,
            )
            if fate is not None and fate.get("fate") == "approved":
                is_switch = True

        # ── is this a missed_fight candidate? ──
        # Clone-grabbed shadow entries and manual-mode holds are not
        # AI decisions the director can be scored on.
        grabbed = "grabbed" in rule
        is_missed_candidate = (
            outcome in ("rejected_challenger", "hold")
            and mode != "manual"
            and not grabbed
        )

        if not is_switch and not is_missed_candidate:
            summary["evaluated"] += 1
            return

        phase = str(decision.get("phase") or "unknown")
        fizzle_window = max(FIZZLE_MIN_WINDOW_S, min_fight_hold_s(phase))
        horizon = (
            max(GOOD_CALL_WINDOW_S, fizzle_window) if is_switch else MISSED_WINDOW_S
        )
        if now < ts + horizon:
            summary["deferred_window_open"] += 1
            return
        summary["evaluated"] += 1

        specs: List[Dict[str, Any]] = []
        if is_switch:
            specs.extend(self._switch_labels(decision, ts, fizzle_window, now))
        if is_missed_candidate:
            missed = self._missed_label(decision, ts)
            if missed is not None:
                specs.append(missed)

        if specs:
            summary["labels_written"] += await self._persist_labels(
                str(decision.get("id")), specs
            )

    # ── label rules ──────────────────────────────────────────────

    def _switch_labels(
        self, decision: Dict[str, Any], ts: float, fizzle_window: float, now: float
    ) -> List[Dict[str, Any]]:
        specs: List[Dict[str, Any]] = []
        chal_fight, chal_team = self._target_of(decision.get("challenger"))
        inc_block = decision.get("incumbent")
        inc_fight, inc_team = self._target_of(inc_block)
        if inc_fight is None:
            inc_fight = self._candidate_fight_for(decision, inc_team)

        target = {
            "team_id": (decision.get("challenger") or {}).get("team_id"),
            "fight_id": chal_fight,
        }

        # good_call — the landing produced real combat.
        combat, kills = self._window_events(
            ts, ts + GOOD_CALL_WINDOW_S, chal_fight, chal_team
        )
        if len(combat) >= GOOD_CALL_MIN_COMBAT or len(kills) >= GOOD_CALL_MIN_KILLS:
            specs.append(
                {
                    "label": "good_call",
                    "confidence": _count_confidence(len(combat), len(kills)),
                    "evidence": self._evidence(
                        rule="good_call",
                        window_s=GOOD_CALL_WINDOW_S,
                        target=target,
                        matched=combat,
                        kills=kills,
                        inputs={
                            "min_combat": GOOD_CALL_MIN_COMBAT,
                            "min_kills": GOOD_CALL_MIN_KILLS,
                        },
                    ),
                }
            )
        else:
            # fizzle — zero combat for the target across the whole
            # hold window AND the target never seen active afterwards
            # (within covered, retained time up to now).
            window_combat, _ = self._window_events(ts, ts + fizzle_window, chal_fight, chal_team)
            later_combat, _ = self._window_events(ts + fizzle_window, now, chal_fight, chal_team)
            if not window_combat and not later_combat:
                extended_silence = now >= ts + fizzle_window + 30.0
                specs.append(
                    {
                        "label": "fizzle",
                        "confidence": 0.95 if extended_silence else 0.85,
                        "evidence": self._evidence(
                            rule="fizzle",
                            window_s=fizzle_window,
                            target=target,
                            matched=[],
                            kills=[],
                            inputs={
                                "phase": decision.get("phase"),
                                "min_fight_hold_s": min_fight_hold_s(
                                    str(decision.get("phase") or "unknown")
                                ),
                                "observed_until_s": round(now - ts, 1),
                                "events_after_window": len(later_combat),
                            },
                        ),
                    }
                )

        # premature_exit — the fight we LEFT erupted right after.
        if inc_team is not None or inc_fight is not None:
            prev_combat, prev_kills = self._window_events(
                ts, ts + PREMATURE_WINDOW_S, inc_fight, inc_team
            )
            if (
                len(prev_combat) >= PREMATURE_MIN_COMBAT
                or len(prev_kills) >= PREMATURE_MIN_KILLS
            ):
                specs.append(
                    {
                        "label": "premature_exit",
                        "confidence": _count_confidence(
                            len(prev_combat), len(prev_kills)
                        ),
                        "evidence": self._evidence(
                            rule="premature_exit",
                            window_s=PREMATURE_WINDOW_S,
                            target={
                                "team_id": (inc_block or {}).get("team_id"),
                                "fight_id": inc_fight,
                                "role": "incumbent_left",
                            },
                            matched=prev_combat,
                            kills=prev_kills,
                            inputs={
                                "min_combat": PREMATURE_MIN_COMBAT,
                                "min_kills": PREMATURE_MIN_KILLS,
                            },
                        ),
                    }
                )
        return specs

    def _missed_label(
        self, decision: Dict[str, Any], ts: float
    ) -> Optional[Dict[str, Any]]:
        chal_fight, chal_team = self._target_of(decision.get("challenger"))
        if chal_team is None and chal_fight is None:
            return None
        inc_fight, inc_team = self._target_of(decision.get("incumbent"))
        if inc_fight is None:
            inc_fight = self._candidate_fight_for(decision, inc_team)

        combat, kills = self._window_events(ts, ts + MISSED_WINDOW_S, chal_fight, chal_team)
        if len(combat) < MISSED_MIN_COMBAT and len(kills) < MISSED_MIN_KILLS:
            return None
        inc_combat, _ = self._window_events(ts, ts + MISSED_WINDOW_S, inc_fight, inc_team)
        # Don't double-count events that correlate to BOTH sides
        # (the same fight) as incumbent activity.
        chal_ids = {e["id"] for e in combat}
        inc_only = [e for e in inc_combat if e["id"] not in chal_ids]
        if len(inc_only) > MISSED_MAX_INCUMBENT_COMBAT:
            return None
        return {
            "label": "missed_fight",
            "confidence": _count_confidence(len(combat), len(kills)),
            "evidence": self._evidence(
                rule="missed_fight",
                window_s=MISSED_WINDOW_S,
                target={
                    "team_id": (decision.get("challenger") or {}).get("team_id"),
                    "fight_id": chal_fight,
                },
                matched=combat,
                kills=kills,
                inputs={
                    "min_combat": MISSED_MIN_COMBAT,
                    "min_kills": MISSED_MIN_KILLS,
                    "incumbent_combat_events": len(inc_only),
                    "max_incumbent_combat": MISSED_MAX_INCUMBENT_COMBAT,
                    "decision_outcome": decision.get("outcome"),
                },
            ),
        }

    # ── introspection ────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        return {
            "running": self._started,
            "labeler_version": LABELER_VERSION,
            "subscriber_id": SUBSCRIBER_ID,
            "subscribed_topics": list(LIVE_DIRECTOR_EVENT_TYPES),
            "events_tracked": len(self._events),
            "coverage_start": self._coverage_start,
            "expired_before": self._expired_before,
            "stats": dict(self.stats),
            "last_pass": dict(self._last_pass),
            "last_error": self._last_error,
        }


def build_outcome_labeler(
    bus: Any,
    session_factory: Optional[Callable[[], Any]] = None,
) -> OutcomeLabeler:
    """Production wiring: construct + start the labeler on the shared
    bus. Failure-isolated by the caller (lifespan)."""
    labeler = OutcomeLabeler(bus, session_factory=session_factory)
    labeler.start()
    return labeler
