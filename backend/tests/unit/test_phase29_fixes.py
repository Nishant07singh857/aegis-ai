# backend/tests/unit/test_phase29_fixes.py
"""Phase 29 post-QA fixes — D-2 failed-tap cooldown + D-1 DEAD
confirmation gate (service layer, observer_pilot/service.py).

D-2 (QA measured repro): a scene that ignores taps ends every
execution ``failed_stay`` (2 taps sent); without a cooldown the
next fresh state re-fires R1 and pokes again — 4 taps by the
second cycle, repeating on a live phone with a broken
calibration. Fix: per-feed ``failed_retry_cooldown_s`` (default
30 s); inside it, NEW decisions neither execute nor suggest and
each suppression is journaled; ``verified`` clears it; manual
override is never blocked but its ``failed_stay`` restarts it.

D-1 (inherited reader defect, pilot-side mitigation): the
TeamStatusReader can read a low-HP ALIVE card as DEAD. Fix: an
R1_SPECTATED_DEAD action needs the spectated DEAD reading in
``dead_confirm_evals`` (default 2) consecutive evaluations;
ALIVE/KNOCKED resets the streak; KNOCKED (R2) is NOT gated.

Fakes mirror test_phase29_w4_pilot_service.py (fake monitor,
scripted executor, fake PilotControl). The journal here is a
recording fake — durable-DB journaling is covered by the W4
suite; these tests assert the gate behaviour + journal ROWS.
D-2 tests pin dead_confirm_evals=1 to isolate the cooldown from
the D-1 gate; D-1 tests use the real defaults.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Dict, List, Optional

import pytest

from app.services.observer_pilot.service import (
    ObserverPilotService,
    PilotServiceConfig,
)
from app.services.observer_pilot.types import (
    FeedPOVState,
    PlayerPOV,
    POVPlayerStatus,
)

pytestmark = pytest.mark.unit

ALIVE = POVPlayerStatus.ALIVE
KNOCKED = POVPlayerStatus.KNOCKED
DEAD = POVPlayerStatus.DEAD
NAMES = ["Alpha", "GodL", "Nova", "Rex"]


# ── fakes (W4 pattern) ───────────────────────────────────────────


@dataclass
class FakeTapResult:
    status: str = "verified"
    taps_sent: int = 1
    detail: str = "fake tap"
    slot_number: Optional[int] = None
    to_index: Optional[int] = None


class FakeControl:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.disabled_slots = set()

    def allows(self, slot) -> bool:
        return bool(self.enabled) and int(slot) not in self.disabled_slots


class FakeExecutor:
    """Scripted results shared by execute / execute_target (the
    service funnels both through the same outcome bookkeeping)."""

    def __init__(self, results: Optional[List[FakeTapResult]] = None):
        self.control = FakeControl()
        self._results = list(results or [])
        self.calls: List[dict] = []
        self.target_calls: List[dict] = []

    def _next(self):
        if self._results:
            return self._results.pop(0)
        return FakeTapResult(status="verified", taps_sent=1)

    def execute(self, decision, now):
        self.calls.append(
            {"slot": decision.slot_number, "to": decision.to_index,
             "rule": decision.rule, "now": now}
        )
        return self._next()

    def execute_target(self, slot_number, source_id, target_index, reason, now):
        self.target_calls.append(
            {"slot": slot_number, "to": target_index, "reason": reason}
        )
        return self._next()

    @property
    def total_calls(self) -> int:
        return len(self.calls) + len(self.target_calls)


class FakeMonitor:
    def __init__(self, states: Optional[Dict[int, FeedPOVState]] = None):
        self._states = dict(states or {})

    def pov_state(self, slot_number):
        return self._states.get(int(slot_number))

    def pov_states(self):
        return dict(self._states)


class RecordingJournal:
    def __init__(self):
        self.entries: List[dict] = []

    def record(self, entry):
        self.entries.append(copy.deepcopy(entry))
        return f"row-{len(self.entries)}"

    def status(self):
        return {"stats": {"events_seen": len(self.entries)},
                "ring_size": len(self.entries)}

    def by_fate(self, fate: str) -> List[dict]:
        return [e for e in self.entries if e.get("fate") == fate]


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def make_state(
    *,
    slot: int = 1,
    spectated: int = 0,
    statuses=(ALIVE, ALIVE, ALIVE, ALIVE),
    observed_at: float = 1000.0,
    hp_velocities=(None, None, None, None),
) -> FeedPOVState:
    players = tuple(
        PlayerPOV(
            roster_index=i,
            player_id=f"p{i}",
            name=NAMES[i],
            status=statuses[i],
            hp_velocity=hp_velocities[i],
            is_spectated=(i == spectated),
        )
        for i in range(4)
    )
    return FeedPOVState(
        slot_number=slot,
        source_id=f"aegis-observer-slot-{slot:02d}",
        team_id="team-1",
        observed_at=observed_at,
        spectated_index=spectated,
        players=players,
        identity_known=True,
        identity_source="both",
    )


def dead_state(**kw) -> FeedPOVState:
    """Spectated idx0 DEAD, idx1 alive -> R1 decision to idx1."""
    return make_state(statuses=(DEAD, ALIVE, ALIVE, ALIVE), **kw)


FAILED = FakeTapResult(status="failed_stay", taps_sent=2)
VERIFIED = FakeTapResult(status="verified", taps_sent=1)


def build(*, mode="full_auto", executor=None, clock=None, **config_kw):
    clock = clock or Clock()
    journal = RecordingJournal()
    executor = executor or FakeExecutor()
    service = ObserverPilotService(
        monitor=FakeMonitor({1: dead_state()}),
        executor=executor,
        config=PilotServiceConfig(**config_kw),
        mode_provider=lambda: (mode, False),
        engagement_provider=lambda: {"teams": {}},
        phase_provider=lambda: "mid_game",
        journal=journal,
        clock=clock,
    )
    return service, journal, executor, clock


def build_d2(**kw):
    """D-2 isolation: D-1 gate at 1 (immediate), cooldown 30 s."""
    kw.setdefault("dead_confirm_evals", 1)
    kw.setdefault("failed_retry_cooldown_s", 30.0)
    return build(**kw)


# ══ D-2: failed-tap cooldown ═════════════════════════════════════


@pytest.mark.asyncio
async def test_failed_stay_starts_cooldown_and_suppresses_next_eval():
    executor = FakeExecutor(results=[FAILED])
    service, journal, _ex, clock = build_d2(executor=executor)

    outcome = await service.evaluate_feed(dead_state(observed_at=1000.0), now=1000.0)
    assert outcome["outcome"] == "executed"
    assert outcome["verification"] == "failed_stay"
    assert len(executor.calls) == 1
    # Cooldown runs until 1000 + 30.
    assert service._failed_cooldown_until[1] == 1030.0

    # QA's repro point: a fresh state 20 s later must NOT re-fire.
    outcome = await service.evaluate_feed(dead_state(observed_at=1020.0), now=1020.0)
    assert outcome["outcome"] == "suppressed_failed_cooldown"
    assert len(executor.calls) == 1  # 0 new taps (was +2 pre-fix)
    assert outcome["failed_cooldown_remaining_s"] == pytest.approx(10.0)

    rows = journal.by_fate("suppressed")
    assert len(rows) == 1
    row = rows[0]
    assert row["taps_sent"] == 0
    assert "suppressed_failed_cooldown" in row["reason"]
    assert row["evidence"]["suppressed_failed_cooldown"] is True
    assert row["evidence"]["cooldown_remaining_s"] == pytest.approx(10.0)
    assert service.stats["suppressed_failed_cooldown"] == 1

    clock.t = 1020.0
    feed = service.status()["feeds"][0]
    assert feed["failed_cooldown_active"] is True
    assert feed["failed_cooldown_until"] == 1030.0
    assert feed["failed_cooldown_remaining_s"] == pytest.approx(10.0)
    assert feed["last_note"]["blocked_rule"] == "R1_SPECTATED_DEAD"
    assert "suppressed_failed_cooldown" in feed["last_note"]["reason"]


@pytest.mark.asyncio
async def test_cooldown_expiry_allows_one_retry_cycle():
    executor = FakeExecutor(results=[FAILED, FAILED])
    service, _j, _ex, _c = build_d2(executor=executor)
    await service.evaluate_feed(dead_state(observed_at=1000.0), now=1000.0)
    suppressed = await service.evaluate_feed(
        dead_state(observed_at=1010.0), now=1010.0
    )
    assert suppressed["outcome"] == "suppressed_failed_cooldown"
    # At exactly the cooldown end the feed is back to normal: one
    # new retry cycle is allowed (and its failure re-arms 30 s).
    outcome = await service.evaluate_feed(dead_state(observed_at=1030.0), now=1030.0)
    assert outcome["outcome"] == "executed"
    assert len(executor.calls) == 2
    assert service._failed_cooldown_until[1] == 1060.0


@pytest.mark.asyncio
async def test_cooldown_suppresses_suggestions_too():
    # semi_auto: the failed execution arrives via an APPROVED
    # suggestion; after that, new decisions are not even suggested
    # while the cooldown runs.
    executor = FakeExecutor(results=[FAILED])
    service, journal, _ex, clock = build_d2(mode="semi_auto", executor=executor)
    outcome = await service.evaluate_feed(dead_state(observed_at=1000.0), now=1000.0)
    assert outcome["outcome"] == "suggested"
    clock.t = 1000.0
    result = await service.approve_suggestion(outcome["suggestion"]["id"], by="op")
    assert result["status"] == "failed_stay"
    assert service._failed_cooldown_until[1] == 1030.0

    outcome = await service.evaluate_feed(dead_state(observed_at=1010.0), now=1010.0)
    assert outcome["outcome"] == "suppressed_failed_cooldown"
    assert service.pending_suggestions() == []
    assert len(journal.by_fate("suppressed")) == 1


@pytest.mark.asyncio
async def test_override_bypasses_cooldown_and_verified_clears_it():
    executor = FakeExecutor(results=[FAILED, VERIFIED, VERIFIED])
    service, _j, _ex, clock = build_d2(executor=executor)
    await service.evaluate_feed(dead_state(observed_at=1000.0), now=1000.0)
    suppressed = await service.evaluate_feed(
        dead_state(observed_at=1010.0), now=1010.0
    )
    assert suppressed["outcome"] == "suppressed_failed_cooldown"

    # Inside the cooldown (t=1020 < 1030) the operator's own
    # override still runs — a human may retry on purpose.
    clock.t = 1020.0
    result = await service.override(1, 2, by="op")
    assert result["status"] == "verified"
    assert result["executed"] is True
    assert len(executor.target_calls) == 1
    # Verified cleared the cooldown BEFORE its original expiry:
    # an auto evaluation at t=1025 (still < 1030) executes again.
    assert 1 not in service._failed_cooldown_until
    outcome = await service.evaluate_feed(dead_state(observed_at=1025.0), now=1025.0)
    assert outcome["outcome"] == "executed"
    assert len(executor.calls) == 2


@pytest.mark.asyncio
async def test_override_failed_stay_restarts_cooldown():
    executor = FakeExecutor(results=[FAILED, FAILED])
    service, _j, _ex, clock = build_d2(executor=executor)
    await service.evaluate_feed(dead_state(observed_at=1000.0), now=1000.0)
    assert service._failed_cooldown_until[1] == 1030.0

    clock.t = 1020.0
    result = await service.override(1, 2, by="op")
    assert result["status"] == "failed_stay"
    # Restarted from the override's own failure, not the original.
    assert service._failed_cooldown_until[1] == 1050.0

    # t=1040 is past the ORIGINAL expiry (1030) but inside the
    # restarted cooldown: still suppressed, 10 s remaining.
    outcome = await service.evaluate_feed(dead_state(observed_at=1040.0), now=1040.0)
    assert outcome["outcome"] == "suppressed_failed_cooldown"
    assert outcome["failed_cooldown_remaining_s"] == pytest.approx(10.0)
    assert len(executor.calls) == 1  # auto path never re-tapped


# ══ D-1: DEAD-confirmation gate (real defaults) ══════════════════


@pytest.mark.asyncio
async def test_single_dead_reading_is_held_unconfirmed():
    service, journal, executor, clock = build()  # dead_confirm_evals=2
    outcome = await service.evaluate_feed(dead_state(observed_at=1000.0), now=1000.0)
    assert outcome["outcome"] == "dead_unconfirmed"
    assert outcome["dead_confirm_count"] == 1
    assert outcome["dead_confirm_evals"] == 2
    assert executor.total_calls == 0

    rows = journal.by_fate("dead_unconfirmed")
    assert len(rows) == 1
    assert rows[0]["rule"] == "R1_SPECTATED_DEAD"
    assert rows[0]["taps_sent"] == 0
    assert rows[0]["evidence"]["dead_confirm_count"] == 1
    assert service.stats["dead_unconfirmed"] == 1

    # Honesty: status shows the state as DEAD-but-unconfirmed —
    # the POV reading itself is NOT relabelled ALIVE.
    feed = service.status()["feeds"][0]
    assert feed["dead_unconfirmed"] is True
    assert feed["dead_confirm_count"] == 1
    assert feed["dead_confirm_evals"] == 2
    assert feed["pov"]["players"][0]["status"] == "dead"
    assert feed["last_note"]["blocked_rule"] == "R1_SPECTATED_DEAD"
    assert "dead_unconfirmed" in feed["last_note"]["reason"]


@pytest.mark.asyncio
async def test_second_consecutive_dead_confirms_and_taps():
    service, _j, executor, _c = build()
    first = await service.evaluate_feed(dead_state(observed_at=1000.0), now=1000.0)
    assert first["outcome"] == "dead_unconfirmed"
    second = await service.evaluate_feed(dead_state(observed_at=1001.0), now=1001.0)
    assert second["outcome"] == "executed"
    assert len(executor.calls) == 1
    assert executor.calls[0]["to"] == 1
    # A third consecutive DEAD evaluation stays confirmed (the
    # streak is not consumed by the action).
    third = await service.evaluate_feed(dead_state(observed_at=1002.0), now=1002.0)
    assert third["outcome"] == "executed"
    assert len(executor.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("reset_status", [ALIVE, KNOCKED])
async def test_alive_or_knocked_between_dead_resets_streak(reset_status):
    service, _j, executor, _c = build()
    await service.evaluate_feed(dead_state(observed_at=1000.0), now=1000.0)
    reset = await service.evaluate_feed(
        make_state(
            statuses=(reset_status, ALIVE, ALIVE, ALIVE),
            observed_at=1001.0,
        ),
        now=1001.0,
    )
    assert reset["outcome"] == "no_action"
    assert service._dead_streak[1] == 0

    # One DEAD after the reset is again only the FIRST reading.
    held = await service.evaluate_feed(dead_state(observed_at=1002.0), now=1002.0)
    assert held["outcome"] == "dead_unconfirmed"
    assert executor.total_calls == 0
    confirmed = await service.evaluate_feed(
        dead_state(observed_at=1003.0), now=1003.0
    )
    assert confirmed["outcome"] == "executed"


@pytest.mark.asyncio
async def test_dead_of_a_different_player_starts_a_new_streak():
    service, _j, executor, _c = build()
    await service.evaluate_feed(dead_state(observed_at=1000.0), now=1000.0)
    # The spectated player CHANGED (idx1, also read DEAD): that is
    # a first reading for this player, not a confirmation of idx0.
    other = make_state(
        spectated=1, statuses=(ALIVE, DEAD, ALIVE, ALIVE), observed_at=1001.0
    )
    outcome = await service.evaluate_feed(other, now=1001.0)
    assert outcome["outcome"] == "dead_unconfirmed"
    assert outcome["dead_confirm_count"] == 1
    assert executor.total_calls == 0


@pytest.mark.asyncio
async def test_dead_gate_holds_suggestions_until_confirmed():
    service, _j, executor, _c = build(mode="semi_auto")
    first = await service.evaluate_feed(dead_state(observed_at=1000.0), now=1000.0)
    assert first["outcome"] == "dead_unconfirmed"
    assert service.pending_suggestions() == []
    second = await service.evaluate_feed(dead_state(observed_at=1001.0), now=1001.0)
    assert second["outcome"] == "suggested"
    assert len(service.pending_suggestions()) == 1
    assert executor.total_calls == 0  # semi_auto never taps directly


@pytest.mark.asyncio
async def test_dead_confirm_evals_1_restores_immediate_behaviour():
    service, _j, executor, _c = build(dead_confirm_evals=1)
    outcome = await service.evaluate_feed(dead_state(observed_at=1000.0), now=1000.0)
    assert outcome["outcome"] == "executed"
    assert len(executor.calls) == 1


@pytest.mark.asyncio
async def test_knocked_r2_is_not_confirmation_gated():
    # The defect is DEAD-only: a KNOCKED spectated player with a
    # fighting teammate (R2) acts on the FIRST evaluation, even
    # with dead_confirm_evals at its default of 2.
    service, _j, executor, _c = build()
    state = make_state(
        statuses=(KNOCKED, ALIVE, ALIVE, ALIVE),
        hp_velocities=(None, -0.2, None, None),
        observed_at=1000.0,
    )
    outcome = await service.evaluate_feed(state, now=1000.0)
    assert outcome["outcome"] == "executed"
    assert executor.calls[0]["rule"] == "R2_SPECTATED_KNOCKED"
