# backend/tests/unit/test_phase27_w5_fixes.py
"""Phase 27 — W5 gate fix regressions (coordinator-applied).

D1: rolling back a NON-head applied adjustment must be refused —
    a newer applied row for the same knob keeps the effective
    value, and the applied chain can never silently disagree
    with the value in effect. Unwinding newest-first still
    restores every value exactly.
D2: KnobProvider.refresh() must clamp DB values to the registry
    bounds (a value written outside the engine/API paths is
    never served out of bounds).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.services.director_learning.engine import LearningEngine
from app.services.director_learning.knobs import KnobProvider, clear_provider
from app.services.director_learning.models import DirectorLearningAdjustment

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
def _clean_provider():
    clear_provider()
    yield
    clear_provider()


def _applied(knob: str, before: float, after: float, ts: datetime):
    return DirectorLearningAdjustment(
        id=str(uuid.uuid4()),
        ts=ts,
        status="applied",
        knob=knob,
        bucket={},
        before_value=before,
        after_value=after,
        reason="seed",
        evidence={},
        learning_mode="suggest",
        applied_by="test",
    )


async def _seed(maker, *rows):
    async with maker() as session:
        for row in rows:
            session.add(row)
        await session.commit()


# ── D1: non-head rollback refused; newest-first unwind exact ──────


async def test_d1_non_head_rollback_refused_and_chain_consistent(maker):
    now = datetime.now(timezone.utc)
    a = _applied("margin_default", 15.0, 18.0, now - timedelta(seconds=20))
    b = _applied("margin_default", 18.0, 21.0, now - timedelta(seconds=10))
    await _seed(maker, a, b)

    provider = KnobProvider(session_factory=maker)
    engine = LearningEngine(session_factory=maker, provider=provider)
    await provider.refresh()
    assert provider.effective("margin_default", learned=True) == 21.0

    # Rolling back the OLDER row must be refused and change nothing.
    with pytest.raises(ValueError, match="not the latest"):
        await engine.rollback_adjustment(a.id, by="qa")
    await provider.refresh()
    assert provider.effective("margin_default", learned=True) == 21.0

    # Unwind the head: B rolls back exactly to 18.
    await engine.rollback_adjustment(b.id, by="qa")
    await provider.refresh()
    assert provider.effective("margin_default", learned=True) == 18.0

    # A is now behind B's compensating row: plain rollback still
    # refuses; explicit cascade unwinds A + the compensating row
    # and restores the exact original value.
    with pytest.raises(ValueError, match="not the latest"):
        await engine.rollback_adjustment(a.id, by="qa")
    comp = await engine.rollback_adjustment(a.id, by="qa", cascade=True)
    await provider.refresh()
    assert provider.effective("margin_default", learned=True) == 15.0
    # Compensating-row shape: applied, exact restore, parent = A,
    # and the cascaded rows cited in its evidence.
    assert comp["status"] == "applied"
    assert comp["after_value"] == 15.0
    assert comp["parent_id"] == a.id
    assert comp["evidence"]["rollback_of"] == a.id
    assert comp["evidence"]["cascaded"]  # non-empty: B / its comp row


async def test_d1_guard_is_per_knob(maker):
    """A newer applied row for a DIFFERENT knob must not block."""
    now = datetime.now(timezone.utc)
    a = _applied("margin_default", 15.0, 18.0, now - timedelta(seconds=20))
    other = _applied(
        "margin_clutch", 5.0, 9.0, now - timedelta(seconds=5)
    )
    await _seed(maker, a, other)

    provider = KnobProvider(session_factory=maker)
    engine = LearningEngine(session_factory=maker, provider=provider)
    await engine.rollback_adjustment(a.id, by="qa")
    await provider.refresh()
    assert provider.effective("margin_default", learned=True) == 15.0
    assert provider.effective("margin_clutch", learned=True) == 9.0


# ── D2: refresh() clamps out-of-bounds DB values ──────────────────


async def test_d2_refresh_clamps_above_max(maker):
    now = datetime.now(timezone.utc)
    await _seed(maker, _applied("margin_default", 15.0, 999.0, now))
    provider = KnobProvider(session_factory=maker)
    await provider.refresh()
    assert provider.effective("margin_default", learned=True) == 30.0


async def test_d2_refresh_clamps_below_min(maker):
    now = datetime.now(timezone.utc)
    await _seed(maker, _applied("margin_default", 15.0, -5.0, now))
    provider = KnobProvider(session_factory=maker)
    await provider.refresh()
    assert provider.effective("margin_default", learned=True) == 8.0
