# backend/tests/unit/test_replay_candidate_contract.py
"""Phase 25 Defect #1 — the LiveDirector -> ReplayQueue JOINT contract test.

Both sides' unit suites passed while the seam itself was broken: the
publisher emitted ``replay.candidate`` with {fight_id, teams, window,
intensity, ...} and the consumer required kind + flat
window_start/window_end, so EVERY simultaneous-fight hand-off was
silently dropped as underspecified.

This test drives the REAL path end-to-end — real EventBus (only the
Redis transport mocked, the conftest / test_live_director pattern),
real LiveDirector, real DirectorService, real ReplayQueue subscribed
via ``queue.subscribe_to(bus)`` — with two simultaneous promoted
fights, and asserts the queue actually holds fight #2.

THE CONTRACT (documented in both modules' docstrings):
  fight_id, teams, window {started_at, last_activity_at}, intensity,
  is_clutch, reason, source="live_director",
  kind ("clutch" when is_clutch else "fight"),
  window_start (= fight started_at), window_end (= last_activity_at),
  score (= int(intensity)), moment_id (= fight_id),
  team (first team), opponents (remaining teams),
  slot_number (challenger candidate's slot).
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from app.core.event_bus import EventBus, EventPriority
from app.services.director_service import DirectorService
from app.services.live_director import LiveDirector
from app.services.replay.queue import (
    CANDIDATE_ACCEPTED_KINDS,
    QUALIFYING_KINDS,
    STATE_PREPARING,
    STATE_QUEUED,
    STATE_READY,
    ReplayQueue,
)
from app.services.replay.renderer import HighlightRenderer
from app.services.replay.service import ReplayService
from app.services.vision_intelligence.attribution import (
    StaticSlotResolver,
    TeamAttribution,
)

pytestmark = pytest.mark.unit

SLOTS = {
    1: TeamAttribution(slot_number=1, team_id="team-a", team_name="Team A"),
    2: TeamAttribution(slot_number=2, team_id="team-b", team_name="Team B"),
    3: TeamAttribution(slot_number=3, team_id="team-c", team_name="Team C"),
    4: TeamAttribution(slot_number=4, team_id="team-d", team_name="Team D"),
}

LIVE_QUEUE_STATES = (STATE_PREPARING, STATE_READY, STATE_QUEUED)


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _real_bus() -> EventBus:
    """A real EventBus with only the Redis transport mocked."""
    bus = EventBus()
    bus._redis = AsyncMock()
    bus._redis.xadd = AsyncMock(return_value="1-0")
    bus._pubsub_redis = AsyncMock()
    bus._pubsub_redis.publish = AsyncMock(return_value=1)
    bus._consumer_redis = AsyncMock()
    bus._running = True
    return bus


class Rig:
    """LiveDirector + DirectorService + ReplayQueue on one real bus.

    Team A starts on PGM (DirectorService default active_camera == 1).
    The queue is subscribed via its real ``subscribe_to(bus)`` seam,
    and every ``handle_candidate`` outcome is spied so a silent drop
    (a None return) fails the test instead of passing unnoticed.
    """

    def __init__(self, tmp_path, phase: str = "mid_game"):
        self.clock = FakeClock()
        self.phase = phase
        self.bus = _real_bus()
        self.director_service = DirectorService()
        # Phase 28: fresh default is semi_auto; this suite tests the
        # full_auto firing path, so pin it explicitly (sync rig —
        # set_mode is async; same _state pattern as
        # test_suggestion_camera_integration).
        self.director_service._state["mode"] = "full_auto"
        self.live = LiveDirector(
            bus=self.bus,
            director_service=self.director_service,
            slot_resolver=StaticSlotResolver(SLOTS),
            phase_provider=lambda: self.phase,
            clock=self.clock,
        )
        self.live.start()

        renderer = HighlightRenderer(
            highlights_dir=tmp_path / "highlights",
            recordings_dir=tmp_path / "recordings",
        )
        self.replay_service = ReplayService(window_seconds=3600, renderer=renderer)
        self.queue = ReplayQueue(
            self.replay_service,
            recorder=None,
            director_provider=lambda: self.director_service.snapshot(),
            phase_provider=lambda: self.phase,
            fights_provider=lambda: [],
        )
        assert self.queue.subscribe_to(self.bus) is not None

        # Spy on the consumer entry point: record (payload, result).
        self.candidate_results: List[Any] = []
        _orig_handle = self.queue.handle_candidate

        def _spy(payload):
            result = _orig_handle(payload)
            self.candidate_results.append(result)
            return result

        self.queue.handle_candidate = _spy  # type: ignore[method-assign]

        # Collector for the raw published candidate payloads.
        self.published: List[Dict[str, Any]] = []

        async def _collect(record):
            self.published.append(record.event.data)

        self.bus.subscribe(
            subscriber_id="test_contract_collector",
            callback=_collect,
            event_types=["replay.candidate"],
        )

    async def publish(self, event_type: str, data: Dict[str, Any]) -> None:
        await self.bus.publish(
            event_type=event_type,
            data=data,
            priority=EventPriority.HIGH,
            source="test",
            match_id="m-1",
        )

    async def fight(
        self, fight_id: str, teams: List[str], intensity: float, **extra
    ) -> None:
        await self.publish(
            "fight.detected",
            {
                "fight_id": fight_id,
                "teams_involved": teams,
                "intensity": intensity,
                "fight_type": "mid_range",
                "location": {"x": 10.0, "y": 10.0},
                "match_id": "m-1",
                **extra,
            },
        )


@pytest.fixture
def rig(tmp_path) -> Rig:
    return Rig(tmp_path)


# ── THE joint test: two simultaneous fights, fight #2 reaches the queue ──


async def test_simultaneous_fight_handoff_reaches_replay_queue(rig: Rig):
    # Fight 1 involves the on-air team A: PGM is committed to it.
    await rig.fight("fight-1", ["team-a", "team-c"], 80.0)
    assert rig.director_service.snapshot()["active_camera"] == 1
    assert rig.published == []  # no hand-off for the on-air fight itself

    # 10 s later (mid_game min hold = 45 s): a hotter second fight.
    rig.clock.advance(10)
    fight2_started_at = rig.clock()
    await rig.fight("fight-2", ["team-b", "team-d"], 90.0)

    # (a) A replay.candidate was published, carrying the FULL contract.
    assert len(rig.published) == 1
    payload = rig.published[0]
    assert payload["fight_id"] == "fight-2"
    assert set(payload["teams"]) == {"team-b", "team-d"}
    assert payload["window"] == {
        "started_at": fight2_started_at,
        "last_activity_at": fight2_started_at,
    }
    assert payload["intensity"] == 90.0
    assert payload["is_clutch"] is False
    assert payload["source"] == "live_director"
    # The flat fields the consumer requires:
    assert payload["kind"] == "fight"  # truthful: not a fabricated detector kind
    assert payload["window_start"] == fight2_started_at
    assert payload["window_end"] == fight2_started_at
    assert payload["score"] == 90  # int(intensity), not the default 80
    assert payload["moment_id"] == "fight-2"
    assert payload["team"] == "team-b"  # first team of the fight
    assert payload["opponents"] == ["team-d"]
    assert payload["slot_number"] == 2  # challenger candidate's slot (team-b)

    # (c) NOTHING was dropped as underspecified: the consumer accepted
    # the candidate (its one handle_candidate call returned an entry).
    assert len(rig.candidate_results) == 1
    assert rig.candidate_results[0] is not None

    # (b) The queue holds an entry for fight #2, matching the fight.
    entries = rig.queue.list_entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry is rig.candidate_results[0]
    assert entry.moment_id == "fight-2"
    assert entry.state in LIVE_QUEUE_STATES
    assert entry.kind == "fight"
    assert entry.score == 90
    assert entry.window_start == fight2_started_at
    assert entry.window_end == fight2_started_at
    assert entry.team == "team-b"
    assert entry.opponents == ["team-d"]
    assert entry.slot_number == 2
    assert entry.source == "replay_candidate"

    # (d) PGM stayed committed to fight #1; there is no PiP state —
    # the second fight went to the replay queue instead of a box.
    snap = rig.director_service.snapshot()
    assert snap["active_camera"] == 1
    assert snap["scene"] == "main"
    assert rig.live.status()["current_fight_id"] == "fight-1"
    assert not any("pip" in key.lower() for key in snap)
    assert not any("split" in key.lower() for key in snap)


async def test_clutch_simultaneous_fight_carries_clutch_kind(rig: Rig):
    await rig.fight("fight-1", ["team-a", "team-c"], 80.0)
    rig.clock.advance(10)
    await rig.fight("fight-2", ["team-b", "team-d"], 90.0, is_clutch=True)

    assert len(rig.published) == 1
    payload = rig.published[0]
    # Kind honesty: "clutch" is truthful here (the fight IS clutch);
    # score stays int(intensity) — the +25 clutch director bonus is a
    # scoring term, not the moment's intensity.
    assert payload["kind"] == "clutch"
    assert payload["score"] == 90

    entries = rig.queue.list_entries()
    assert len(entries) == 1
    assert entries[0].kind == "clutch"
    assert entries[0].score == 90
    assert entries[0].moment_id == "fight-2"
    assert entries[0].state in LIVE_QUEUE_STATES


# ── Consumer honesty preserved ─────────────────────────────────────


def _bare_queue(tmp_path) -> ReplayQueue:
    renderer = HighlightRenderer(
        highlights_dir=tmp_path / "highlights",
        recordings_dir=tmp_path / "recordings",
    )
    svc = ReplayService(window_seconds=3600, renderer=renderer)
    return ReplayQueue(
        svc,
        recorder=None,
        director_provider=lambda: {"mode": "manual", "active_camera": 1},
        phase_provider=lambda: "mid_game",
        fights_provider=lambda: [],
    )


def test_candidate_missing_window_still_dropped(tmp_path):
    queue = _bare_queue(tmp_path)
    # Kind but no window (flat, ts, or nested): still underspecified.
    assert queue.handle_candidate({"kind": "fight"}) is None
    assert queue.handle_candidate({"kind": "fight", "score": 90}) is None
    assert queue.handle_candidate({"kind": "fight", "window_start": 100.0}) is None
    # Window but no kind: still underspecified.
    assert (
        queue.handle_candidate({"window_start": 100.0, "window_end": 110.0})
        is None
    )
    assert queue.list_entries() == []


def test_candidate_unknown_kind_dropped_and_fight_kind_gated(tmp_path):
    queue = _bare_queue(tmp_path)
    # An invented kind is not accepted on the candidate path either.
    assert (
        queue.handle_candidate(
            {"kind": "invented_kind", "window_start": 100.0, "window_end": 110.0}
        )
        is None
    )
    assert queue.list_entries() == []
    # The kind sets themselves: "fight" is candidate-only — the
    # detector poll gate (QUALIFYING_KINDS) must NOT accept it.
    assert "fight" in CANDIDATE_ACCEPTED_KINDS
    assert "fight" not in QUALIFYING_KINDS
    assert QUALIFYING_KINDS <= CANDIDATE_ACCEPTED_KINDS
