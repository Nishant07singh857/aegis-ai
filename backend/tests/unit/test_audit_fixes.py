# backend/tests/unit/test_audit_fixes.py
"""Phase 12 audit-fix tests (W2).

Covers the six audit fixes without requiring live Postgres/Redis:
- event persistence writes an event and it can be read back (SQLite)
- record_outcome is wired to fight_ended events and changes accuracy
- the processing queue is bounded with a drop policy
- teams reads are cached (second call is a cache hit), writes invalidate
- rate limiting degrades honestly when Redis is absent

Redis-dependent assertions are skipped when no Redis is reachable.
"""

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.base_agent import AgentConfig
from app.agents.prediction_agents.fight_predictor import FightPredictor
from app.core.event_bus import BaseEvent, EventBus, EventPriority, EventRecord
from app.core.rate_limit_middleware import check_rate_limit


# ── 1. event persistence ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_persist_event_writes_and_reads_back(tmp_path):
    """_persist_event writes a row to event_store; it can be read back."""
    from app.db.models.event_store import EventStore

    db_file = tmp_path / "event_store_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(EventStore.__table__.create)
    TestSession = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    bus = EventBus()
    bus._session_factory = TestSession  # inject test DB instead of app DB

    event = BaseEvent(
        event_type="kill",
        priority=EventPriority.CRITICAL,
        match_id="match_001",
        data={"killer": "p1", "victim": "p2"},
    )
    await bus._persist_event(event)

    async with TestSession() as session:
        row = (await session.execute(select(EventStore))).scalars().first()

    assert row is not None
    assert row.event_id == event.event_id
    assert row.event_type == "kill"
    assert row.priority == EventPriority.CRITICAL.value
    assert row.match_id == "match_001"
    assert row.data == {"killer": "p1", "victim": "p2"}

    await engine.dispose()


@pytest.mark.asyncio
async def test_persist_event_never_breaks_event_flow():
    """A dead DB must not raise out of _persist_event (failure-safe)."""
    bus = EventBus()

    async def _boom():
        raise ConnectionError("db is gone")

    class _BrokenFactory:
        def __call__(self):
            raise ConnectionError("db is gone")

    bus._session_factory = _BrokenFactory()
    # Must not raise.
    await bus._persist_event(BaseEvent(event_type="kill", data={}))


# ── 2. record_outcome wired to fight_ended ────────────────────────────

def _teams():
    team_a = {
        "team_id": "team_a",
        "team_name": "Alpha",
        "players": [{"state": "alive"} for _ in range(4)],
    }
    team_b = {
        "team_id": "team_b",
        "team_name": "Beta",
        "players": [{"state": "alive"} for _ in range(4)],
    }
    return team_a, team_b


@pytest.mark.asyncio
async def test_record_outcome_changes_accuracy_via_fight_ended():
    """fight_ended events close the accuracy loop: predicted vs actual."""
    bus = EventBus()
    agent = FightPredictor(
        config=AgentConfig(agent_id="test_fp", agent_type="fight_predictor"),
        event_bus=bus,
    )
    team_a, team_b = _teams()

    # Fight 1: predict, then end it with the predicted winner -> correct.
    pred = agent.predict_fight("fight_1", team_a, team_b, {})
    await agent.handle_event(
        EventRecord(
            event=BaseEvent(
                event_type="fight_ended",
                data={"fight_id": "fight_1", "winner_team_id": pred.predicted_winner},
            )
        )
    )
    stats = agent.get_accuracy()
    assert stats["total_predictions"] == 1
    assert stats["accuracy"] == 100.0

    # Fight 2: predict, then end it with a wrong winner -> incorrect.
    pred2 = agent.predict_fight("fight_2", team_a, team_b, {})
    wrong = "team_b" if pred2.predicted_winner != "team_b" else "team_a"
    await agent.handle_event(
        EventRecord(
            event=BaseEvent(
                event_type="fight_ended",
                data={"fight_id": "fight_2", "winner_team_id": wrong},
            )
        )
    )
    stats = agent.get_accuracy()
    assert stats["total_predictions"] == 2
    assert stats["accuracy"] == 50.0
    assert len(agent.prediction_history) == 2


@pytest.mark.asyncio
async def test_fight_ended_without_prediction_is_safe():
    """fight_ended for an unknown fight must not raise or corrupt stats."""
    bus = EventBus()
    agent = FightPredictor(
        config=AgentConfig(agent_id="test_fp2", agent_type="fight_predictor"),
        event_bus=bus,
    )
    await agent.handle_event(
        EventRecord(
            event=BaseEvent(
                event_type="fight_ended",
                data={"fight_id": "nope", "winner_team_id": "team_x"},
            )
        )
    )
    assert agent.get_accuracy()["total_predictions"] == 0


# ── 3. bounded queue ─────────────────────────────────────────────────

def _record(i: int) -> EventRecord:
    return EventRecord(event=BaseEvent(event_type=f"e{i}", data={}))


def test_processing_queue_bound_respected():
    bus = EventBus(max_processing_queue=5)
    assert bus._processing_queue.maxsize == 5

    # Fill with low-priority events.
    for i in range(5):
        assert bus._enqueue_for_processing(EventPriority.LOW.value, _record(i)) is True
    assert bus._processing_queue.qsize() == 5

    # A critical event evicts the lowest-priority item instead of blocking.
    critical = _record(99)
    assert bus._enqueue_for_processing(EventPriority.CRITICAL.value, critical) is True
    assert bus._processing_queue.qsize() == 5
    assert bus._stats["events_dropped"] == 1

    # Another low-priority event is dropped (nothing less important to evict).
    assert bus._enqueue_for_processing(EventPriority.LOW.value, _record(100)) is False
    assert bus._processing_queue.qsize() == 5
    assert bus._stats["events_dropped"] == 2

    # The critical event survived the eviction.
    remaining_ids = set()
    while not bus._processing_queue.empty():
        _prio, _seq, rec = bus._processing_queue.get_nowait()
        remaining_ids.add(rec.event.event_type)
    assert "e99" in remaining_ids


def test_equal_priority_enqueue_never_crashes():
    """Same-priority tuples must stay orderable (tiebreaker counter)."""
    bus = EventBus(max_processing_queue=10)
    for i in range(10):
        bus._enqueue_for_processing(EventPriority.MEDIUM.value, _record(i))
    assert bus._processing_queue.qsize() == 10


# ── 4. teams cache ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_teams_list_cache_hit_on_second_call():
    from app.api.v1.teams import service as teams_service
    from app.api.v1.teams.service import TeamService

    teams_service._local_cache.clear()
    svc = TeamService(db=None)

    calls = 0
    orig = svc._fetch_list_teams

    async def counting(skip: int = 0, limit: int = 100):
        nonlocal calls
        calls += 1
        return await orig(skip=skip, limit=limit)

    svc._fetch_list_teams = counting

    first = await svc.list_teams()
    second = await svc.list_teams()
    assert calls == 1, "second call must be served from cache"
    assert [t.id for t in first] == [t.id for t in second]

    # Writes invalidate: the next read re-fetches.
    teams_service._invalidate_teams_cache()
    await svc.list_teams()
    assert calls == 2


@pytest.mark.asyncio
async def test_teams_get_cache_hit_on_second_call():
    from app.api.v1.teams import service as teams_service
    from app.api.v1.teams.service import TeamService

    teams_service._local_cache.clear()
    svc = TeamService(db=None)

    calls = 0
    orig = svc._fetch_team

    async def counting(team_id: str):
        nonlocal calls
        calls += 1
        return await orig(team_id)

    svc._fetch_team = counting

    first = await svc.get_team("does-not-exist")
    second = await svc.get_team("does-not-exist")
    assert calls == 1
    assert first == second  # both None, served from cache


# ── 5. rate limiter degrades without Redis ───────────────────────────

@pytest.mark.asyncio
async def test_rate_limit_degrades_to_single_node_without_redis():
    """No Redis reachable -> in-memory fallback still enforces the limit."""
    key = f"pytest-degraded-{asyncio.get_event_loop().time()}"
    assert await check_rate_limit(key, max_requests=2, window_seconds=60) is True
    assert await check_rate_limit(key, max_requests=2, window_seconds=60) is True
    assert await check_rate_limit(key, max_requests=2, window_seconds=60) is False


@pytest.mark.asyncio
async def test_redis_cache_roundtrip_or_skip():
    """If a Redis is actually reachable, cache_manager round-trips."""
    from app.core.cache import cache_manager

    try:
        await asyncio.wait_for(cache_manager.connect(), timeout=2.0)
    except Exception:
        pytest.skip("no Redis reachable; skipping Redis-backed assertion")
    try:
        assert await cache_manager.set("pytest:audit", {"ok": True}, ttl=10) is True
        assert await cache_manager.get("pytest:audit") == {"ok": True}
    finally:
        await cache_manager.delete("pytest:audit")
        await cache_manager.disconnect()
