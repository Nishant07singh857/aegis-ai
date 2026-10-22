# backend/tests/unit/test_director_learning.py
"""Phase 27 (W1) — Director Learning foundation proof tests.

Covers the W1 contracts later waves build on:

* sink no-op default: with nothing registered, ``_log_decision`` and
  the DirectorService suggestion flows behave exactly as before;
* the journal persists a decision entry end-to-end (sqlite, injectable
  session factory — the repo's DB-less test pattern);
* fate capture for approve + reject + expire through the REAL
  DirectorService methods;
* the ``modified`` fate: reject a suggestion, then put a DIFFERENT
  target on air inside the 30 s watch (via approving another
  suggestion, and via manual-mode direct write after a mode flip);
* both shadow divergence kinds from scripted shadow entries + human
  writes, plus the agreement controls (no divergence);
* writer failure isolation: a session factory that raises never
  breaks the decision flow and increments the drop counter;
* queries helpers (recent / by suggestion / window / unlabeled /
  fate / stats).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.services import director_service as director_module
from app.services.director_learning import queries
from app.services.director_learning.journal import (
    DecisionJournal,
    clear_sinks,
    notify_decision,
    register_decision_sink,
    register_fate_sink,
)
from app.services.director_learning.models import (
    DirectorDecision,
    DirectorDecisionLabel,
    DirectorShadowDivergence,
    DirectorSuggestionFate,
)
from app.services.director_service import DirectorService
from app.services.live_director import LiveDirector
from app.services.vision_intelligence.attribution import StaticSlotResolver

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


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
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


@pytest.fixture(autouse=True)
def _clean_sinks():
    clear_sinks()
    yield
    clear_sinks()
    # DirectorService write sinks are per-instance registrations via
    # bound methods in these tests; the module registry holds the
    # journal bound methods — clear them too.
    director_module._output_write_sinks.clear()


def _wire(journal: DecisionJournal, ds: DirectorService) -> None:
    register_decision_sink(journal.on_decision)
    register_fate_sink(journal.on_fate)
    director_module.register_output_write_sink(journal.on_write)


def _decision_entry(**overrides):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trigger": "fight.detected",
        "mode": "full_auto",
        "phase": "mid_game",
        "candidates": [
            {
                "team_id": "team-b",
                "slot": 2,
                "score": 85.0,
                "base_score": 85.0,
                "breakdown": {
                    "precombat": 0.0,
                    "active_combat": 85.0,
                    "zone_pressure": 0.0,
                    "clutch": 0.0,
                    "priority": 0.0,
                },
                "storyline": None,
                "engagement_state": "ENGAGED",
                "fight_id": "fight-b",
                "is_incumbent": False,
            }
        ],
        "incumbent": {
            "team_id": "team-a",
            "slot": 1,
            "score": 50.0,
            "breakdown": {"staleness_penalty": 0.0},
        },
        "challenger": {
            "team_id": "team-b",
            "slot": 2,
            "score": 85.0,
            "fight_id": "fight-b",
            "storyline": None,
            "engagement_state": "ENGAGED",
        },
        "outcome": "switch",
        "rule": "margin_default_15",
        "margin_required": 15.0,
        "margin_actual": 35.0,
        "reason": "test decision",
    }
    entry.update(overrides)
    return entry


def _shadow_entry(clock: FakeClock, **overrides):
    """A manual-mode shadow entry timestamped at the fake clock."""
    ts = datetime.fromtimestamp(clock(), tz=timezone.utc).isoformat()
    entry = _decision_entry(
        timestamp=ts,
        mode="manual",
        outcome="hold",
        rule="manual_mode_no_write",
        margin_required=None,
        margin_actual=10.0,
        reason="MANUAL mode: the live director never writes.",
    )
    entry.update(overrides)
    return entry


async def _rows(maker, model):
    async with maker() as session:
        return list((await session.execute(select(model))).scalars().all())


# ── sink no-op default ─────────────────────────────────────────────


async def test_sink_noop_default_decision_and_fates():
    # No sinks registered (autouse fixture cleared them): forwarding
    # is a silent no-op and raises nothing.
    notify_decision(_decision_entry())

    # LiveDirector._log_decision behaves exactly as before.
    from unittest.mock import AsyncMock

    from app.core.event_bus import EventBus

    bus = EventBus()
    bus._redis = AsyncMock()
    bus._running = True
    ds = DirectorService()
    live = LiveDirector(
        bus=bus,
        director_service=ds,
        slot_resolver=StaticSlotResolver({}),
        clock=FakeClock(),
    )
    entry = _decision_entry()
    returned = live._log_decision(dict(entry))
    assert returned["outcome"] == "switch"
    assert live.decision_log()[-1]["outcome"] == "switch"
    assert live.status()["stats"]["decisions"] == 1

    # DirectorService suggestion flow unchanged with no journal.
    await ds.set_mode("semi_auto")
    suggestion = await ds.propose_suggestion(
        suggestion_type="scene",
        payload={"scene": "standings"},
        reason="test",
        confidence=0.8,
        created_by="test",
    )
    assert suggestion is not None
    approved = await ds.approve_suggestion(suggestion["id"], updated_by="op")
    assert approved["status"] == "applied"
    assert ds.snapshot()["scene"] == "standings"


async def test_sink_noop_writes_without_journal():
    ds = DirectorService()
    await ds.set_mode("manual")
    state = await ds.set_camera(4, updated_by="op")
    assert state["active_camera"] == 4
    state = await ds.set_scene("standings", updated_by="op")
    assert state["scene"] == "standings"
    # system write path in full_auto also unaffected.
    await ds.set_mode("full_auto")
    result = await ds.system_publish(
        scene="main", active_camera=2, updated_by="live_director"
    )
    assert result["changed"] is True
    assert ds.snapshot()["active_camera"] == 2


# ── journal persistence end-to-end ─────────────────────────────────


async def test_journal_persists_decision_end_to_end(maker):
    ds = DirectorService()
    journal = DecisionJournal(session_factory=maker, director_service=ds)
    _wire(journal, ds)

    entry = _decision_entry(suggestion_id="sug-1")
    decision_id = journal.on_decision(entry)
    assert decision_id
    assert journal.decision_id_for_suggestion("sug-1") == decision_id
    await journal.drain()

    rows = await _rows(maker, DirectorDecision)
    assert len(rows) == 1
    row = rows[0]
    assert row.id == decision_id
    assert row.mode == "full_auto"
    assert row.phase == "mid_game"
    assert row.outcome == "switch"
    assert row.rule == "margin_default_15"
    assert row.margin_required == 15.0
    assert row.margin_actual == 35.0
    assert row.suggestion_id == "sug-1"
    assert row.challenger["team_id"] == "team-b"
    assert row.incumbent["slot"] == 1
    assert row.candidates[0]["breakdown"]["active_combat"] == 85.0
    assert journal.stats["decisions_persisted"] == 1
    assert journal.stats["decisions_dropped"] == 0

    # queries contract
    recent = await queries.recent_decisions(10, session_factory=maker)
    assert [r["id"] for r in recent] == [decision_id]
    by_sid = await queries.decision_by_suggestion_id("sug-1", session_factory=maker)
    assert by_sid["id"] == decision_id
    now = datetime.now(timezone.utc)
    window = await queries.decisions_in_window(
        now - timedelta(minutes=5), now + timedelta(minutes=5),
        session_factory=maker,
    )
    assert len(window) == 1


async def test_journal_via_log_decision_funnel(maker):
    """The REAL funnel: LiveDirector._log_decision -> sink -> DB."""
    from unittest.mock import AsyncMock

    from app.core.event_bus import EventBus

    ds = DirectorService()
    journal = DecisionJournal(session_factory=maker, director_service=ds)
    _wire(journal, ds)

    bus = EventBus()
    bus._redis = AsyncMock()
    bus._running = True
    live = LiveDirector(
        bus=bus,
        director_service=ds,
        slot_resolver=StaticSlotResolver({}),
        clock=FakeClock(),
    )
    live._log_decision(_decision_entry())
    await journal.drain()
    rows = await _rows(maker, DirectorDecision)
    assert len(rows) == 1
    assert rows[0].outcome == "switch"


# ── fate capture: approve / reject / expire ────────────────────────


async def _semi_auto_with_suggestion(ds: DirectorService, scene="standings"):
    await ds.set_mode("semi_auto")
    return await ds.propose_suggestion(
        suggestion_type="scene",
        payload={"scene": scene},
        reason="test suggestion",
        confidence=0.8,
        created_by="live_director",
    )


async def test_fate_approve_captured(maker):
    ds = DirectorService()
    journal = DecisionJournal(session_factory=maker, director_service=ds)
    _wire(journal, ds)

    suggestion = await _semi_auto_with_suggestion(ds)
    await ds.approve_suggestion(suggestion["id"], updated_by="operator-1")
    await journal.drain()

    rows = await _rows(maker, DirectorSuggestionFate)
    assert len(rows) == 1
    assert rows[0].suggestion_id == suggestion["id"]
    assert rows[0].fate == "approved"
    assert rows[0].decided_by == "operator-1"
    assert rows[0].chosen_alternative is None
    fate = await queries.fate_for_suggestion(
        suggestion["id"], session_factory=maker
    )
    assert fate["fate"] == "approved"


async def test_fate_reject_captured_and_linked_to_decision(maker):
    ds = DirectorService()
    journal = DecisionJournal(session_factory=maker, director_service=ds)
    _wire(journal, ds)

    suggestion = await _semi_auto_with_suggestion(ds)
    # The journal saw the decision that produced this suggestion.
    decision_id = journal.on_decision(
        _decision_entry(outcome="suggestion", suggestion_id=suggestion["id"])
    )
    await ds.reject_suggestion(suggestion["id"], updated_by="operator-2")
    await journal.drain()

    rows = await _rows(maker, DirectorSuggestionFate)
    assert len(rows) == 1
    assert rows[0].fate == "rejected"
    assert rows[0].decided_by == "operator-2"
    assert rows[0].decision_id == decision_id


async def test_fate_expire_captured(maker):
    ds = DirectorService()
    journal = DecisionJournal(session_factory=maker, director_service=ds)
    _wire(journal, ds)

    suggestion = await _semi_auto_with_suggestion(ds)
    # Force the TTL into the past, then trigger the lazy pruner.
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    ds._suggestions[suggestion["id"]]["expires_at"] = past
    assert ds.list_suggestions() == []
    await journal.drain()

    rows = await _rows(maker, DirectorSuggestionFate)
    assert len(rows) == 1
    assert rows[0].fate == "expired"
    assert rows[0].decided_by is None


# ── modified fate ──────────────────────────────────────────────────


async def test_modified_fate_via_approving_different_suggestion(maker):
    ds = DirectorService()
    journal = DecisionJournal(session_factory=maker, director_service=ds)
    _wire(journal, ds)

    s1 = await _semi_auto_with_suggestion(ds, scene="standings")
    await ds.reject_suggestion(s1["id"], updated_by="op")
    # Human instead approves a DIFFERENT pending suggestion.
    s2 = await ds.propose_suggestion(
        suggestion_type="scene",
        payload={"scene": "killfeed_focus"},
        reason="different target",
        confidence=0.7,
        created_by="phase_tracker",
    )
    assert s2 is not None
    await ds.approve_suggestion(s2["id"], updated_by="op")
    await journal.drain()

    rows = await _rows(maker, DirectorSuggestionFate)
    by_sid = {}
    for row in rows:
        by_sid.setdefault(row.suggestion_id, []).append(row)
    assert [r.fate for r in by_sid[s1["id"]]] == ["rejected", "modified"]
    modified = by_sid[s1["id"]][1]
    assert modified.chosen_alternative["scene"] == "killfeed_focus"
    assert modified.chosen_alternative["via"] == "approve_suggestion"
    assert modified.chosen_alternative["via_suggestion_id"] == s2["id"]
    assert modified.decided_by == "op"
    assert journal.stats["modified_fates"] == 1
    # fate_for_suggestion returns the LATEST row (modified supersedes).
    fate = await queries.fate_for_suggestion(s1["id"], session_factory=maker)
    assert fate["fate"] == "modified"


async def test_modified_fate_via_manual_direct_write(maker):
    ds = DirectorService()
    journal = DecisionJournal(session_factory=maker, director_service=ds)
    _wire(journal, ds)

    s1 = await _semi_auto_with_suggestion(ds, scene="standings")
    await ds.reject_suggestion(s1["id"], updated_by="op")
    # Human flips to MANUAL and writes a different target directly.
    await ds.set_mode("manual", updated_by="op")
    await ds.set_scene("commentary", updated_by="op")
    await journal.drain()

    rows = [
        r for r in await _rows(maker, DirectorSuggestionFate)
        if r.suggestion_id == s1["id"]
    ]
    assert [r.fate for r in rows] == ["rejected", "modified"]
    assert rows[1].chosen_alternative["scene"] == "commentary"
    assert rows[1].chosen_alternative["via"] == "set_scene"


async def test_no_modified_when_human_applies_proposed_target(maker):
    ds = DirectorService()
    journal = DecisionJournal(session_factory=maker, director_service=ds)
    _wire(journal, ds)

    s1 = await _semi_auto_with_suggestion(ds, scene="standings")
    await ds.reject_suggestion(s1["id"], updated_by="op")
    # Human changes their mind and puts the SAME target on air manually.
    await ds.set_mode("manual", updated_by="op")
    await ds.set_scene("standings", updated_by="op")
    await journal.drain()

    rows = [
        r for r in await _rows(maker, DirectorSuggestionFate)
        if r.suggestion_id == s1["id"]
    ]
    assert [r.fate for r in rows] == ["rejected"]


# ── shadow divergences ─────────────────────────────────────────────


async def test_human_switched_ai_held_divergence(maker):
    clock = FakeClock()
    ds = DirectorService()
    journal = DecisionJournal(
        session_factory=maker, director_service=ds, clock=clock
    )
    _wire(journal, ds)

    # Shadow: AI challenger is slot 2 (team-b), margin 10 < 15 -> AI
    # would NOT have switched anywhere.
    journal.on_decision(_shadow_entry(clock, margin_actual=10.0))
    # Human cuts to slot 3 instead.
    await ds.set_mode("manual")
    await ds.set_camera(3, updated_by="op")
    await journal.drain()

    rows = await _rows(maker, DirectorShadowDivergence)
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "human_switched_ai_held"
    assert row.human_action["active_camera"] == 3
    assert row.human_action["source"] == "set_camera"
    assert row.ai_candidate["team_id"] == "team-b"
    assert row.ai_candidate["slot"] == 2
    assert row.shadow_decision_id is not None
    assert row.detail["would_switch"] is False
    assert row.detail["margin_required_recomputed"] == 15.0


async def test_no_divergence_when_human_follows_winning_shadow(maker):
    clock = FakeClock()
    ds = DirectorService()
    journal = DecisionJournal(
        session_factory=maker, director_service=ds, clock=clock
    )
    _wire(journal, ds)

    # Shadow challenger slot 2 with margin 35 >= 15: AI WOULD cut to 2.
    journal.on_decision(_shadow_entry(clock, margin_actual=35.0))
    await ds.set_mode("manual")
    await ds.set_camera(2, updated_by="op")  # human does exactly that
    await journal.drain()
    assert await _rows(maker, DirectorShadowDivergence) == []


async def test_system_write_is_not_a_human_divergence(maker):
    clock = FakeClock()
    ds = DirectorService()
    journal = DecisionJournal(
        session_factory=maker, director_service=ds, clock=clock
    )
    _wire(journal, ds)

    journal.on_decision(_shadow_entry(clock, margin_actual=10.0))
    # AI's own full_auto write (system_publish) must never be read as
    # a human switch. (Phase 28: fresh default is semi_auto, where
    # system_publish is a no-op — pin full_auto explicitly.)
    await ds.set_mode("full_auto")
    result = await ds.system_publish(
        scene="main", active_camera=3, updated_by="live_director"
    )
    assert result["changed"] is True
    await journal.drain()
    assert journal.stats["human_writes"] == 0
    assert await _rows(maker, DirectorShadowDivergence) == []


async def test_ai_wanted_human_stayed_divergence(maker):
    clock = FakeClock()
    ds = DirectorService()
    journal = DecisionJournal(
        session_factory=maker, director_service=ds, clock=clock
    )
    _wire(journal, ds)

    # Shadow challenger would have won under full_auto (35 >= 15).
    journal.on_decision(_shadow_entry(clock, margin_actual=35.0))
    # Window still open: no divergence yet.
    assert await journal.reconcile_now() == []
    # 16 s pass with NO human switch.
    clock.advance(16)
    divergences = await journal.reconcile_now()
    assert len(divergences) == 1
    assert divergences[0]["kind"] == "ai_wanted_human_stayed"
    await journal.drain()

    rows = await _rows(maker, DirectorShadowDivergence)
    assert len(rows) == 1
    assert rows[0].ai_candidate["slot"] == 2
    assert rows[0].human_action is None
    assert rows[0].detail["window_s"] == 15.0
    # Deduped: a second pass raises nothing new.
    assert await journal.reconcile_now() == []


async def test_ai_wanted_suppressed_when_human_switches_in_window(maker):
    clock = FakeClock()
    ds = DirectorService()
    journal = DecisionJournal(
        session_factory=maker, director_service=ds, clock=clock
    )
    _wire(journal, ds)

    journal.on_decision(_shadow_entry(clock, margin_actual=35.0))
    await ds.set_mode("manual")
    clock.advance(5)  # human acts 5 s later (inside the 15 s window)
    await ds.set_camera(4, updated_by="op")
    clock.advance(20)
    assert await journal.reconcile_now() == []
    await journal.drain()
    kinds = [
        r.kind for r in await _rows(maker, DirectorShadowDivergence)
    ]
    # The in-window switch is captured by the human_switched path
    # (slot 4 != AI's slot 2), never double-counted as ai_wanted.
    assert kinds == ["human_switched_ai_held"]


async def test_ai_wanted_clutch_and_stale_margins_recomputed(maker):
    clock = FakeClock()
    ds = DirectorService()
    journal = DecisionJournal(
        session_factory=maker, director_service=ds, clock=clock
    )
    _wire(journal, ds)

    # Clutch challenger: breakdown clutch=25 -> required margin is 5,
    # so margin_actual 7 WOULD have won under full_auto.
    entry = _shadow_entry(clock, margin_actual=7.0)
    entry["candidates"][0]["breakdown"]["clutch"] = 25.0
    journal.on_decision(entry)
    clock.advance(16)
    divergences = await journal.reconcile_now()
    assert len(divergences) == 1
    assert divergences[0]["detail"]["margin_required_recomputed"] == 5.0
    assert divergences[0]["detail"]["margin_rule_recomputed"] == "margin_clutch_5"


# ── failure isolation ──────────────────────────────────────────────


def _raising_factory():
    def factory():
        raise RuntimeError("db is down")

    return factory


async def test_writer_failure_isolation():
    ds = DirectorService()
    journal = DecisionJournal(
        session_factory=_raising_factory(), director_service=ds
    )
    _wire(journal, ds)

    # Decision flow unaffected; drop counter increments.
    decision_id = journal.on_decision(_decision_entry())
    assert decision_id  # in-memory ring still works
    await journal.drain()
    assert journal.stats["decisions_persisted"] == 0
    assert journal.stats["decisions_dropped"] == 1

    # Fate flow unaffected either.
    suggestion = await _semi_auto_with_suggestion(ds)
    await ds.reject_suggestion(suggestion["id"], updated_by="op")
    await journal.drain()
    assert journal.stats["fates_dropped"] == 1

    # And the LiveDirector funnel still returns its entry.
    notify_decision(_decision_entry())


async def test_raising_sink_never_breaks_notify():
    def bad_sink(entry):
        raise RuntimeError("sink exploded")

    register_decision_sink(bad_sink)
    notify_decision(_decision_entry())  # must not raise


# ── queries: unlabeled + stats ─────────────────────────────────────


async def test_unlabeled_decisions_and_journal_stats(maker):
    ds = DirectorService()
    journal = DecisionJournal(session_factory=maker, director_service=ds)
    _wire(journal, ds)

    id1 = journal.on_decision(_decision_entry())
    id2 = journal.on_decision(
        _decision_entry(outcome="hold", mode="manual", rule="manual_mode_no_write")
    )
    await journal.drain()

    now = datetime.now(timezone.utc)
    unlabeled = await queries.unlabeled_decisions(now, session_factory=maker)
    assert {r["id"] for r in unlabeled} == {id1, id2}

    # Label one (as W2 will) -> it leaves the unlabeled queue.
    async with maker() as session:
        session.add(
            DirectorDecisionLabel(
                decision_id=id1,
                label="good_switch",
                confidence=0.9,
                confidence_band="high",
                evidence={"fight_id": "fight-b"},
                labeler_version="w2-test",
                labeled_at=now,
            )
        )
        await session.commit()
    unlabeled = await queries.unlabeled_decisions(
        now + timedelta(seconds=1), session_factory=maker
    )
    assert [r["id"] for r in unlabeled] == [id2]

    stats = await queries.journal_stats(session_factory=maker)
    assert stats["decisions_total"] == 2
    assert stats["decisions_by_mode"] == {"full_auto": 1, "manual": 1}
    assert stats["decisions_by_outcome"] == {"switch": 1, "hold": 1}
    assert stats["labels_total"] == 1
    assert stats["fates_by_fate"] == {}
