# backend/app/services/replay/buffer.py
"""Time-windowed ring buffer of broadcast events (Phase 10).

Subscribes to the REAL event bus (app.core.event_bus) for fight/kill/
commentary/standings event types and keeps a rolling window (default 5
minutes, configurable). Entries older than the window are pruned so
memory stays bounded — replay always covers "the last N minutes", not
the whole match.

Honesty note: this is an EVENT replay buffer. It stores structured
events ({event_id, ts, type, payload}), not video frames. "Playing a
clip" re-emits those events so the output page / PiP can re-render the
segment. There is no video DVR in this release.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, TYPE_CHECKING

import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.replay.buffer")

#: Bus event types captured into the replay window. These are the types
#: the match simulator and feed adapters actually publish (see
#: backend/app/main.py: kill_event/knock_event) plus forward-compatible
#: names any real feed adapter would use.
REPLAY_EVENT_TYPES = (
    "kill_event",
    "knock_event",
    "fight_event",
    "fight_detected",
    "commentary",
    "standings",
)


@dataclass
class BufferedEvent:
    """One event in the replay window."""

    event_id: str
    ts: float  # epoch seconds, derived from the bus event's timestamp_ms
    type: str
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "ts": self.ts,
            "type": self.type,
            "payload": self.payload,
        }


class ReplayBuffer:
    """Rolling-window buffer of broadcast events.

    Works standalone (``record()``) for unit tests and as an event-bus
    subscriber in production (``subscribe_to(bus)``). Threading model:
    asyncio callbacks only — never called from a raw thread.
    """

    def __init__(self, window_seconds: int = 300) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.window_seconds = window_seconds
        # deque(maxlen) gives a hard memory bound on top of time pruning.
        self._events: Deque[BufferedEvent] = deque(maxlen=50_000)
        self._subscribed_bus_id: Optional[str] = None
        self._subscriber_id = f"replay-buffer-{uuid.uuid4().hex[:8]}"

    # ── ingestion ──────────────────────────────────────────────

    def record(
        self,
        event_type: str,
        payload: Dict[str, Any],
        ts: Optional[float] = None,
        event_id: Optional[str] = None,
    ) -> BufferedEvent:
        """Store one event; ts defaults to now."""
        entry = BufferedEvent(
            event_id=event_id or str(uuid.uuid4()),
            ts=ts if ts is not None else time.time(),
            type=event_type,
            payload=payload or {},
        )
        self._events.append(entry)
        return entry

    async def _bus_callback(self, record: "EventRecord") -> None:
        """EventBus subscriber callback (receives an EventRecord)."""
        event = record.event
        payload = dict(event.data or {})
        payload.setdefault("_source", event.source)
        payload.setdefault("_event_id", event.event_id)
        self.record(
            event_type=event.event_type,
            payload=payload,
            ts=(event.timestamp_ms / 1000.0) if event.timestamp_ms else None,
            event_id=event.event_id,
        )

    def subscribe_to(self, bus: "EventBus") -> str:
        """Subscribe this buffer to a live EventBus instance.

        Subscribing is in-memory and does not require the bus to be
        connected yet; events only flow once the bus runs.
        """
        bus.subscribe(
            subscriber_id=self._subscriber_id,
            callback=self._bus_callback,
            event_types=list(REPLAY_EVENT_TYPES),
        )
        logger.info(
            "ReplayBuffer subscribed to event bus",
            subscriber_id=self._subscriber_id,
            event_types=list(REPLAY_EVENT_TYPES),
            window_seconds=self.window_seconds,
        )
        return self._subscriber_id

    def unsubscribe_from(self, bus: "EventBus") -> None:
        bus.unsubscribe(self._subscriber_id)

    # ── reads ──────────────────────────────────────────────────

    def prune(self, now: Optional[float] = None) -> int:
        """Drop events older than the window. Returns count removed."""
        cutoff = (now if now is not None else time.time()) - self.window_seconds
        removed = 0
        while self._events and self._events[0].ts < cutoff:
            self._events.popleft()
            removed += 1
        if removed:
            logger.debug("ReplayBuffer pruned", removed=removed, remaining=len(self._events))
        return removed

    def events_between(
        self, from_ts: float, to_ts: float, now: Optional[float] = None
    ) -> List[BufferedEvent]:
        """Events within [from_ts, to_ts], oldest first. Prunes first."""
        self.prune(now=now)
        return sorted(
            (e for e in self._events if from_ts <= e.ts <= to_ts),
            key=lambda e: e.ts,
        )

    def recent(self, limit: int = 100, now: Optional[float] = None) -> List[BufferedEvent]:
        """Newest ``limit`` events, oldest first. Prunes first."""
        self.prune(now=now)
        return list(self._events)[-limit:]

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._events)
