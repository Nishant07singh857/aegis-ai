# backend/tests/unit/test_day_reel.py
"""Unit tests for the Phase 20 whole-day highlight reel (G5).

Covers:
  * ordering: score descending, chronological tie-break
  * empty day: honestly empty reel, never synthesised moments
  * duration cap: fill stops before exceeding the cap; the top moment
    always survives; trimmed_count is accurate
  * segment cap: max_segments hard limit
  * adaptation: replay Highlight -> DayReelMoment field mapping
  * montage bridge: to_montage_clips carries the keys the
    MontageGenerator actually reads (clip_id, excitement_score)

No network, no Redis, no DB, no event bus — compile_day_reel is pure.
"""

from __future__ import annotations

import pytest

from app.agents.highlight.day_reel import (
    DEFAULT_MAX_DURATION_MS,
    DEFAULT_MAX_SEGMENTS,
    DayReelMoment,
    compile_day_reel,
    from_replay_highlights,
    to_montage_clips,
)
from app.services.replay.schemas import DayReel, Highlight


def _moment(
    moment_id: str,
    score: int,
    duration_ms: int = 5_000,
    start_ts: float = 1_000.0,
    match_id: str = "m1",
) -> DayReelMoment:
    return DayReelMoment(
        moment_id=moment_id,
        match_id=match_id,
        title=f"Moment {moment_id}",
        kind="multi_kill",
        score=score,
        start_ts=start_ts,
        end_ts=start_ts + duration_ms / 1000.0,
        duration_ms=duration_ms,
    )


def _highlight(
    moment_id: str,
    score: int,
    start: float = 1_000.0,
    end: float = 1_012.0,
) -> Highlight:
    return Highlight(
        moment_id=moment_id,
        kind="multi_kill",
        score=score,
        reason="Triple kill",
        player="Scout",
        team="Team A",
        window_start=start,
        window_end=end,
        window_start_iso="2026-10-19T00:00:00+00:00",
        window_end_iso="2026-10-19T00:00:12+00:00",
    )


# ── ordering ─────────────────────────────────────────────────────

def test_orders_by_score_descending() -> None:
    reel = compile_day_reel(
        [
            _moment("low", 40),
            _moment("high", 95),
            _moment("mid", 70),
        ]
    )
    assert [s.moment_id for s in reel.segments] == ["high", "mid", "low"]
    assert [s.rank for s in reel.segments] == [1, 2, 3]


def test_score_ties_break_chronologically() -> None:
    reel = compile_day_reel(
        [
            _moment("late", 80, start_ts=2_000.0),
            _moment("early", 80, start_ts=1_000.0),
            _moment("earliest", 80, start_ts=500.0),
        ]
    )
    assert [s.moment_id for s in reel.segments] == ["earliest", "early", "late"]


# ── empty day honesty ────────────────────────────────────────────

def test_empty_day_is_honestly_empty() -> None:
    reel = compile_day_reel([])
    assert reel.is_empty
    assert reel.segments == []
    assert reel.total_duration_ms == 0
    assert reel.moment_count == 0
    assert reel.trimmed_count == 0
    # The pydantic schema carries the same honest flag for the API.
    schema = DayReel(
        day=reel.day,
        segments=[],
        total_duration_ms=0,
        moment_count=0,
        max_duration_ms=reel.max_duration_ms,
        max_segments=reel.max_segments,
        is_empty=reel.is_empty,
    )
    assert schema.is_empty


# ── duration cap ─────────────────────────────────────────────────

def test_duration_cap_drops_lowest_ranked_first() -> None:
    # 5 moments of 60s each, cap at 150s -> top two fit (120s), the
    # third would reach 180s > 150s so it and below are trimmed.
    moments = [
        _moment(f"m{i}", score=90 - i * 5, duration_ms=60_000)
        for i in range(5)
    ]
    reel = compile_day_reel(moments, max_duration_ms=150_000)
    assert [s.moment_id for s in reel.segments] == ["m0", "m1"]
    assert reel.total_duration_ms == 120_000
    assert reel.trimmed_count == 3
    assert reel.moment_count == 5


def test_top_moment_survives_oversized_cap() -> None:
    # A single 10-minute moment with a 5-minute cap: dropping it would
    # mean an empty reel despite real highlights — it stays.
    reel = compile_day_reel(
        [_moment("epic", 100, duration_ms=600_000)],
        max_duration_ms=300_000,
    )
    assert not reel.is_empty
    assert [s.moment_id for s in reel.segments] == ["epic"]
    assert reel.total_duration_ms == 600_000
    assert reel.trimmed_count == 0


def test_default_caps_match_broadcast_package() -> None:
    reel = compile_day_reel([_moment("x", 50)], day="2026-10-19")
    assert reel.max_duration_ms == DEFAULT_MAX_DURATION_MS
    assert reel.max_segments == DEFAULT_MAX_SEGMENTS
    assert reel.day == "2026-10-19"


# ── segment cap ──────────────────────────────────────────────────

def test_segment_cap_is_hard_limit() -> None:
    moments = [_moment(f"m{i}", score=100 - i) for i in range(40)]
    reel = compile_day_reel(moments, max_segments=10)
    assert len(reel.segments) == 10
    assert reel.trimmed_count == 30
    # Top 10 by score survive (all 5s durations fit the default cap).
    assert reel.segments[0].moment_id == "m0"
    assert reel.segments[-1].moment_id == "m9"


# ── match aggregation ────────────────────────────────────────────

def test_match_ids_collected_sorted() -> None:
    moments = [
        _moment("a", 90, match_id="m3"),
        _moment("b", 95, match_id="m1"),
        _moment("c", 85, match_id="m1"),
    ]
    reel = compile_day_reel(moments)
    assert reel.match_ids == ["m1", "m3"]


# ── replay highlight adaptation ──────────────────────────────────

def test_from_replay_highlights_maps_fields() -> None:
    moments = from_replay_highlights(
        [_highlight("h1", 95, start=1_000.0, end=1_012.0)],
        match_id="match-7",
        match_number=3,
    )
    assert len(moments) == 1
    m = moments[0]
    assert m.moment_id == "h1"
    assert m.score == 95
    assert m.match_id == "match-7"
    assert m.match_number == 3
    assert m.title == "Triple kill"
    assert m.duration_ms == 12_000  # real event window, not invented
    assert m.player == "Scout"
    assert m.team == "Team A"


def test_from_replay_highlights_empty_stays_empty() -> None:
    reel = compile_day_reel(from_replay_highlights([]))
    assert reel.is_empty


def test_garbage_scores_never_crash() -> None:
    m = DayReelMoment(moment_id="g", score="not-a-number")  # type: ignore[arg-type]
    assert m.score == 0
    m2 = DayReelMoment(moment_id="g2", score=999)
    assert m2.score == 100
    reel = compile_day_reel([m, m2])
    assert reel.segments[0].moment_id == "g2"


# ── montage bridge ───────────────────────────────────────────────

def test_to_montage_clips_carries_generator_keys() -> None:
    reel = compile_day_reel([_moment("h1", 95), _moment("h2", 80)])
    clips = to_montage_clips(reel)
    assert len(clips) == 2
    assert clips[0]["clip_id"] == "h1"
    assert clips[0]["excitement_score"] == 95
    assert clips[1]["clip_id"] == "h2"
    assert clips[1]["excitement_score"] == 80


def test_to_montage_clips_empty_reel_is_empty() -> None:
    assert to_montage_clips(compile_day_reel([])) == []
