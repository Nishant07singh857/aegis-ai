# backend/app/services/observer_pilot/pov_journal.py
"""POV pilot journal — durable, failure-isolated writer (Phase 29, W4).

Pattern copied from ``director_learning.journal.DecisionJournal``:
synchronous ``record()`` entry points that never raise, an injectable
session factory (default ``app.db.base.AsyncSessionLocal``), persists
scheduled onto the running loop (stashed when there is none, flushed
by :meth:`POVJournal.drain`), a bounded in-memory ring for cheap
recent reads, and drop counters instead of exceptions — a dead DB
degrades the journal to counters, never to a broken pilot.

Every POV event is recorded: executed taps (with the TapResult),
suggestions and their fates (approved / rejected / expired), shadow
decisions (manual / clone-grabbed), manual overrides, and failures.
``recent()`` reads the DB (the durable truth); ``recent_sync()`` is
the ring view used by the status surface when no loop/DB is handy.
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

logger = structlog.get_logger("aegis.observer_pilot.journal")

RING_MAXLEN = 500


def _default_session_factory():
    from app.db.base import AsyncSessionLocal

    return AsyncSessionLocal


def _ts_dt(epoch: Optional[float]) -> datetime:
    if epoch is not None:
        try:
            return datetime.fromtimestamp(float(epoch), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            pass
    return datetime.now(timezone.utc)


class POVJournal:
    """Persisting POV-event writer. ``record()`` never raises."""

    def __init__(
        self,
        session_factory: Optional[Callable[[], Any]] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self.stats: Dict[str, int] = {
            "events_seen": 0,
            "events_persisted": 0,
            "events_dropped": 0,
        }
        self._ring: deque = deque(maxlen=RING_MAXLEN)
        self._tasks: set = set()
        self._pending: List[Callable[[], Any]] = []

    # ── write path (sync, never raises) ──────────────────────────

    def record(self, entry: Dict[str, Any]) -> Optional[str]:
        """Index + ring + schedule one POV event's persist.

        ``entry`` keys: slot_number, source_id, team_id, from_index,
        to_index, rule, reason, evidence, mode, fate, suggestion_id,
        verification, taps_sent, decided_by, ts (epoch float).
        Returns the assigned row id (handy for tests/callers).
        """
        try:
            self.stats["events_seen"] += 1
            row_id = str(uuid.uuid4())
            record = {"id": row_id, **copy.deepcopy(entry)}
            self._ring.append(record)
            self._schedule(lambda: self._persist(row_id, entry))
            return row_id
        except Exception as exc:  # pragma: no cover - defensive
            self.stats["events_dropped"] += 1
            logger.warning("pov_journal_record_failed", error=str(exc)[:200])
            return None

    async def _persist(self, row_id: str, entry: Dict[str, Any]) -> None:
        from .models import POVPilotDecision

        try:
            factory = self._session_factory or _default_session_factory()
            async with factory() as session:
                session.add(
                    POVPilotDecision(
                        id=row_id,
                        ts=_ts_dt(entry.get("ts")),
                        slot_number=int(entry.get("slot_number") or 0),
                        source_id=entry.get("source_id"),
                        team_id=entry.get("team_id"),
                        from_index=entry.get("from_index"),
                        to_index=entry.get("to_index"),
                        rule=entry.get("rule"),
                        reason=entry.get("reason"),
                        evidence=copy.deepcopy(entry.get("evidence")),
                        mode=entry.get("mode"),
                        fate=str(entry.get("fate") or "failed"),
                        suggestion_id=(
                            str(entry["suggestion_id"])
                            if entry.get("suggestion_id")
                            else None
                        ),
                        verification=entry.get("verification"),
                        taps_sent=entry.get("taps_sent"),
                        decided_by=entry.get("decided_by"),
                        decided_at_epoch=(
                            float(entry["ts"])
                            if isinstance(entry.get("ts"), (int, float))
                            else None
                        ),
                    )
                )
                await session.commit()
            self.stats["events_persisted"] += 1
        except Exception as exc:
            self.stats["events_dropped"] += 1
            logger.warning("pov_journal_drop", error=str(exc)[:200])

    # ── read path ────────────────────────────────────────────────

    async def recent(
        self, limit: int = 50, slot_number: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Most recent journal rows from the DB, newest first.

        Falls back to the in-memory ring when the DB read fails —
        the caller gets real recorded events either way, never a
        fabricated row; an empty journal returns []."""
        from sqlalchemy import select

        from .models import POVPilotDecision

        try:
            factory = self._session_factory or _default_session_factory()
            async with factory() as session:
                query = select(POVPilotDecision).order_by(
                    POVPilotDecision.ts.desc(),
                    POVPilotDecision.created_at.desc(),
                )
                if slot_number is not None:
                    query = query.where(
                        POVPilotDecision.slot_number == int(slot_number)
                    )
                query = query.limit(max(1, int(limit)))
                rows = (await session.execute(query)).scalars().all()
                out = []
                for row in rows:
                    data = row.to_dict()
                    data.pop("is_deleted", None)
                    data.pop("deleted_at", None)
                    out.append(data)
                return out
        except Exception as exc:
            logger.warning("pov_journal_recent_failed", error=str(exc)[:200])
            return self.recent_sync(limit=limit, slot_number=slot_number)

    def recent_sync(
        self, limit: int = 50, slot_number: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """In-memory ring view, newest first: [{id, ts, fate, ...}]."""
        records = list(self._ring)
        if slot_number is not None:
            records = [
                r for r in records if r.get("slot_number") == int(slot_number)
            ]
        return records[-max(1, int(limit)):][::-1]

    # ── scheduling / lifecycle ───────────────────────────────────

    def _schedule(self, coro_factory: Callable[[], Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._pending.append(coro_factory)
            return
        task = loop.create_task(coro_factory())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Flush stashed persists and await in-flight ones (tests)."""
        pending, self._pending = self._pending, []
        for factory in pending:
            task = asyncio.ensure_future(factory())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def status(self) -> Dict[str, Any]:
        return {"stats": dict(self.stats), "ring_size": len(self._ring)}


# ── process-wide singleton ───────────────────────────────────────

_journal: Optional[POVJournal] = None


def get_pov_journal() -> Optional[POVJournal]:
    return _journal


def set_pov_journal(journal: Optional[POVJournal]) -> None:
    global _journal
    _journal = journal


__all__ = ["POVJournal", "get_pov_journal", "set_pov_journal"]
