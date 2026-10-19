# backend/app/services/replay/service.py
"""Replay service: marks, clip retrieval, and event-replay playback.

Playback model (honest): this system has NO video DVR. A "clip" is a
time window of structured broadcast events. ``play_clip`` re-emits
those events over the WS fan-out as ``{"type": "replay.play", ...}`` so
the output page / PiP can re-render the segment. Consumers must treat
replay.play as event data, never as video.

WS wiring (coordinator adds to app/main.py lifespan — see docs):
  replay_service.add_listener(manager.broadcast)
  replay_service.buffer.subscribe_to(event_bus)   # after event_bus.connect()
"""

from __future__ import annotations

import copy
import time
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional

import structlog

from .buffer import ReplayBuffer
from .highlights import HighlightDetector
from .schemas import Clip, DayReel, DayReelSegment, Highlight, Mark
from app.agents.highlight.day_reel import (
    DEFAULT_MAX_DURATION_MS,
    DEFAULT_MAX_SEGMENTS,
    compile_day_reel,
    day_utc_label,
    from_replay_highlights,
)

logger = structlog.get_logger("aegis.replay.service")

#: WS message type consumed by the output page / PiP.
WS_REPLAY_PLAY_TYPE = "replay.play"

#: Rolling window around a saved mark when played (event replay needs
#: context before the mark and the aftermath after it).
MARK_PRE_ROLL_SECONDS = 15.0
MARK_POST_ROLL_SECONDS = 45.0

#: Bound on saved marks so the in-memory list can't grow forever.
MAX_MARKS = 100

BroadcastListener = Callable[[Dict], Awaitable[None]]


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class ReplayService:
    """Process-wide replay coordinator."""

    def __init__(self, window_seconds: int = 300) -> None:
        self.buffer = ReplayBuffer(window_seconds=window_seconds)
        self.detector = HighlightDetector()
        self._marks: Dict[str, Mark] = {}
        self._listeners: List[BroadcastListener] = []

    # ── listeners (same pattern as director_service) ───────────

    def add_listener(self, listener: BroadcastListener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: BroadcastListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    async def _broadcast(self, message: Dict) -> None:
        for listener in list(self._listeners):
            try:
                await listener(message)
            except Exception as e:  # fan-out must not die on one client
                logger.warning("Replay broadcast listener failed", error=str(e))

    # ── marks ──────────────────────────────────────────────────

    def capture_mark(self, label: Optional[str] = None) -> Mark:
        """Save a bookmark at "now" with the current clip context size."""
        now = time.time()
        self.buffer.prune(now=now)
        window_events = self.buffer.events_between(
            now - MARK_PRE_ROLL_SECONDS, now + MARK_POST_ROLL_SECONDS, now=now
        )
        mark = Mark(
            id=str(uuid.uuid4()),
            label=label or f"Mark {len(self._marks) + 1}",
            ts=now,
            ts_iso=_iso(now),
            event_count=len(window_events),
        )
        self._marks[mark.id] = mark
        # Bounded: drop the oldest mark when over the cap.
        if len(self._marks) > MAX_MARKS:
            oldest = min(self._marks.values(), key=lambda m: m.ts)
            del self._marks[oldest.id]
        logger.info("Replay mark captured", mark_id=mark.id, label=mark.label)
        return mark

    def list_marks(self) -> List[Mark]:
        return sorted(self._marks.values(), key=lambda m: m.ts, reverse=True)

    def delete_mark(self, mark_id: str) -> bool:
        return self._marks.pop(mark_id, None) is not None

    # ── clips ──────────────────────────────────────────────────

    def get_clip(self, from_ts: float, to_ts: float) -> Clip:
        """Return buffered events in [from_ts, to_ts], oldest first."""
        if from_ts > to_ts:
            raise ValueError("from_ts must be <= to_ts")
        now = time.time()
        events = self.buffer.events_between(from_ts, to_ts, now=now)
        return Clip(
            from_ts=from_ts,
            to_ts=to_ts,
            from_iso=_iso(from_ts),
            to_iso=_iso(to_ts),
            event_count=len(events),
            events=[e.to_dict() for e in events],
        )

    async def play_clip(self, from_ts: float, to_ts: float) -> Clip:
        """Re-emit a clip's events as {"type": "replay.play"} over WS.

        This is EVENT replay: the events are re-broadcast so the output
        page/PiP can re-render the segment. No video is involved.
        """
        clip = self.get_clip(from_ts, to_ts)
        await self._broadcast(
            {
                "type": WS_REPLAY_PLAY_TYPE,
                "data": {
                    "clip": clip.model_dump(),
                    "played_at": _iso(time.time()),
                },
            }
        )
        logger.info(
            "Replay clip played", from_ts=from_ts, to_ts=to_ts,
            events=clip.event_count,
        )
        return clip

    async def play_mark(self, mark_id: str) -> Clip:
        """Play the default window around a saved mark."""
        mark = self._marks.get(mark_id)
        if mark is None:
            raise KeyError(mark_id)
        return await self.play_clip(
            mark.ts - MARK_PRE_ROLL_SECONDS, mark.ts + MARK_POST_ROLL_SECONDS
        )

    # ── highlights ─────────────────────────────────────────────

    def get_highlights(self, limit: int = 10) -> List[Highlight]:
        """Top-N scored moments from the current buffer window.

        Honestly empty when the simulator / feed has produced no
        qualifying kill events — never synthesised.
        """
        self.buffer.prune()
        events = self.buffer.recent(limit=50_000)
        return self.detector.build_reel(events, limit=limit)

    def get_day_reel(self, limit: int = 50) -> DayReel:
        """Whole-day highlight reel from recorded highlights.

        Compiles the top scored moments recorded in this process's
        replay buffer (``GET /replay/highlights`` source), ordered by
        excitement score with the day-reel duration/segment caps.

        Honest scope: the replay buffer is the current session's
        recorded events — highlights only exist for matches played
        while this process was running. There is intentionally no
        match-day persistence yet; an empty buffer returns an empty
        reel (``is_empty``), never synthesised moments.
        """
        self.buffer.prune()
        events = self.buffer.recent(limit=50_000)
        highlights = self.detector.build_reel(events, limit=limit)
        moments = from_replay_highlights(highlights)
        reel = compile_day_reel(moments, day=day_utc_label())
        return DayReel(
            day=reel.day,
            segments=[
                DayReelSegment(
                    rank=s.rank,
                    moment_id=s.moment_id,
                    match_id=s.match_id,
                    match_number=s.match_number,
                    title=s.title,
                    kind=s.kind,
                    score=s.score,
                    duration_ms=s.duration_ms,
                    window_start_iso=_iso(s.start_ts) if s.start_ts else None,
                    window_end_iso=_iso(s.end_ts) if s.end_ts else None,
                    player=s.player,
                    team=s.team,
                )
                for s in reel.segments
            ],
            total_duration_ms=reel.total_duration_ms,
            moment_count=reel.moment_count,
            trimmed_count=reel.trimmed_count,
            match_ids=reel.match_ids,
            max_duration_ms=reel.max_duration_ms,
            max_segments=reel.max_segments,
            is_empty=reel.is_empty,
        )

    def snapshot(self) -> Dict:
        """Lightweight status for /feed-like introspection."""
        self.buffer.prune()
        return {
            "window_seconds": self.buffer.window_seconds,
            "buffered_events": len(self.buffer),
            "marks": len(self._marks),
            "listeners": len(self._listeners),
            "is_video": False,
        }


#: Process-wide singleton used by the API router and app/main.py.
replay_service = ReplayService()
