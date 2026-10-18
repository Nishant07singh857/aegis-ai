# backend/app/services/rundown/rundown_service.py
"""
AEGIS AI — Producer run-of-show service.

An ordered rundown (show plan) for the broadcast: segments with planned
durations, a current-segment pointer, and start timestamps so the control
room can show elapsed/remaining time.

State::

    segments           [{id, title, planned_duration_s, notes, order}]
    current_index      int — -1 means "not started yet"
    started_at         ISO-8601 | None — when the rundown started
    segment_started_at ISO-8601 | None — when the current segment started
    updated_by         str | None
    updated_at         ISO-8601

Rules:

* ``add_segment`` appends at the end (or inserts at an explicit order);
  ids are uuid4 hex.
* ``reorder`` moves a segment to a new 0-based position and renumbers
  ``order`` fields; if the rundown is in progress and the move crosses the
  current segment, ``current_index`` follows the *segment* (not the slot).
* ``remove`` drops a segment; if the current segment is removed the
  pointer moves to the segment that slides into its place (or the last
  remaining one, or -1 when nothing remains).
* ``advance`` starts the rundown (index -1 -> 0) or steps to the next
  segment; raises :class:`RundownError` when the rundown is empty or
  already on its final segment.
* ``reset`` rewinds to "not started" but keeps the segments.

Persistence is in-memory (process-local). Durable DB persistence is a
documented follow-up — the service is the single source of truth and the
API router only speaks to it, so swapping in a store later is a one-file
change.

Broadcasting mirrors the director service:

* ``add_listener(...)`` — main.py registers ``ConnectionManager.broadcast``,
  so every change fans out to ``/ws/live`` as
  ``{"type": "rundown.state", "data": <state>}``.
* every change is also published to the event bus via
  ``event_bus.publish(event_type="rundown.state", ...)`` when the bus is
  connected.

Zero paid APIs, no secrets, no placeholders.
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

import structlog

logger = structlog.get_logger("aegis.services.rundown")

#: WebSocket message type.
WS_MESSAGE_TYPE = "rundown.state"

#: Signature of a broadcast listener: async callable taking the message dict.
BroadcastListener = Callable[[Dict[str, Any]], Awaitable[None]]

MAX_TITLE_LENGTH = 140
MAX_NOTES_LENGTH = 2000


class RundownError(ValueError):
    """Invalid rundown operation (empty title, bad duration, unknown id,
    advance past the end, ...)."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_segment_fields(
    title: str,
    planned_duration_s: int,
    notes: Optional[str],
) -> None:
    if not title or not title.strip():
        raise RundownError("Segment title must not be empty.")
    if len(title) > MAX_TITLE_LENGTH:
        raise RundownError(
            f"Segment title too long ({len(title)} > {MAX_TITLE_LENGTH})."
        )
    if planned_duration_s is None or int(planned_duration_s) <= 0:
        raise RundownError("planned_duration_s must be a positive integer.")
    if notes is not None and len(notes) > MAX_NOTES_LENGTH:
        raise RundownError(
            f"Segment notes too long ({len(notes)} > {MAX_NOTES_LENGTH})."
        )


class RundownService:
    """In-memory run-of-show with listener fan-out + event-bus emit."""

    def __init__(self) -> None:
        self._segments: List[Dict[str, Any]] = []
        self._current_index: int = -1
        self._started_at: Optional[str] = None
        self._segment_started_at: Optional[str] = None
        self._updated_by: Optional[str] = None
        self._updated_at: Optional[str] = None
        self._listeners: List[BroadcastListener] = []
        self._lock = asyncio.Lock()
        self._log = logger.bind(service="rundown_service")

    # ── listeners ──────────────────────────────────────────────

    def add_listener(self, listener: BroadcastListener) -> None:
        """Register an async callback invoked with every broadcast message."""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: BroadcastListener) -> None:
        """Unregister a previously added listener."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    # ── reads ──────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """Return a deep copy of the current rundown state."""
        return {
            "segments": copy.deepcopy(self._segments),
            "current_index": self._current_index,
            "started_at": self._started_at,
            "segment_started_at": self._segment_started_at,
            "updated_by": self._updated_by,
            "updated_at": self._updated_at,
        }

    def get_segment(self, segment_id: str) -> Optional[Dict[str, Any]]:
        for segment in self._segments:
            if segment["id"] == segment_id:
                return copy.deepcopy(segment)
        return None

    # ── writes ─────────────────────────────────────────────────

    async def add_segment(
        self,
        *,
        title: str,
        planned_duration_s: int,
        notes: Optional[str] = None,
        order: Optional[int] = None,
        updated_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Append a segment (or insert at ``order``). Returns the segment."""
        _validate_segment_fields(title, planned_duration_s, notes)
        segment = {
            "id": uuid.uuid4().hex,
            "title": title.strip(),
            "planned_duration_s": int(planned_duration_s),
            "notes": (notes or "").strip(),
            "order": 0,
        }
        async with self._lock:
            insert_at = (
                len(self._segments)
                if order is None
                else max(0, min(int(order), len(self._segments)))
            )
            self._segments.insert(insert_at, segment)
            self._renumber()
            self._touch(updated_by)
            out = copy.deepcopy(segment)

        self._log.info(
            "Rundown segment added",
            segment_id=out["id"],
            title=out["title"],
            updated_by=updated_by,
        )
        await self._emit()
        return out

    async def update_segment(
        self,
        segment_id: str,
        *,
        title: Optional[str] = None,
        planned_duration_s: Optional[int] = None,
        notes: Optional[str] = None,
        updated_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Patch title / duration / notes of one segment."""
        async with self._lock:
            segment = self._find_or_raise(segment_id)
            new_title = segment["title"] if title is None else title
            new_duration = (
                segment["planned_duration_s"]
                if planned_duration_s is None
                else planned_duration_s
            )
            new_notes = segment["notes"] if notes is None else notes
            _validate_segment_fields(new_title, new_duration, new_notes)
            segment["title"] = new_title.strip()
            segment["planned_duration_s"] = int(new_duration)
            segment["notes"] = (new_notes or "").strip()
            self._touch(updated_by)
            out = copy.deepcopy(segment)

        await self._emit()
        return out

    async def reorder(
        self,
        segment_id: str,
        new_order: int,
        *,
        updated_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Move a segment to a new 0-based position. The current-segment
        pointer follows the *segment* it was on, not the slot."""
        async with self._lock:
            from_idx = self._index_or_raise(segment_id)
            to_idx = max(0, min(int(new_order), len(self._segments) - 1))
            current_segment = (
                self._segments[self._current_index]
                if 0 <= self._current_index < len(self._segments)
                else None
            )
            segment = self._segments.pop(from_idx)
            self._segments.insert(to_idx, segment)
            self._renumber()
            if current_segment is not None:
                self._current_index = next(
                    i
                    for i, s in enumerate(self._segments)
                    if s["id"] == current_segment["id"]
                )
            self._touch(updated_by)

        self._log.info(
            "Rundown segment reordered",
            segment_id=segment_id,
            new_order=to_idx,
            updated_by=updated_by,
        )
        return await self._emit()

    async def remove(
        self,
        segment_id: str,
        *,
        updated_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Delete a segment. Returns the new full state."""
        async with self._lock:
            idx = self._index_or_raise(segment_id)
            del self._segments[idx]
            if self._current_index > idx:
                self._current_index -= 1
            elif self._current_index == idx:
                # The live segment was removed: point at whatever slid
                # into its place, else the last remaining, else rewind.
                if idx < len(self._segments):
                    self._current_index = idx
                elif self._segments:
                    self._current_index = len(self._segments) - 1
                else:
                    self._current_index = -1
                    self._started_at = None
            if self._current_index == -1:
                self._segment_started_at = None
            else:
                self._segment_started_at = _utcnow_iso()
            self._renumber()
            self._touch(updated_by)

        self._log.info(
            "Rundown segment removed",
            segment_id=segment_id,
            updated_by=updated_by,
        )
        return await self._emit()

    async def advance(
        self, *, updated_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """Start the rundown (first call) or move to the next segment.

        Raises :class:`RundownError` when there are no segments or the
        rundown is already on its final segment.
        """
        async with self._lock:
            if not self._segments:
                raise RundownError(
                    "Cannot advance: the rundown has no segments."
                )
            if self._current_index >= len(self._segments) - 1:
                raise RundownError(
                    "Cannot advance: already on the final segment. "
                    "Use reset() to start over."
                )
            now = _utcnow_iso()
            self._current_index += 1
            self._segment_started_at = now
            if self._current_index == 0 and self._started_at is None:
                self._started_at = now
            self._touch(updated_by)

        segment = self._segments[self._current_index]
        self._log.info(
            "Rundown advanced",
            current_index=self._current_index,
            title=segment["title"],
            updated_by=updated_by,
        )
        return await self._emit()

    async def reset(
        self, *, updated_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """Rewind to 'not started', keeping the segments."""
        async with self._lock:
            self._current_index = -1
            self._started_at = None
            self._segment_started_at = None
            self._touch(updated_by)

        self._log.info("Rundown reset", updated_by=updated_by)
        return await self._emit()

    # ── internals ──────────────────────────────────────────────

    def _find_or_raise(self, segment_id: str) -> Dict[str, Any]:
        for segment in self._segments:
            if segment["id"] == segment_id:
                return segment
        raise RundownError(f"No rundown segment with id {segment_id!r}.")

    def _index_or_raise(self, segment_id: str) -> int:
        for idx, segment in enumerate(self._segments):
            if segment["id"] == segment_id:
                return idx
        raise RundownError(f"No rundown segment with id {segment_id!r}.")

    def _renumber(self) -> None:
        for idx, segment in enumerate(self._segments):
            segment["order"] = idx

    def _touch(self, updated_by: Optional[str]) -> None:
        self._updated_by = updated_by
        self._updated_at = _utcnow_iso()

    async def _emit(self) -> Dict[str, Any]:
        state = self.snapshot()
        await self._broadcast({"type": WS_MESSAGE_TYPE, "data": state})
        await self._publish_event(state)
        return state

    async def _broadcast(self, message: Dict[str, Any]) -> None:
        for listener in list(self._listeners):
            await listener(message)

    async def _publish_event(self, state: Dict[str, Any]) -> None:
        """Emit the change on the event bus (persistent event side).

        Skipped when the bus isn't connected; the listener fan-out still
        reaches live WS clients.
        """
        try:
            from app.core.event_bus import event_bus

            if event_bus._running:
                await event_bus.publish(
                    event_type="rundown.state",
                    data=state,
                    source="rundown_service",
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("rundown event-bus publish failed", error=str(exc))


#: Process-wide singleton used by the API router and app/main.py.
rundown_service = RundownService()
