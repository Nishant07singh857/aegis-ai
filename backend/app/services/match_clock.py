# backend/app/services/match_clock.py
"""Match clock — feed-elapsed time for the live match (Phase 24, gap row 19).

Spectator-delay caveat (operational fact, labelled everywhere this value
is shown): BGMI spectator mode lags behind the real game. No published
delay figure exists — the earlier "~30-60 s" was a venue observation, not
a spec — so it must be measured on our own rig with a stopwatch and
published as a sync-offset. ``elapsed_s()`` is therefore **feed-elapsed** time — how long the
*observed feed* has been in the match — not true in-game time. The game
itself is ahead of this clock. Nothing in AEGIS may present this number
as exact game time.

States: ``not_started`` -> ``live`` -> ``ended``. The clock starts on a
bus ``match_start`` event and ends on ``match_end`` (the bus names the
feed adapter maps ``FeedEventType.MATCH_START`` / ``MATCH_END`` to — see
``feed_adapter/schemas.py::BUS_EVENT_TYPE_MAP``). It subscribes to the
event bus itself in :meth:`MatchClock.start`; until then (and in unit
tests) events can be fed directly via :meth:`handle_event`.

Elapsed time is computed server-side from a monotonic clock so wall-clock
adjustments never move the broadcast timer backwards. No value is ever
invented: before ``match_start`` the elapsed is ``None`` (the frontend
renders "—"), and after ``match_end`` it freezes at the final duration.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

import structlog

logger = structlog.get_logger("aegis.match_clock")

#: Bus event types that drive the clock.
MATCH_START_EVENT = "match_start"
MATCH_END_EVENT = "match_end"
CLOCK_EVENT_TYPES = [MATCH_START_EVENT, MATCH_END_EVENT]

SUBSCRIBER_ID = "match_clock"

STATE_NOT_STARTED = "not_started"
STATE_LIVE = "live"
STATE_ENDED = "ended"


class MatchClock:
    """In-memory match clock driven by bus match_start / match_end."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._bus: Any = None
        self._started = False
        self._state = STATE_NOT_STARTED
        self._match_id: Optional[str] = None
        self._started_at: Optional[float] = None
        self._ended_elapsed: Optional[float] = None

    # ── lifecycle / bus wiring ──────────────────────────────────

    def start(self, bus: Any) -> None:
        """Subscribe to match_start / match_end on the bus. Idempotent."""
        if self._started:
            return
        self._bus = bus
        bus.subscribe(
            subscriber_id=SUBSCRIBER_ID,
            callback=self.handle_record,
            event_types=list(CLOCK_EVENT_TYPES),
        )
        self._started = True
        logger.info("match_clock_subscribed", event_types=CLOCK_EVENT_TYPES)

    def stop(self) -> None:
        if not self._started:
            return
        try:
            self._bus.unsubscribe(SUBSCRIBER_ID)
        except Exception:  # pragma: no cover - defensive
            pass
        self._started = False

    @property
    def running(self) -> bool:
        return self._started

    # ── event intake ────────────────────────────────────────────

    async def handle_record(self, record: Any) -> None:
        """Bus callback. Never raises."""
        try:
            event = getattr(record, "event", None)
            event_type = getattr(event, "event_type", "") if event else ""
            data = getattr(event, "data", None) if event else None
            match_id = getattr(event, "match_id", None) if event else None
            if isinstance(data, dict):
                match_id = match_id or data.get("match_id")
            self.handle_event(event_type, match_id=match_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("match_clock_record_error", error=str(exc)[:200])

    def handle_event(
        self, event_type: str, match_id: Optional[str] = None
    ) -> None:
        """Direct event intake (also used by tests without a bus)."""
        if event_type == MATCH_START_EVENT:
            self.begin(match_id=match_id)
        elif event_type == MATCH_END_EVENT:
            self.finish()

    # ── state transitions ───────────────────────────────────────

    def begin(self, match_id: Optional[str] = None) -> None:
        """Start (or restart, for a new match) the clock."""
        self._state = STATE_LIVE
        self._match_id = match_id
        self._started_at = self._clock()
        self._ended_elapsed = None

    def finish(self) -> None:
        """Freeze the clock at the final elapsed duration."""
        if self._state != STATE_LIVE:
            # A match_end with no live clock is honest: stay not_started
            # unless a match was actually timed.
            if self._state == STATE_NOT_STARTED:
                return
            return
        self._ended_elapsed = self.elapsed_s()
        self._state = STATE_ENDED

    def reset(self) -> None:
        self._state = STATE_NOT_STARTED
        self._match_id = None
        self._started_at = None
        self._ended_elapsed = None

    # ── reads ───────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    @property
    def match_id(self) -> Optional[str]:
        return self._match_id

    def elapsed_s(self) -> Optional[float]:
        """Feed-elapsed seconds, or None before the match starts.

        After match_end this returns the frozen final duration.
        """
        if self._state == STATE_NOT_STARTED or self._started_at is None:
            return None
        if self._state == STATE_ENDED:
            return self._ended_elapsed
        return max(0.0, self._clock() - self._started_at)

    def snapshot(self) -> Dict[str, Any]:
        elapsed = self.elapsed_s()
        return {
            "state": self._state,
            "match_id": self._match_id,
            "elapsed_s": round(elapsed, 1) if elapsed is not None else None,
            # Labelled, never hidden: this is feed time (see docstring).
            "clock_basis": "feed_elapsed_spectator_delayed",
        }


#: Process-wide singleton (wired in app/main.py lifespan).
match_clock = MatchClock()


def get_match_clock() -> MatchClock:
    return match_clock
