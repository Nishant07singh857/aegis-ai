# backend/tests/unit/test_director_labeler.py
"""Phase 27 (W2) — Outcome Labeler proof tests.

Scripted event streams over a REAL EventBus (only the Redis transport
is mocked, the repo pattern) + journal decisions persisted in sqlite
via the injectable session factory prove:

* each label (good_call / fizzle / premature_exit / missed_fight)
  fires with correct evidence;
* premature_exit coexists with good_call on the same decision;
* a young decision (evidence window still open) stays unlabeled;
* an ambiguous decision (some activity, below every threshold)
  stays unlabeled — labels are never forced;
* idempotence: two passes over the same data add zero rows;
* an approved semi_auto suggestion earns good_call while its
  unapproved twin earns nothing;
* clone cuts are real switches (good_call fires for mode="clone");
* clone-grabbed shadow entries and manual holds earn no missed_fight;
* mode_transition rows are never labeled;
* a decision predating labeler coverage is skipped + counted
  (``skipped_no_coverage``), never guessed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.event_bus import EventBus, EventPriority
from app.db.base import Base
from app.services.director_learning.labeler import (
    LABELER_VERSION,
    OutcomeLabeler,
)
from app.services.director_learning.models import (
    DirectorDecision,
    DirectorDecisionLabel,
    DirectorSuggestionFate,
)

pytestmark = pytest.mark.unit

LEARNING_TABLES = [
    Base.metadata.tables[name]
    for name in (
        "director_decisions",
        "director_suggestion_fates",
        "director_shadow_divergences",
        "director_decision_labels",
        "director_learning_adjustments",
        "director_learning_state",
    )
]

T0 = 1_700_000_000.0


class FakeClock:
    def __init__(self, start: float = T0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest_asyncio.fixture()
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn, tables=LEARNING_TABLES
            )
        )
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    yield factory
    await engine.dispose()


def _real_bus() -> EventBus:
    bus = EventBus()
    bus._redis = AsyncMock()
    bus._redis.xadd = AsyncMock(return_value="1-0")
    bus._pubsub_redis = AsyncMock()
    bus._pubsub_redis.publish = AsyncMock(return_value=1)
    bus._consumer_redis = AsyncMock()
    bus._running = True
    return bus


class Rig:
    """Labeler on a real bus + sqlite journal tables, fake clock."""

    def __init__(self, maker):
        self.clock = FakeClock()
        self.bus = _real_bus()
        self.maker = maker
        self.labeler = OutcomeLabeler(
            self.bus, session_factory=maker, clock=self.clock
        )
        self.labeler.start()
        self._seq = 0

    async def publish(self, event_type: str, data: Dict[str, Any]) -> None:
        await self.bus.publish(
            event_type=event_type,
            data=data,
            priority=EventPriority.HIGH,
            source="test",
            match_id="m-1",
        )

    async def fight(self, at: float, fight_id: str, teams: List[str]) -> None:
        await self.publish(
            "fight.detected",
            {
                "fight_id": fight_id,
                "teams_involved": teams,
                "intensity": 80.0,
                "fight_type": "mid_range",
                "match_id": "m-1",
                "timestamp": at,
            },
        )

    async def kill(self, at: float, killer_team: str, victim_team: str) -> None:
        await self.publish(
            "kill_event",
            {
                "killer": "P1",
                "killerTeam": killer_team,
                "victim": "P2",
                "victimTeam": victim_team,
                "weapon": "M416",
                "isKnock": False,
                "timestamp": at,
            },
        )

    async def knock(self, at: float, killer_team: str, victim_team: str) -> None:
        await self.publish(
            "knock_event",
            {
                "killer": "P1",
                "killerTeam": killer_team,
                "victim": "P2",
                "victimTeam": victim_team,
                "weapon": "M416",
                "isKnock": True,
                "timestamp": at,
            },
        )

    async def decision(self, at: float, **overrides) -> str:
        self._seq += 1
        row = {
            "id": f"dec-{self._seq}",
            "ts": datetime.fromtimestamp(at, tz=timezone.utc),
            "mode": "full_auto",
            "phase": "mid_game",
            "trigger": "fight.detected",
            "outcome": "switch",
            "rule": "margin_default_15",
            "reason": "test decision",
            "margin_required": 15.0,
            "margin_actual": 35.0,
            "incumbent": {"team_id": "team-a", "slot": 1, "score": 50.0},
            "challenger": {
                "team_id": "team-b",
                "slot": 2,
                "score": 85.0,
                "fight_id": "fight-b",
            },
            "candidates": [
                {
                    "team_id": "team-a",
                    "slot": 1,
                    "score": 50.0,
                    "fight_id": "fight-a",
                    "is_incumbent": True,
                },
                {
                    "team_id": "team-b",
                    "slot": 2,
                    "score": 85.0,
                    "fight_id": "fight-b",
                    "is_incumbent": False,
                },
            ],
            "suggestion_id": None,
        }
        row.update(overrides)
        async with self.maker() as session:
            session.add(DirectorDecision(**row))
            await session.commit()
        return row["id"]

    async def fate(self, suggestion_id: str, fate: str) -> None:
        async with self.maker() as session:
            session.add(
                DirectorSuggestionFate(
                    suggestion_id=suggestion_id,
                    fate=fate,
                    decided_by="operator-1",
                    ts=datetime.fromtimestamp(self.clock.t, tz=timezone.utc),
                )
            )
            await session.commit()

    async def labels(self) -> List[DirectorDecisionLabel]:
        async with self.maker() as session:
            rows = (await session.execute(select(DirectorDecisionLabel))).scalars().all()
            return list(rows)

    async def labels_for(self, decision_id: str) -> List[DirectorDecisionLabel]:
        return [r for r in await self.labels() if r.decision_id == decision_id]


@pytest.fixture()
def rig_maker(maker):
    return maker


def _rig(maker) -> Rig:
    return Rig(maker)


# ── good_call ──────────────────────────────────────────────────────


async def test_good_call_from_combat_events(maker):
    rig = _rig(maker)
    t_dec = T0 + 10
    dec = await rig.decision(t_dec)
    # Landing fight produces 3 combat events within 45 s.
    await rig.fight(t_dec + 5, "fight-b", ["team-b", "team-c"])
    await rig.knock(t_dec + 10, "team-b", "team-c")
    await rig.knock(t_dec + 20, "team-c", "team-b")
    rig.clock.advance(10 + 60)
    summary = await rig.labeler.run_pass()

    rows = await rig.labels_for(dec)
    assert [r.label for r in rows] == ["good_call"]
    row = rows[0]
    assert row.labeler_version == LABELER_VERSION
    assert row.confidence_band in ("medium", "high")
    assert row.evidence["combat_events"] == 3
    assert row.evidence["counts_by_topic"]["fight.detected"] == 1
    assert row.evidence["counts_by_topic"]["knock.occurred"] == 2
    assert len(row.evidence["event_ids"]) == 3
    assert row.evidence["window_s"] == 45.0
    assert summary["labels_written"] == 1


async def test_good_call_from_two_kills(maker):
    rig = _rig(maker)
    t_dec = T0 + 10
    dec = await rig.decision(t_dec)
    await rig.kill(t_dec + 5, "team-b", "team-c")
    await rig.kill(t_dec + 15, "team-b", "team-d")
    rig.clock.advance(10 + 60)
    await rig.labeler.run_pass()

    rows = await rig.labels_for(dec)
    assert [r.label for r in rows] == ["good_call"]
    assert rows[0].evidence["kill_events"] == 2
    assert rows[0].confidence >= 0.8
    assert rows[0].confidence_band == "high"


# ── fizzle ─────────────────────────────────────────────────────────


async def test_fizzle_when_landing_goes_silent(maker):
    rig = _rig(maker)
    t_dec = T0 + 10
    dec = await rig.decision(t_dec)
    # Combat happens — but only for unrelated teams.
    await rig.fight(t_dec + 5, "fight-z", ["team-y", "team-z"])
    await rig.kill(t_dec + 10, "team-y", "team-z")
    rig.clock.advance(10 + 90)
    await rig.labeler.run_pass()

    rows = await rig.labels_for(dec)
    assert [r.label for r in rows] == ["fizzle"]
    assert rows[0].confidence >= 0.8
    assert rows[0].confidence_band == "high"
    assert rows[0].evidence["combat_events"] == 0
    # mid_game min fight hold (45 s) dominates the 20 s floor.
    assert rows[0].evidence["window_s"] == 45.0


# ── premature_exit (coexists with good_call) ───────────────────────


async def test_premature_exit_coexists_with_good_call(maker):
    rig = _rig(maker)
    t_dec = T0 + 10
    dec = await rig.decision(t_dec)
    # The fight we LEFT (team-a / fight-a) erupts within 20 s.
    await rig.kill(t_dec + 5, "team-a", "team-d")
    await rig.kill(t_dec + 10, "team-a", "team-c")
    # The landing ALSO produces combat -> good_call too.
    await rig.fight(t_dec + 8, "fight-b", ["team-b", "team-c"])
    await rig.knock(t_dec + 12, "team-b", "team-c")
    await rig.knock(t_dec + 18, "team-c", "team-b")
    rig.clock.advance(10 + 60)
    await rig.labeler.run_pass()

    rows = await rig.labels_for(dec)
    assert sorted(r.label for r in rows) == ["good_call", "premature_exit"]
    premature = [r for r in rows if r.label == "premature_exit"][0]
    assert premature.evidence["kill_events"] == 2
    assert premature.evidence["target"]["team_id"] == "team-a"
    assert premature.evidence["window_s"] == 20.0


# ── missed_fight ───────────────────────────────────────────────────


async def test_missed_fight_for_rejected_challenger(maker):
    rig = _rig(maker)
    t_dec = T0 + 10
    dec = await rig.decision(
        t_dec, outcome="rejected_challenger", rule="margin_default_15"
    )
    # Turned-away challenger erupts within 30 s; incumbent stays quiet.
    await rig.fight(t_dec + 5, "fight-b", ["team-b", "team-c"])
    await rig.kill(t_dec + 8, "team-b", "team-c")
    await rig.knock(t_dec + 12, "team-b", "team-c")
    await rig.knock(t_dec + 20, "team-c", "team-b")
    rig.clock.advance(10 + 40)
    await rig.labeler.run_pass()

    rows = await rig.labels_for(dec)
    assert [r.label for r in rows] == ["missed_fight"]
    assert rows[0].evidence["combat_events"] == 4
    assert rows[0].evidence["inputs"]["incumbent_combat_events"] == 0
    assert rows[0].evidence["window_s"] == 30.0


async def test_missed_fight_blocked_when_incumbent_also_active(maker):
    rig = _rig(maker)
    t_dec = T0 + 10
    dec = await rig.decision(
        t_dec, outcome="rejected_challenger", rule="margin_default_15"
    )
    await rig.fight(t_dec + 5, "fight-b", ["team-b", "team-c"])
    await rig.kill(t_dec + 8, "team-b", "team-c")
    await rig.knock(t_dec + 12, "team-b", "team-c")
    await rig.knock(t_dec + 20, "team-c", "team-b")
    # Incumbent's fight is busy too (>1 combat event): not a miss.
    await rig.kill(t_dec + 6, "team-a", "team-d")
    await rig.knock(t_dec + 9, "team-a", "team-d")
    rig.clock.advance(10 + 40)
    await rig.labeler.run_pass()
    assert await rig.labels_for(dec) == []


# ── neutrality: young + ambiguous decisions stay unlabeled ─────────


async def test_young_decision_stays_unlabeled(maker):
    rig = _rig(maker)
    t_dec = T0 + 10
    dec = await rig.decision(t_dec)
    await rig.fight(t_dec + 5, "fight-b", ["team-b", "team-c"])
    await rig.knock(t_dec + 6, "team-b", "team-c")
    await rig.knock(t_dec + 7, "team-c", "team-b")
    # Only 20 s past the decision: the 45 s window is still open.
    rig.clock.advance(10 + 20)
    summary = await rig.labeler.run_pass()
    assert await rig.labels_for(dec) == []
    assert summary["deferred_window_open"] == 1


async def test_ambiguous_decision_stays_unlabeled(maker):
    rig = _rig(maker)
    t_dec = T0 + 10
    dec = await rig.decision(t_dec)
    # Some activity on the landing, below every threshold (2 combat,
    # 0 kills by team-b) — and nonzero, so not a fizzle either.
    await rig.knock(t_dec + 5, "team-c", "team-b")
    await rig.knock(t_dec + 10, "team-c", "team-b")
    rig.clock.advance(10 + 90)
    await rig.labeler.run_pass()
    assert await rig.labels_for(dec) == []


# ── idempotence ────────────────────────────────────────────────────


async def test_second_pass_adds_zero_rows(maker):
    rig = _rig(maker)
    t_dec = T0 + 10
    dec = await rig.decision(t_dec)
    await rig.fight(t_dec + 5, "fight-b", ["team-b", "team-c"])
    await rig.knock(t_dec + 10, "team-b", "team-c")
    await rig.knock(t_dec + 20, "team-c", "team-b")
    rig.clock.advance(10 + 60)
    await rig.labeler.run_pass()
    first = await rig.labels()
    assert len(first) == 1
    summary2 = await rig.labeler.run_pass()
    second = await rig.labels()
    assert len(second) == 1
    assert summary2["labels_written"] == 0


# ── semi_auto: approved suggestion vs unapproved twin ──────────────


async def test_approved_suggestion_switch_vs_unapproved_twin(maker):
    rig = _rig(maker)
    t_dec = T0 + 10
    approved = await rig.decision(
        t_dec, mode="semi_auto", outcome="suggestion", suggestion_id="sug-ok"
    )
    twin = await rig.decision(
        t_dec, mode="semi_auto", outcome="suggestion", suggestion_id="sug-no"
    )
    await rig.fate("sug-ok", "approved")
    await rig.fate("sug-no", "rejected")
    # Identical landing evidence for both (same challenger target).
    await rig.fight(t_dec + 5, "fight-b", ["team-b", "team-c"])
    await rig.knock(t_dec + 10, "team-b", "team-c")
    await rig.knock(t_dec + 20, "team-c", "team-b")
    rig.clock.advance(10 + 60)
    await rig.labeler.run_pass()

    assert [r.label for r in await rig.labels_for(approved)] == ["good_call"]
    assert await rig.labels_for(twin) == []


# ── clone-mode deltas ──────────────────────────────────────────────


async def test_clone_cut_is_a_real_switch(maker):
    rig = _rig(maker)
    t_dec = T0 + 10
    dec = await rig.decision(t_dec, mode="clone", outcome="switch")
    await rig.fight(t_dec + 5, "fight-b", ["team-b", "team-c"])
    await rig.knock(t_dec + 10, "team-b", "team-c")
    await rig.knock(t_dec + 20, "team-c", "team-b")
    rig.clock.advance(10 + 60)
    await rig.labeler.run_pass()
    assert [r.label for r in await rig.labels_for(dec)] == ["good_call"]


async def test_clone_grabbed_shadow_entry_earns_no_missed_fight(maker):
    rig = _rig(maker)
    t_dec = T0 + 10
    grabbed = await rig.decision(
        t_dec, mode="clone", outcome="hold", rule="clone_grabbed_no_write"
    )
    manual = await rig.decision(
        t_dec, mode="manual", outcome="hold", rule="manual_mode_no_write"
    )
    # The turned-away challenger erupts — would be a missed_fight for
    # a genuine AI hold, but grabbed/manual entries are not decisions.
    await rig.fight(t_dec + 5, "fight-b", ["team-b", "team-c"])
    await rig.kill(t_dec + 8, "team-b", "team-c")
    await rig.knock(t_dec + 12, "team-b", "team-c")
    await rig.knock(t_dec + 20, "team-c", "team-b")
    rig.clock.advance(10 + 40)
    await rig.labeler.run_pass()
    assert await rig.labels_for(grabbed) == []
    assert await rig.labels_for(manual) == []


async def test_mode_transition_rows_never_labeled(maker):
    rig = _rig(maker)
    t_dec = T0 + 10
    dec = await rig.decision(
        t_dec, mode="clone", outcome="mode_transition", rule="clone_grab"
    )
    await rig.fight(t_dec + 5, "fight-b", ["team-b", "team-c"])
    await rig.knock(t_dec + 10, "team-b", "team-c")
    await rig.knock(t_dec + 20, "team-c", "team-b")
    rig.clock.advance(10 + 90)
    summary = await rig.labeler.run_pass()
    assert await rig.labels_for(dec) == []
    assert summary["skipped_mode_transition"] == 1
    assert rig.labeler.status()["stats"]["skipped_mode_transition"] == 1


# ── coverage gap ───────────────────────────────────────────────────


async def test_decision_before_coverage_is_skipped_and_counted(maker):
    rig = _rig(maker)
    # Decision predates labeler start (coverage begins at T0).
    dec = await rig.decision(T0 - 500)
    rig.clock.advance(120)
    summary = await rig.labeler.run_pass()
    assert await rig.labels_for(dec) == []
    assert summary["skipped_no_coverage"] == 1
    status = rig.labeler.status()
    assert status["stats"]["skipped_no_coverage"] == 1
    assert status["labeler_version"] == LABELER_VERSION
    assert status["running"] is True
