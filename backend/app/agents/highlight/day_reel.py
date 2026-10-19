# backend/app/agents/highlight/day_reel.py
"""AEGIS AI — Whole-day highlight reel compilation (Phase 20 / G5).

Pure, dependency-free compilation logic: takes highlight moments
recorded across all matches of a day, orders them by excitement score,
caps the reel duration sensibly, and returns an ordered DayReel.

This EXTENDS the Phase-10 reel builder
(``backend/app/agents/highlight_agents/montage_generator.py``):
``compile_day_reel()`` produces the ordered, duration-capped segment
list; the segments convert to the montage generator's clip dicts via
``to_montage_clips()`` so ``MontageGenerator.generate_montage()`` can
render them with music, transitions and beat sync.

Honesty rules (same as the replay detector):
  * No highlights recorded -> ``DayReel`` with ``segments == []`` and
    ``is_empty == True``. Callers render an honest empty state
    ("No highlights recorded yet"). Moments are NEVER synthesised.
  * A reel with real highlights always carries at least one segment —
    a non-empty reel that shows nothing would be a lie.
  * Scores and durations come from the recorded moments, never invented.

Capping policy (documented choice):
  * ``max_duration_ms`` (default 5 min) — a day recap is a broadcast
    package; it must stay watchable. Segments are taken in score order
    and the fill stops before the total would exceed the cap. The
    single most exciting moment always makes the reel even if it alone
    exceeds the cap (it would be absurd to show an empty reel when a
    squad wipe exists).
  * ``max_segments`` (default 24) — hard cap on segment count so the
    reel never becomes a slideshow of everything that happened.
  * ``MIN_SEGMENT_MS`` (3 s) — a moment shorter than this is held at
    its real duration; segments are never stretched, only the count is
    capped.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

# A day recap is a broadcast package: 5 minutes keeps it watchable,
# 24 segments keeps it a reel rather than a slideshow.
DEFAULT_MAX_DURATION_MS = 5 * 60 * 1000
DEFAULT_MAX_SEGMENTS = 24
MIN_SEGMENT_MS = 3_000


def _clamp_score(score: Any) -> int:
    """Scores are 0-100 ints; garbage in -> 0, never an exception."""
    try:
        return max(0, min(100, int(score)))
    except (TypeError, ValueError):
        return 0


@dataclass
class DayReelMoment:
    """One recorded highlight moment that can go into the day reel.

    ``score`` is the excitement score (0-100) from the detector that
    recorded it. ``start_ts`` / ``end_ts`` are epoch seconds of the
    moment's real event window; ``duration_ms`` is the allocated reel
    time for this moment.
    """

    moment_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    match_id: Optional[str] = None
    match_number: Optional[int] = None
    title: str = ""
    kind: str = ""  # e.g. "multi_kill" | "squad_wipe" | "clutch"
    score: int = 0
    start_ts: Optional[float] = None
    end_ts: Optional[float] = None
    duration_ms: int = MIN_SEGMENT_MS
    player: Optional[str] = None
    team: Optional[str] = None

    def __post_init__(self) -> None:
        self.score = _clamp_score(self.score)
        if not isinstance(self.duration_ms, int) or self.duration_ms <= 0:
            self.duration_ms = MIN_SEGMENT_MS
        if not self.moment_id:
            self.moment_id = str(uuid.uuid4())


@dataclass
class DayReelSegment:
    """One ordered slot in the compiled day reel."""

    rank: int  # 1-based position in the reel
    moment_id: str
    match_id: Optional[str]
    match_number: Optional[int]
    title: str
    kind: str
    score: int
    duration_ms: int
    start_ts: Optional[float]
    end_ts: Optional[float]
    player: Optional[str]
    team: Optional[str]


@dataclass
class DayReel:
    """The compiled whole-day reel."""

    day: str  # ISO date, e.g. "2026-10-19"
    segments: List[DayReelSegment] = field(default_factory=list)
    total_duration_ms: int = 0
    moment_count: int = 0  # moments considered before capping
    trimmed_count: int = 0  # moments dropped by the caps
    match_ids: List[str] = field(default_factory=list)
    max_duration_ms: int = DEFAULT_MAX_DURATION_MS
    max_segments: int = DEFAULT_MAX_SEGMENTS

    @property
    def is_empty(self) -> bool:
        return not self.segments


def _moment_key(m: DayReelMoment) -> tuple:
    """Sort key: excitement score descending, then chronological.

    ``start_ts`` tie-break keeps the reel narratable when scores tie:
    earlier moments play first.
    """
    return (-m.score, m.start_ts if m.start_ts is not None else float("inf"))


def compile_day_reel(
    moments: List[DayReelMoment],
    *,
    day: Optional[str] = None,
    max_duration_ms: int = DEFAULT_MAX_DURATION_MS,
    max_segments: int = DEFAULT_MAX_SEGMENTS,
) -> DayReel:
    """Compile the whole-day reel from recorded moments.

    1. Orders moments by excitement score (ties -> chronological).
    2. Hard-caps the segment count at ``max_segments``.
    3. Greedy-fills duration up to ``max_duration_ms``; the top moment
       always survives even if it alone exceeds the cap.

    Empty input -> honestly empty ``DayReel`` (``is_empty`` True).
    """
    day_label = day or date.today().isoformat()
    ordered = sorted(moments, key=_moment_key)[: max(0, max_segments)]

    segments: List[DayReelSegment] = []
    total_ms = 0
    trimmed = max(0, len(moments) - len(ordered))

    for i, m in enumerate(ordered):
        fits = total_ms + m.duration_ms <= max_duration_ms
        if not fits and i > 0:
            # This moment doesn't fit: it and everything after it are
            # trimmed (they are lower-ranked by construction).
            trimmed += len(ordered) - i
            break
        segments.append(
            DayReelSegment(
                rank=i + 1,
                moment_id=m.moment_id,
                match_id=m.match_id,
                match_number=m.match_number,
                title=m.title,
                kind=m.kind,
                score=m.score,
                duration_ms=m.duration_ms,
                start_ts=m.start_ts,
                end_ts=m.end_ts,
                player=m.player,
                team=m.team,
            )
        )
        total_ms += m.duration_ms

    match_ids = sorted({m.match_id for m in ordered if m.match_id})

    return DayReel(
        day=day_label,
        segments=segments,
        total_duration_ms=total_ms,
        moment_count=len(moments),
        trimmed_count=trimmed,
        match_ids=match_ids,
        max_duration_ms=max_duration_ms,
        max_segments=max_segments,
    )


def from_replay_highlights(
    highlights: List[Any],
    *,
    match_id: Optional[str] = None,
    match_number: Optional[int] = None,
) -> List[DayReelMoment]:
    """Adapt scored replay ``Highlight`` schemas to day-reel moments.

    The replay highlight's real event window (``window_start`` /
    ``window_end``) becomes the segment duration — the reel plays the
    moment as it happened, not a synthesised clip length.
    """
    moments: List[DayReelMoment] = []
    for h in highlights:
        start = getattr(h, "window_start", None)
        end = getattr(h, "window_end", None)
        duration_ms = MIN_SEGMENT_MS
        if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
            duration_ms = max(MIN_SEGMENT_MS, int(round((end - start) * 1000)))
        moments.append(
            DayReelMoment(
                moment_id=getattr(h, "moment_id", "") or str(uuid.uuid4()),
                match_id=match_id,
                match_number=match_number,
                title=getattr(h, "reason", "") or "",
                kind=getattr(h, "kind", "") or "",
                score=getattr(h, "score", 0),
                start_ts=start if isinstance(start, (int, float)) else None,
                end_ts=end if isinstance(end, (int, float)) else None,
                duration_ms=duration_ms,
                player=getattr(h, "player", None),
                team=getattr(h, "team", None),
            )
        )
    return moments


def day_utc_label() -> str:
    """Today's date label in UTC (broadcast days are UTC-anchored)."""
    return datetime.now(timezone.utc).date().isoformat()


def to_montage_clips(reel: DayReel) -> List[Dict[str, Any]]:
    """Convert compiled day-reel segments to montage clip dicts.

    Shape accepted by ``MontageGenerator.generate_montage(clips=...)``:
    ``clip_id`` and ``excitement_score`` are the keys the generator
    actually reads. Empty reel -> empty clip list (never synthesised).
    """
    return [
        {
            "clip_id": s.moment_id,
            "excitement_score": s.score,
            "timestamp_ms": int((s.start_ts or 0) * 1000),
            "duration_ms": float(s.duration_ms),
            "title": s.title,
            "kind": s.kind,
            "match_id": s.match_id,
            "match_number": s.match_number,
            "player": s.player,
            "team": s.team,
        }
        for s in reel.segments
    ]
