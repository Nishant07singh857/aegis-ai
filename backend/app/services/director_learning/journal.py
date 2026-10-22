# backend/app/services/director_learning/journal.py
"""Decision journal + human-feedback capture (Phase 27, W1).

Sink registry (module level)
----------------------------
``LiveDirector._log_decision`` forwards every decision entry to
:func:`notify_decision`; ``DirectorService`` forwards every suggestion
fate (approve / reject / expire) to :func:`notify_fate`. Both default
to NO-OP: with no sink registered the calls do nothing and director
behaviour is byte-identical to pre-Phase-27. Sinks are SYNCHRONOUS
callables (``_log_decision`` and ``_prune_expired`` are sync code
paths); a raising sink is caught per-sink inside the notify helpers —
a broken journal can never break or delay a decision.

DecisionJournal (the writer)
----------------------------
Async, failure-isolated. Every observed decision gets a uuid
``decision_id``, is kept in a bounded in-memory ring (for shadow
reconciliation + W2 correlation) with a ``suggestion_id ->
decision_id`` index, and is persisted through an injectable session
factory (default ``app.db.base.AsyncSessionLocal``). A failed persist
drops the row and increments ``decisions_dropped`` — it never raises.

Modified fates (DEFINITION, from code reality)
----------------------------------------------
In SEMI_AUTO a human who disagrees with suggestion S cannot write
directly (``set_camera``/``set_scene`` raise outside MANUAL). What
they CAN actually do — all verified in ``director_service.py`` — is:
approve S (fate ``approved``), reject S (``rejected``), let S lapse
(``expired``, 60 s TTL), approve a DIFFERENT pending suggestion, or
flip to MANUAL and write a different target directly. So: when a
``rejected``/``expired`` fate lands for S, the journal opens a 30 s
watch. If a human output write lands inside the window whose target
(scene/active_camera) differs from BOTH what S proposed AND the
output that was on air when S was decided, a ``modified`` fate is
recorded as a SECOND row for S (the rejected/expired row is never
mutated; ``queries.fate_for_suggestion`` returns the latest row) with
``chosen_alternative`` = the fields actually observed on that write.
No differing write within 30 s -> the original fate stands alone.

Startup: :func:`build_director_learning` (lifespan, failure-isolated).
"""

from __future__ import annotations

import asyncio
import copy
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import structlog

from .shadow import ShadowReconciler, classify_write

logger = structlog.get_logger("aegis.director_learning.journal")

#: Bounded in-memory decision ring (shadow reconciliation + W2 joins).
RING_MAXLEN = 500
#: Bounded in-memory human-write log (ai_wanted window checks).
WRITE_LOG_MAXLEN = 200
#: Seconds a rejected/expired suggestion is watched for a "modified".
MODIFIED_WATCH_S = 30.0
#: Reconciler cadence.
RECONCILE_INTERVAL_S = 1.0

DecisionSink = Callable[[Dict[str, Any]], None]
FateSink = Callable[[Dict[str, Any]], None]

_decision_sinks: List[DecisionSink] = []
_fate_sinks: List[FateSink] = []


# ── sink registry ────────────────────────────────────────────────


def register_decision_sink(fn: DecisionSink) -> None:
    """Register a sync callable receiving every decision entry dict."""
    if fn not in _decision_sinks:
        _decision_sinks.append(fn)


def register_fate_sink(fn: FateSink) -> None:
    """Register a sync callable receiving every suggestion-fate dict.

    Fate dict: {suggestion_id, fate, decided_by, suggestion, ts} where
    fate is "approved" | "rejected" | "expired" and ``suggestion`` is
    the full suggestion dict (payload included) when known.
    """
    if fn not in _fate_sinks:
        _fate_sinks.append(fn)


def clear_sinks() -> None:
    """Remove all registered sinks (tests / shutdown)."""
    _decision_sinks.clear()
    _fate_sinks.clear()


def notify_decision(entry: Dict[str, Any]) -> None:
    """Forward one decision entry to all sinks. NEVER raises."""
    for fn in list(_decision_sinks):
        try:
            fn(entry)
        except Exception as exc:  # a broken sink must not break directing
            logger.warning("decision_sink_failed", error=str(exc)[:200])


def notify_fate(fate: Dict[str, Any]) -> None:
    """Forward one suggestion-fate dict to all sinks. NEVER raises."""
    for fn in list(_fate_sinks):
        try:
            fn(fate)
        except Exception as exc:
            logger.warning("fate_sink_failed", error=str(exc)[:200])


# ── helpers ──────────────────────────────────────────────────────


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _default_session_factory():
    from app.db.base import AsyncSessionLocal

    return AsyncSessionLocal


# ── the journal ──────────────────────────────────────────────────


class DecisionJournal:
    """Persisting decision/fate writer + shadow reconciliation driver.

    ``session_factory`` is injectable for tests (any zero-arg callable
    returning an async context manager yielding an AsyncSession, e.g.
    ``async_sessionmaker``). ``director_service`` is used ONLY for
    lock-free ``snapshot()`` reads (incumbent output at fate time).
    ``clock`` is an injectable epoch-seconds callable for tests.
    """

    def __init__(
        self,
        session_factory: Optional[Callable[[], Any]] = None,
        director_service: Optional[Any] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._session_factory = session_factory
        self._director_service = director_service
        self._clock = clock
        self.stats: Dict[str, int] = {
            "decisions_seen": 0,
            "decisions_persisted": 0,
            "decisions_dropped": 0,
            "fates_seen": 0,
            "fates_persisted": 0,
            "fates_dropped": 0,
            "divergences_persisted": 0,
            "divergences_dropped": 0,
            "modified_fates": 0,
            "writes_seen": 0,
            "human_writes": 0,
        }
        #: decision ring records: {decision_id, ts_epoch, entry}
        self._ring: deque = deque(maxlen=RING_MAXLEN)
        #: suggestion_id -> decision_id (bounded by ring lifetime use)
        self._suggestion_index: Dict[str, str] = {}
        #: observed output writes: {seq, ts_epoch, event}
        self._writes: deque = deque(maxlen=WRITE_LOG_MAXLEN)
        self._write_seq = 0
        #: open modified-fate watches: suggestion_id -> watch dict
        self._watches: Dict[str, Dict[str, Any]] = {}
        self._reconciler = ShadowReconciler(persist=self._persist_divergence)
        self._tasks: set = set()
        self._pending: List[Callable[[], Any]] = []
        self._task: Optional[asyncio.Task] = None
        self._running = False

    # ── sink entry points (sync, never raise) ────────────────────

    def on_decision(self, entry: Dict[str, Any]) -> Optional[str]:
        """Decision sink: index + ring + schedule persist.

        Returns the assigned decision_id (handy for tests/callers).
        """
        try:
            self.stats["decisions_seen"] += 1
            decision_id = str(uuid.uuid4())
            ts_dt = _parse_ts(entry.get("timestamp"))
            record = {
                "decision_id": decision_id,
                "ts_epoch": ts_dt.timestamp(),
                "entry": copy.deepcopy(entry),
            }
            self._ring.append(record)
            sid = entry.get("suggestion_id")
            if sid:
                self._suggestion_index[str(sid)] = decision_id
            self._schedule(lambda: self._persist_decision(decision_id, entry, ts_dt))
            return decision_id
        except Exception as exc:  # pragma: no cover - defensive
            self.stats["decisions_dropped"] += 1
            logger.warning("journal_on_decision_failed", error=str(exc)[:200])
            return None

    def on_fate(self, fate: Dict[str, Any]) -> None:
        """Fate sink: persist the fate; open/close modified watches."""
        try:
            self.stats["fates_seen"] += 1
            sid = str(fate.get("suggestion_id") or "")
            if not sid:
                return
            kind = str(fate.get("fate") or "")
            ts_dt = _parse_ts(fate.get("ts"))
            self._schedule(
                lambda: self._persist_fate(
                    suggestion_id=sid,
                    fate=kind,
                    decided_by=fate.get("decided_by"),
                    chosen_alternative=None,
                    ts_dt=ts_dt,
                )
            )
            if kind in ("rejected", "expired"):
                self._open_watch(sid, fate)
            elif kind == "approved":
                # Decided for real — no modified watch for this one.
                self._watches.pop(sid, None)
        except Exception as exc:  # pragma: no cover - defensive
            self.stats["fates_dropped"] += 1
            logger.warning("journal_on_fate_failed", error=str(exc)[:200])

    def on_write(self, event: Dict[str, Any]) -> None:
        """Output-write sink (DirectorService write path).

        Human writes drive modified-fate detection and the
        human_switched_ai_held divergence; ALL writes are logged for
        the ai_wanted window checks. System writes are classified and
        never treated as human action.
        """
        try:
            self.stats["writes_seen"] += 1
            self._write_seq += 1
            record = {
                "seq": self._write_seq,
                "ts_epoch": self._clock(),
                "event": copy.deepcopy(event),
            }
            self._writes.append(record)
            if classify_write(event) != "human":
                return
            self.stats["human_writes"] += 1
            self._check_modified_watches(record)
            divergence = self._reconciler.divergence_for_human_write(
                record, list(self._ring)
            )
            if divergence is not None:
                self._schedule(lambda: self._persist_divergence(divergence))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("journal_on_write_failed", error=str(exc)[:200])

    # ── modified-fate watches ────────────────────────────────────

    def _open_watch(self, sid: str, fate: Dict[str, Any]) -> None:
        suggestion = fate.get("suggestion") or {}
        payload = suggestion.get("payload") or {}
        proposed = {
            "scene": payload.get("scene"),
            "active_camera": payload.get("active_camera"),
            "team_id": payload.get("team_id"),
        }
        incumbent_output = None
        if self._director_service is not None:
            try:
                snap = self._director_service.snapshot()
                incumbent_output = {
                    "scene": snap.get("scene"),
                    "active_camera": snap.get("active_camera"),
                }
            except Exception:  # pragma: no cover - defensive
                incumbent_output = None
        self._watches[sid] = {
            "suggestion_id": sid,
            "proposed": proposed,
            "incumbent_output": incumbent_output,
            "opened_at": self._clock(),
            "base_fate": fate.get("fate"),
        }

    def _check_modified_watches(self, write: Dict[str, Any]) -> None:
        if not self._watches:
            return
        event = write["event"]
        now = float(write["ts_epoch"])
        actual = {
            "scene": event.get("scene"),
            "active_camera": event.get("active_camera"),
        }
        for sid, watch in list(self._watches.items()):
            if now - watch["opened_at"] > MODIFIED_WATCH_S:
                del self._watches[sid]
                continue
            proposed = watch["proposed"]
            differs_proposed = (
                proposed.get("scene") is not None
                and actual.get("scene") != proposed["scene"]
            ) or (
                proposed.get("active_camera") is not None
                and actual.get("active_camera") != proposed["active_camera"]
            )
            # A write that merely APPLIES the proposed target (via a
            # duplicate-payload suggestion) is agreement, not modified.
            if not differs_proposed:
                continue
            incumbent = watch.get("incumbent_output") or {}
            differs_incumbent = True
            if incumbent:
                differs_incumbent = (
                    actual.get("scene") != incumbent.get("scene")
                    or actual.get("active_camera") != incumbent.get("active_camera")
                )
            if not differs_incumbent:
                continue
            # Modified: the human put a DIFFERENT target on air.
            del self._watches[sid]
            self.stats["modified_fates"] += 1
            alternative: Dict[str, Any] = {"via": event.get("source")}
            if actual.get("scene") is not None:
                alternative["scene"] = actual["scene"]
            if actual.get("active_camera") is not None:
                alternative["active_camera"] = actual["active_camera"]
            if event.get("suggestion_id"):
                alternative["via_suggestion_id"] = event["suggestion_id"]
            decided_by = event.get("updated_by")
            ts_dt = datetime.fromtimestamp(now, tz=timezone.utc)
            self._schedule(
                lambda sid=sid, alt=alternative, by=decided_by, ts=ts_dt: (
                    self._persist_fate(
                        suggestion_id=sid,
                        fate="modified",
                        decided_by=by,
                        chosen_alternative=alt,
                        ts_dt=ts,
                    )
                )
            )

    # ── reconciliation ───────────────────────────────────────────

    async def reconcile_now(self) -> List[Dict[str, Any]]:
        """One reconciliation pass: expire watches + ai_wanted check.

        Returns the divergences persisted by this pass. Called by the
        periodic task; also directly callable (tests / W2 / ops).
        """
        now = self._clock()
        for sid, watch in list(self._watches.items()):
            if now - watch["opened_at"] > MODIFIED_WATCH_S:
                del self._watches[sid]
        divergences = self._reconciler.pending_ai_wanted(
            list(self._ring), list(self._writes), now
        )
        for divergence in divergences:
            await self._persist_divergence(divergence)
        return divergences

    # ── persistence (async, failure-isolated) ────────────────────

    def _factory(self):
        if self._session_factory is not None:
            return self._session_factory
        return _default_session_factory()

    async def _persist_decision(
        self, decision_id: str, entry: Dict[str, Any], ts_dt: datetime
    ) -> None:
        from .models import DirectorDecision

        try:
            async with self._factory()() as session:
                session.add(
                    DirectorDecision(
                        id=decision_id,
                        ts=ts_dt,
                        mode=entry.get("mode"),
                        phase=entry.get("phase"),
                        trigger=entry.get("trigger"),
                        outcome=entry.get("outcome"),
                        rule=entry.get("rule"),
                        reason=entry.get("reason"),
                        margin_required=entry.get("margin_required"),
                        margin_actual=entry.get("margin_actual"),
                        incumbent=copy.deepcopy(entry.get("incumbent")),
                        challenger=copy.deepcopy(entry.get("challenger")),
                        candidates=copy.deepcopy(entry.get("candidates")),
                        suggestion_id=(
                            str(entry["suggestion_id"])
                            if entry.get("suggestion_id")
                            else None
                        ),
                    )
                )
                await session.commit()
            self.stats["decisions_persisted"] += 1
        except Exception as exc:
            self.stats["decisions_dropped"] += 1
            logger.warning("journal_decision_drop", error=str(exc)[:200])

    async def _persist_fate(
        self,
        *,
        suggestion_id: str,
        fate: str,
        decided_by: Optional[str],
        chosen_alternative: Optional[Dict[str, Any]],
        ts_dt: datetime,
    ) -> None:
        from .models import DirectorSuggestionFate

        try:
            async with self._factory()() as session:
                session.add(
                    DirectorSuggestionFate(
                        id=str(uuid.uuid4()),
                        suggestion_id=suggestion_id,
                        decision_id=self._suggestion_index.get(suggestion_id),
                        fate=fate,
                        decided_by=decided_by,
                        chosen_alternative=chosen_alternative,
                        ts=ts_dt,
                    )
                )
                await session.commit()
            self.stats["fates_persisted"] += 1
        except Exception as exc:
            self.stats["fates_dropped"] += 1
            logger.warning("journal_fate_drop", error=str(exc)[:200])

    async def _persist_divergence(self, divergence: Dict[str, Any]) -> None:
        from .models import DirectorShadowDivergence

        try:
            async with self._factory()() as session:
                session.add(
                    DirectorShadowDivergence(
                        id=str(uuid.uuid4()),
                        ts=datetime.now(timezone.utc),
                        kind=divergence["kind"],
                        phase=divergence.get("phase"),
                        human_action=divergence.get("human_action"),
                        ai_candidate=divergence.get("ai_candidate"),
                        shadow_decision_id=divergence.get("shadow_decision_id"),
                        detail=divergence.get("detail"),
                    )
                )
                await session.commit()
            self.stats["divergences_persisted"] += 1
        except Exception as exc:
            self.stats["divergences_dropped"] += 1
            logger.warning("journal_divergence_drop", error=str(exc)[:200])

    # ── scheduling / lifecycle ───────────────────────────────────

    def _schedule(self, coro_factory: Callable[[], Any]) -> None:
        """Schedule an async persist; stash it if no loop is running."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._pending.append(coro_factory)
            return
        task = loop.create_task(coro_factory())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Flush stashed persists and await all in-flight ones (tests)."""
        pending, self._pending = self._pending, []
        for factory in pending:
            task = asyncio.ensure_future(factory())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _run(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(RECONCILE_INTERVAL_S)
                await self.reconcile_now()
                # Flush anything stashed while no loop was running.
                pending, self._pending = self._pending, []
                for factory in pending:
                    self._schedule(factory)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("journal_reconcile_failed", error=str(exc)[:200])

    def start(self) -> None:
        """Start the periodic reconciler (idempotent; needs a loop)."""
        if self._running:
            return
        self._running = True
        try:
            self._task = asyncio.get_running_loop().create_task(self._run())
        except RuntimeError:
            self._task = None  # reconcile_now() remains callable manually

    def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

    # ── introspection (W2/W3/W4 contracts) ───────────────────────

    def decision_id_for_suggestion(self, suggestion_id: str) -> Optional[str]:
        return self._suggestion_index.get(str(suggestion_id))

    def recent_decisions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """In-memory ring view: [{decision_id, ts_epoch, entry}]."""
        records = list(self._ring)
        return records[-max(1, int(limit)):]

    def status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "stats": dict(self.stats),
            "ring_size": len(self._ring),
            "suggestion_index_size": len(self._suggestion_index),
            "open_modified_watches": len(self._watches),
            "writes_observed": len(self._writes),
        }


# ── process-wide singleton + production builder ──────────────────

_journal: Optional[DecisionJournal] = None


def get_journal() -> Optional[DecisionJournal]:
    """The process-wide journal, or None when learning is not wired."""
    return _journal


def set_journal(journal: Optional[DecisionJournal]) -> None:
    global _journal
    _journal = journal


def build_director_learning(
    director_service: Any,
    session_factory: Optional[Callable[[], Any]] = None,
) -> DecisionJournal:
    """Production wiring: journal + sinks + write hook + reconciler.

    Registers the journal as THE decision sink and fate sink, hooks
    the DirectorService output-write sink, and starts the periodic
    reconciler. Failure-isolated by the caller (lifespan); every sink
    is itself failure-isolated, so a dead DB degrades to drop counters,
    never to broken directing.

    Phase 27 (W3 extension): also builds the knob provider + learning
    engine, registers the provider as the current provider (so
    LiveDirector clone mode and the shadow ladder can resolve it),
    and exposes them as ``journal.knob_provider`` /
    ``journal.learning_engine`` plus the engine module singleton for
    W4. The engine does NOT self-schedule: periodic analysis is
    available as ``engine.periodic_analysis_callable()`` for the
    coordinator's lifespan scheduler — no new busy loop here.
    """
    journal = DecisionJournal(
        session_factory=session_factory,
        director_service=director_service,
    )
    register_decision_sink(journal.on_decision)
    register_fate_sink(journal.on_fate)
    register = getattr(director_service, "register_output_write_sink", None)
    if register is not None:
        register(journal.on_write)
    else:  # module-level registry fallback
        try:
            from app.services.director_service import (
                register_output_write_sink as _reg,
            )

            _reg(journal.on_write)
        except Exception:  # pragma: no cover - defensive
            pass
    # ── W3: provider + engine ──
    try:
        from .engine import LearningEngine, set_engine
        from .knobs import KnobProvider, register_provider

        provider = KnobProvider(session_factory=session_factory)
        engine = LearningEngine(
            session_factory=session_factory, provider=provider
        )
        register_provider(provider)
        set_engine(engine)
        journal.knob_provider = provider  # type: ignore[attr-defined]
        journal.learning_engine = engine  # type: ignore[attr-defined]
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("learning_engine_build_failed", error=str(exc)[:200])
        journal.knob_provider = None  # type: ignore[attr-defined]
        journal.learning_engine = None  # type: ignore[attr-defined]
    journal.start()
    set_journal(journal)
    return journal
