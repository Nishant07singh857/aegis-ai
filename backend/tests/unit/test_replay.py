# backend/tests/unit/test_replay.py
"""Unit tests for the Phase 10 replay system + highlight detection.

Covers:
  * buffer window pruning: events older than the window are dropped
  * multi-kill scoring: >=3 kills by the same player within 10s
  * squad-wipe scoring: >=4 kills against one team within 30s
  * clutch scoring: solo >=2 kills in 10s, teammates scoring nothing
  * clip retrieval: get_clip returns the events in the window, oldest first
  * empty-reel honesty: no kill events -> build_reel returns [], never
    synthesised moments
  * marks: capture/list/delete round-trip

No network, no Redis, no DB, no real credentials. The ReplayBuffer works
standalone (record()) without an event bus; the router-level bus wiring
is coordinator-verified at startup.
"""

from __future__ import annotations

import time

import pytest

from app.services.replay.buffer import ReplayBuffer
from app.services.replay.highlights import HighlightDetector
from app.services.replay.service import ReplayService

# Anchored to real "now" minus a few minutes: replay pruning always runs
# against time.time(), so synthetic timestamps must live inside the live
# window or they'd be (correctly) pruned before scoring.
BASE_TS = time.time() - 300.0


def _kill(
    buffer: ReplayBuffer,
    killer: str,
    victim: str,
    ts: float,
    killer_team: str = "Team A",
    victim_team: str = "Team B",
    is_knock: bool = False,
) -> None:
    buffer.record(
        event_type="knock_event" if is_knock else "kill_event",
        payload={
            "killer": killer,
            "killerTeam": killer_team,
            "victim": victim,
            "victimTeam": victim_team,
            "weapon": "M416",
            "isKnock": is_knock,
            "timestamp": ts,
        },
        ts=ts,
        event_id=f"{killer}-{victim}-{ts}",
    )


# ── buffer pruning ─────────────────────────────────────────────


def test_buffer_prunes_events_outside_window() -> None:
    buf = ReplayBuffer(window_seconds=300)
    _kill(buf, "P1", "V1", BASE_TS)                    # old: outside window
    _kill(buf, "P2", "V2", BASE_TS + 100)              # inside
    _kill(buf, "P3", "V3", BASE_TS + 350)              # inside

    removed = buf.prune(now=BASE_TS + 350)
    assert removed == 1
    remaining = buf.events_between(BASE_TS - 1000, BASE_TS + 1000, now=BASE_TS + 350)
    assert [e.payload["killer"] for e in remaining] == ["P2", "P3"]


def test_buffer_window_is_configurable() -> None:
    buf = ReplayBuffer(window_seconds=60)
    _kill(buf, "P1", "V1", BASE_TS)
    removed = buf.prune(now=BASE_TS + 61)
    assert removed == 1
    assert len(buf.recent(now=BASE_TS + 61)) == 0


# ── clip retrieval ─────────────────────────────────────────────


def test_get_clip_returns_events_oldest_first() -> None:
    svc = ReplayService(window_seconds=3600)
    # Record out of order; clip must come back chronological.
    _kill(svc.buffer, "P3", "V3", BASE_TS + 30)
    _kill(svc.buffer, "P1", "V1", BASE_TS + 10)
    _kill(svc.buffer, "P2", "V2", BASE_TS + 20)

    clip = svc.get_clip(BASE_TS, BASE_TS + 60)
    assert clip.event_count == 3
    assert [e["payload"]["killer"] for e in clip.events] == ["P1", "P2", "P3"]
    assert clip.is_video is False  # honest marker: event replay, not video


def test_get_clip_rejects_inverted_window() -> None:
    svc = ReplayService()
    with pytest.raises(ValueError):
        svc.get_clip(BASE_TS + 10, BASE_TS)


# ── multi-kill scoring ─────────────────────────────────────────


def test_multi_kill_detected_and_scored() -> None:
    buf = ReplayBuffer(window_seconds=3600)
    _kill(buf, "Ninja", "V1", BASE_TS)
    _kill(buf, "Ninja", "V2", BASE_TS + 4)
    _kill(buf, "Ninja", "V3", BASE_TS + 9)
    _kill(buf, "Other", "V4", BASE_TS + 2)  # unrelated kill: must not join

    reel = HighlightDetector().build_reel(buf.recent(), limit=10)
    multi = [m for m in reel if m.kind == "multi_kill"]
    assert len(multi) == 1
    assert multi[0].score == 60
    assert multi[0].player == "Ninja"
    assert len(multi[0].event_ids) == 3
    assert "Triple kill" in multi[0].reason


def test_quad_kill_scores_higher_than_triple() -> None:
    buf = ReplayBuffer(window_seconds=3600)
    for i in range(4):
        _kill(buf, "Ace", f"V{i}", BASE_TS + i * 2)

    reel = HighlightDetector().build_reel(buf.recent(), limit=10)
    multi = [m for m in reel if m.kind == "multi_kill"]
    assert multi[0].score == 80
    assert "Quad kill" in multi[0].reason


def test_kills_spread_too_wide_are_not_multi_kill() -> None:
    buf = ReplayBuffer(window_seconds=3600)
    _kill(buf, "Slow", "V1", BASE_TS)
    _kill(buf, "Slow", "V2", BASE_TS + 30)
    _kill(buf, "Slow", "V3", BASE_TS + 60)

    reel = HighlightDetector().build_reel(buf.recent(), limit=10)
    assert [m for m in reel if m.kind == "multi_kill"] == []


# ── squad wipe / clutch ────────────────────────────────────────


def test_squad_wipe_detected() -> None:
    buf = ReplayBuffer(window_seconds=3600)
    for i, killer in enumerate(["A", "B", "C", "D"]):
        _kill(buf, killer, f"Victim{i}", BASE_TS + i * 5,
              killer_team="Attackers", victim_team="WipedSquad")

    reel = HighlightDetector().build_reel(buf.recent(), limit=10)
    wipes = [m for m in reel if m.kind == "squad_wipe"]
    assert len(wipes) == 1
    assert wipes[0].score == 90
    assert wipes[0].team == "WipedSquad"
    assert "Squad wipe" in wipes[0].reason


def test_clutch_detected_when_teammates_silent() -> None:
    buf = ReplayBuffer(window_seconds=3600)
    _kill(buf, "Hero", "V1", BASE_TS, killer_team="Lone")
    _kill(buf, "Hero", "V2", BASE_TS + 6, killer_team="Lone")
    # A teammate of another team scores — must not disqualify the clutch.
    _kill(buf, "Rando", "V3", BASE_TS + 3, killer_team="Elsewhere")

    reel = HighlightDetector().build_reel(buf.recent(), limit=10)
    clutches = [m for m in reel if m.kind == "clutch"]
    assert len(clutches) == 1
    assert clutches[0].score == 75
    assert clutches[0].player == "Hero"


def test_no_clutch_when_teammate_also_scores() -> None:
    buf = ReplayBuffer(window_seconds=3600)
    _kill(buf, "P1", "V1", BASE_TS, killer_team="Squad")
    _kill(buf, "P2", "V2", BASE_TS + 4, killer_team="Squad")

    reel = HighlightDetector().build_reel(buf.recent(), limit=10)
    assert [m for m in reel if m.kind == "clutch"] == []


# ── empty-reel honesty ─────────────────────────────────────────


def test_empty_buffer_gives_empty_reel() -> None:
    buf = ReplayBuffer(window_seconds=3600)
    assert HighlightDetector().build_reel(buf.recent(), limit=10) == []


def test_knocks_alone_never_score() -> None:
    buf = ReplayBuffer(window_seconds=3600)
    for i in range(5):  # plenty of "action", zero kills
        _kill(buf, "P1", f"V{i}", BASE_TS + i, is_knock=True)
    assert HighlightDetector().build_reel(buf.recent(), limit=10) == []


# ── marks ──────────────────────────────────────────────────────


def test_mark_capture_list_delete_round_trip() -> None:
    svc = ReplayService(window_seconds=3600)
    _kill(svc.buffer, "P1", "V1", BASE_TS)

    mark = svc.capture_mark(label="Big fight")
    assert mark.label == "Big fight"
    assert svc.list_marks()[0].id == mark.id

    assert svc.delete_mark(mark.id) is True
    assert svc.list_marks() == []
    assert svc.delete_mark("nonexistent") is False
