# backend/tests/unit/test_phase27_w3_learning_clone.py
"""Phase 27 (W3) — learning engine + Director-Style (clone) mode.

Covers: knob registry + forbidden-floor guard, bounds clamping,
provider profiles (factory vs learned vs off), engine rules
R1–R5 with sample guards, suggest/auto/off modes, duplicate-pending
and sample-cooldown guards, apply/rollback exactness, byte-identical
factory behaviour, and clone semantics (learned profile drives only
clone, grab = manual, explicit + inactivity release, human-write
gate, transition journaling).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.event_bus import EventBus, EventPriority
from app.db.base import Base
from app.services import director_service as director_module
from app.services.director_learning import knobs as knobs_module
from app.services.director_learning.engine import LearningEngine
from app.services.director_learning.journal import (
    clear_sinks,
    register_decision_sink,
)
from app.services.director_learning.knobs import (
    KNOB_REGISTRY,
    ForbiddenKnobError,
    KnobProvider,
    KnobSpec,
    clamp_value,
    clear_provider,
    ensure_adjustable,
    get_provider,
    register_knob,
    register_provider,
)
from app.services.director_learning.models import (
    LEARNING_STATE_ID,
    DirectorDecision,
    DirectorDecisionLabel,
    DirectorLearningAdjustment,
    DirectorLearningState,
    DirectorSuggestionFate,
)
from app.services.director_service import DirectorModeError, DirectorService
from app.services.live_director import LiveDirector
from app.services.live_director.config import LiveDirectorConfig
from app.services.vision_intelligence.attribution import (
    StaticSlotResolver,
    TeamAttribution,
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

SLOTS = {
    1: TeamAttribution(slot_number=1, team_id="team-a", team_name="Team A"),
    2: TeamAttribution(slot_number=2, team_id="team-b", team_name="Team B"),
    3: TeamAttribution(slot_number=3, team_id="team-c", team_name="Team C"),
    4: TeamAttribution(slot_number=4, team_id="team-d", team_name="Team D"),
}

EXPECTED_KNOBS = {
    "margin_default": (15.0, 8.0, 30.0),
    "margin_fight_hold": (40.0, 25.0, 60.0),
    "margin_stale_incumbent": (10.0, 5.0, 25.0),
    "margin_clutch": (5.0, 2.0, 15.0),
    "storyline_rank1_uplift": (0.10, 0.0, 0.20),
    "storyline_rank2_uplift": (0.06, 0.0, 0.15),
    "storyline_match_point_uplift": (0.10, 0.0, 0.20),
    "storyline_hot_streak_uplift": (0.08, 0.0, 0.16),
    "engagement_precombat_points": (15.0, 8.0, 25.0),
    "precombat_confidence_enter": (0.50, 0.35, 0.70),
    "drop_clash_watch_points": (10.0, 5.0, 20.0),
}


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
def _clean_globals():
    clear_provider()
    clear_sinks()
    director_module._output_write_sinks.clear()
    yield
    clear_provider()
    clear_sinks()
    director_module._output_write_sinks.clear()


# ── seeding helpers ────────────────────────────────────────────────


def _base_decision(**overrides):
    decision = {
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc),
        "mode": "semi_auto",
        "phase": "mid_game",
        "trigger": "fight.detected",
        "outcome": "suggestion",
        "rule": "margin_default_15",
        "reason": "seed",
        "margin_required": 15.0,
        "margin_actual": 20.0,
        "incumbent": {
            "team_id": "team-a", "slot": 1, "score": 50.0,
            "breakdown": {"staleness_penalty": 0.0},
        },
        "challenger": {
            "team_id": "team-b", "slot": 2, "score": 70.0,
            "fight_id": "fight-b", "engagement_state": "ACTIVE_COMBAT",
        },
        "candidates": [
            {
                "team_id": "team-b", "slot": 2, "score": 70.0,
                "breakdown": {
                    "precombat": 0.0, "active_combat": 70.0,
                    "storyline_bonus": 0.0,
                },
                "engagement_state": "ACTIVE_COMBAT",
            }
        ],
        "suggestion_id": None,
    }
    decision.update(overrides)
    return decision


async def _seed(maker, *rows):
    async with maker() as session:
        for row in rows:
            session.add(row)
        await session.commit()


async def _seed_r1(maker, n_decided: int, n_approved: int, prefix="r1"):
    rows = []
    for i in range(n_decided):
        sid = f"{prefix}-sug-{i}"
        rows.append(
            DirectorDecision(**_base_decision(suggestion_id=sid))
        )
        rows.append(
            DirectorSuggestionFate(
                id=str(uuid.uuid4()), suggestion_id=sid,
                fate="approved" if i < n_approved else "rejected",
                decided_by="op",
                ts=datetime.now(timezone.utc) + timedelta(seconds=i),
            )
        )
    await _seed(maker, *rows)


async def _set_mode_row(maker, mode: str):
    await _seed(
        maker,
        DirectorLearningState(
            id=LEARNING_STATE_ID, learning_mode=mode, updated_by="test"
        ),
    )


async def _adjustments(maker, knob=None):
    async with maker() as session:
        query = select(DirectorLearningAdjustment)
        if knob:
            query = query.where(DirectorLearningAdjustment.knob == knob)
        return list((await session.execute(query)).scalars().all())


# ── registry + provider ────────────────────────────────────────────


def test_registry_exact_knobs_and_defaults():
    assert set(KNOB_REGISTRY) == set(EXPECTED_KNOBS)
    for name, (default, lo, hi) in EXPECTED_KNOBS.items():
        spec = KNOB_REGISTRY[name]
        assert (spec.default, spec.min_value, spec.max_value) == (default, lo, hi)
        assert spec.step > 0 and spec.unit and spec.why


def test_forbidden_floors_raise():
    for name in (
        "storyline_max_multiplier", "SWITCH_COOLDOWN_S",
        "MAX_SWITCHES_PER_MINUTE", "min_fight_hold_s",
        "PRECOMBAT_CAP", "HEAT_CAP", "context_beat_duration_s",
        "stale_after_fight_s", "final_zone_hard_lock", "some_new_cap",
    ):
        with pytest.raises(ForbiddenKnobError):
            ensure_adjustable(name)
        with pytest.raises(ForbiddenKnobError):
            register_knob(
                KnobSpec(name, 1.0, 0.0, 2.0, 1.0, "x", "attack")
            )
    provider = KnobProvider()
    with pytest.raises(ForbiddenKnobError):
        provider.effective("storyline_max_multiplier")
    with pytest.raises(KeyError):
        provider.effective("not_a_knob")


def test_bounds_clamping():
    assert clamp_value("margin_default", 100.0) == 30.0
    assert clamp_value("margin_default", 0.0) == 8.0
    assert clamp_value("storyline_rank1_uplift", 0.5) == 0.20
    assert clamp_value("precombat_confidence_enter", 0.1) == 0.35


async def test_provider_profiles_and_off_dormancy(maker):
    await _seed(
        maker,
        DirectorLearningAdjustment(
            id=str(uuid.uuid4()), ts=datetime.now(timezone.utc),
            status="applied", knob="margin_default", bucket={},
            before_value=15.0, after_value=21.0, reason="seed",
            evidence={}, learning_mode="suggest", applied_by="test",
        ),
    )
    await _set_mode_row(maker, "suggest")
    provider = KnobProvider(session_factory=maker)
    await provider.refresh()
    assert provider.effective("margin_default", learned=True) == 21.0
    assert provider.effective("margin_default", learned=False) == 15.0
    assert provider.has_profile() is True
    # Mode off: learned profile dormant, defaults everywhere.
    await LearningEngine(session_factory=maker, provider=provider).set_learning_mode(
        "off", updated_by="test"
    )
    assert provider.effective("margin_default", learned=True) == 15.0


def test_unregistered_provider_is_static_defaults():
    assert get_provider() is None
    assert knobs_module.effective("margin_default") == 15.0
    assert knobs_module.effective_all()["margin_clutch"] == 5.0


# ── engine rules ───────────────────────────────────────────────────


async def test_r1_fires_with_evidence(maker):
    await _set_mode_row(maker, "suggest")
    await _seed_r1(maker, 10, 3)
    provider = KnobProvider(session_factory=maker)
    register_provider(provider)
    engine = LearningEngine(session_factory=maker, provider=provider)
    created = await engine.run_analysis()
    assert len(created) == 1
    row = created[0]
    assert row["knob"] == "margin_default"
    assert row["status"] == "proposed"
    assert row["before_value"] == 15.0
    assert row["after_value"] == 18.0
    assert row["evidence"]["n"] == 10
    assert row["evidence"]["approval_rate"] == 0.3
    assert row["evidence"]["watermark"]["bucket_total"] == 10
    assert "0.30" in row["reason"]


async def test_r1_does_not_fire_at_9_samples_or_50pct(maker):
    await _set_mode_row(maker, "suggest")
    await _seed_r1(maker, 9, 2, prefix="nine")
    engine = LearningEngine(session_factory=maker)
    assert await engine.run_analysis() == []
    # Fresh evidence set: 10 decided, 5 approved (50%) -> no fire.
    maker2_rows = await _adjustments(maker)
    assert maker2_rows == []


async def test_r1_does_not_fire_at_50pct_approval(maker):
    await _set_mode_row(maker, "suggest")
    await _seed_r1(maker, 10, 5, prefix="half")
    engine = LearningEngine(session_factory=maker)
    assert await engine.run_analysis() == []


async def _seed_switches(maker, label, count_labelled, n_switches=10,
                         precombat=10.0, rule="margin_default_15"):
    rows = []
    for i in range(n_switches):
        did = str(uuid.uuid4())
        cand = [{
            "team_id": "team-b", "slot": 2, "score": 70.0,
            "breakdown": {"precombat": precombat, "storyline_bonus": 0.0},
            "engagement_state": "ACTIVE_COMBAT",
        }]
        rows.append(
            DirectorDecision(**_base_decision(
                id=did, outcome="switch", mode="full_auto",
                rule=rule, suggestion_id=None, candidates=cand,
            ))
        )
        if i < count_labelled:
            rows.append(
                DirectorDecisionLabel(
                    id=str(uuid.uuid4()), decision_id=did, label=label,
                    confidence=0.9, confidence_band="high", evidence={},
                    labeler_version="w2-test",
                    labeled_at=datetime.now(timezone.utc),
                )
            )
    await _seed(maker, *rows)


async def test_r2_precombat_branch(maker):
    await _set_mode_row(maker, "suggest")
    await _seed_switches(maker, "fizzle", 4)  # rate 0.4 > 0.35, precombat>0
    engine = LearningEngine(session_factory=maker)
    created = await engine.run_analysis()
    by_knob = {r["knob"]: r for r in created}
    assert by_knob["engagement_precombat_points"]["after_value"] == 13.0
    assert by_knob["engagement_precombat_points"]["evidence"]["rule"] == "R2"


async def test_r2_margin_branch_when_no_precombat(maker):
    await _set_mode_row(maker, "suggest")
    await _seed_switches(maker, "fizzle", 4, precombat=0.0)
    engine = LearningEngine(session_factory=maker)
    created = await engine.run_analysis()
    by_knob = {r["knob"]: r for r in created}
    assert by_knob["margin_default"]["after_value"] == 17.0


async def test_r3_premature_exit(maker):
    await _set_mode_row(maker, "suggest")
    await _seed_switches(maker, "premature_exit", 4)
    engine = LearningEngine(session_factory=maker)
    created = await engine.run_analysis()
    by_knob = {r["knob"]: r for r in created}
    assert by_knob["margin_fight_hold"]["after_value"] == 44.0
    assert by_knob["margin_fight_hold"]["before_value"] == 40.0


async def test_r4_missed_fights_on_stale(maker):
    await _set_mode_row(maker, "suggest")
    rows = []
    for _ in range(5):
        did = str(uuid.uuid4())
        rows.append(
            DirectorDecision(**_base_decision(
                id=did, outcome="switch", mode="full_auto",
                rule="margin_stale_10", suggestion_id=None,
            ))
        )
        rows.append(
            DirectorDecisionLabel(
                id=str(uuid.uuid4()), decision_id=did,
                label="missed_fight", confidence=0.8,
                confidence_band="medium", evidence={},
                labeler_version="w2-test",
                labeled_at=datetime.now(timezone.utc),
            )
        )
    await _seed(maker, *rows)
    engine = LearningEngine(session_factory=maker)
    created = await engine.run_analysis()
    by_knob = {r["knob"]: r for r in created}
    assert by_knob["margin_stale_incumbent"]["after_value"] == 8.0
    assert by_knob["margin_stale_incumbent"]["evidence"]["window"] == "all"


async def test_r5_storyline_rejections(maker):
    await _set_mode_row(maker, "suggest")
    rows = []
    for i in range(8):
        sid = f"r5-sug-{i}"
        cand = [{
            "team_id": "team-b", "slot": 2, "score": 70.0,
            "breakdown": {"precombat": 0.0, "storyline_bonus": 5.0},
            "engagement_state": "ACTIVE_COMBAT",
        }]
        rows.append(
            DirectorDecision(**_base_decision(
                suggestion_id=sid, candidates=cand
            ))
        )
        rows.append(
            DirectorSuggestionFate(
                id=str(uuid.uuid4()), suggestion_id=sid,
                fate="rejected" if i < 5 else "approved",
                decided_by="op",
                ts=datetime.now(timezone.utc) + timedelta(seconds=i),
            )
        )
    await _seed(maker, *rows)
    engine = LearningEngine(session_factory=maker)
    created = await engine.run_analysis()
    by_knob = {r["knob"]: r for r in created}
    assert by_knob["storyline_rank1_uplift"]["after_value"] == 0.08
    assert by_knob["storyline_rank1_uplift"]["evidence"]["n"] == 8


async def test_engine_empty_labels_table_is_fine(maker):
    await _set_mode_row(maker, "suggest")
    engine = LearningEngine(session_factory=maker)
    assert await engine.run_analysis() == []


# ── modes + guards + lifecycle ─────────────────────────────────────


async def test_suggest_auto_off_modes(maker):
    # suggest
    await _set_mode_row(maker, "suggest")
    await _seed_r1(maker, 10, 3, prefix="m-suggest")
    engine = LearningEngine(session_factory=maker)
    created = await engine.run_analysis()
    assert created[0]["status"] == "proposed"


async def test_auto_mode_applies_immediately(maker):
    await _set_mode_row(maker, "auto")
    await _seed_r1(maker, 10, 2, prefix="m-auto")
    provider = KnobProvider(session_factory=maker)
    register_provider(provider)
    engine = LearningEngine(session_factory=maker, provider=provider)
    created = await engine.run_analysis()
    assert created[0]["status"] == "applied"
    assert created[0]["applied_by"] == "learning_engine"
    assert provider.effective("margin_default", learned=True) == 18.0
    assert provider.effective("margin_default", learned=False) == 15.0


async def test_off_mode_creates_nothing(maker):
    await _set_mode_row(maker, "off")
    await _seed_r1(maker, 10, 0, prefix="m-off")
    engine = LearningEngine(session_factory=maker)
    assert await engine.run_analysis() == []
    assert await _adjustments(maker) == []


async def test_duplicate_pending_guard(maker):
    await _set_mode_row(maker, "suggest")
    await _seed_r1(maker, 10, 3, prefix="dup")
    engine = LearningEngine(session_factory=maker)
    assert len(await engine.run_analysis()) == 1
    assert await engine.run_analysis() == []


async def test_sample_cooldown_guard(maker):
    await _set_mode_row(maker, "auto")
    await _seed_r1(maker, 10, 3, prefix="cool")
    provider = KnobProvider(session_factory=maker)
    engine = LearningEngine(session_factory=maker, provider=provider)
    created = await engine.run_analysis()
    assert len(created) == 1  # applied, watermark 10
    # Same samples: cooldown blocks re-proposal.
    assert await engine.run_analysis() == []
    # 10 NEW decided samples in the same bucket: cooldown clears.
    await _seed_r1(maker, 10, 3, prefix="cool2")
    created2 = await engine.run_analysis()
    assert len(created2) == 1
    assert created2[0]["before_value"] == 18.0
    assert created2[0]["after_value"] == 21.0


async def test_at_bound_no_proposal(maker):
    await _set_mode_row(maker, "suggest")
    await _seed(
        maker,
        DirectorLearningAdjustment(
            id=str(uuid.uuid4()), ts=datetime.now(timezone.utc),
            status="applied", knob="margin_default", bucket={},
            before_value=27.0, after_value=30.0, reason="at bound",
            evidence={}, learning_mode="suggest", applied_by="test",
        ),
    )
    await _seed_r1(maker, 10, 2, prefix="bound")
    provider = KnobProvider(session_factory=maker)
    engine = LearningEngine(session_factory=maker, provider=provider)
    assert await engine.run_analysis() == []


async def test_apply_reject_rollback_exactness(maker):
    await _set_mode_row(maker, "suggest")
    await _seed_r1(maker, 10, 3, prefix="life")
    provider = KnobProvider(session_factory=maker)
    register_provider(provider)
    engine = LearningEngine(session_factory=maker, provider=provider)
    proposal = (await engine.run_analysis())[0]
    assert provider.effective("margin_default", learned=True) == 15.0
    applied = await engine.apply_adjustment(proposal["id"], applied_by="op")
    assert applied["status"] == "applied"
    assert provider.effective("margin_default", learned=True) == 18.0
    compensating = await engine.rollback_adjustment(proposal["id"], by="op")
    assert compensating["parent_id"] == proposal["id"]
    assert compensating["after_value"] == 15.0
    assert provider.effective("margin_default", learned=True) == 15.0
    rows = {r.id: r for r in await _adjustments(maker)}
    assert rows[proposal["id"]].status == "rolled_back"
    # Reject path on a fresh proposal.
    await _seed_r1(maker, 10, 3, prefix="life2")
    # Cooldown: 10 new samples arrived, so a new proposal may fire.
    created = await engine.run_analysis()
    assert created
    rejected = await engine.reject_adjustment(created[0]["id"], decided_by="op")
    assert rejected["status"] == "rejected"


async def test_mode_get_set_validation(maker):
    engine = LearningEngine(session_factory=maker)
    assert await engine.get_learning_mode() == "suggest"  # default, no row
    assert await engine.set_learning_mode("auto", updated_by="op") == "auto"
    assert await engine.get_learning_mode() == "auto"
    with pytest.raises(ValueError):
        await engine.set_learning_mode("turbo")


# ── LiveDirector rig ───────────────────────────────────────────────


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
    def __init__(self, phase="mid_game", config=None):
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
            config=config,
        )
        self.live.start()

    async def fight(self, fight_id, teams, intensity, **extra):
        await self.bus.publish(
            event_type="fight.detected",
            data={
                "fight_id": fight_id, "teams_involved": teams,
                "intensity": intensity, "fight_type": "mid_range",
                "location": {"x": 1.0, "y": 1.0}, "match_id": "m-1",
                **extra,
            },
            priority=EventPriority.HIGH, source="test", match_id="m-1",
        )


async def _applied_margin(maker, after, before=15.0):
    await _seed(
        maker,
        DirectorLearningAdjustment(
            id=str(uuid.uuid4()), ts=datetime.now(timezone.utc),
            status="applied", knob="margin_default", bucket={},
            before_value=before, after_value=after, reason="seed",
            evidence={}, learning_mode="suggest", applied_by="test",
        ),
    )


async def _scenario_decisions(setup):
    """Fixed scripted scenario; returns comparable decision tuples."""
    rig = Rig()
    if setup is not None:
        await setup(rig)
    from app.services.feed_adapter.schemas import (
        FeedEventType,
        NormalizedEvent,
    )

    knock = NormalizedEvent(
        event_type=FeedEventType.KNOCK, player="BPlayer", team="Team B",
        victim="CPlayer", victim_team="Team C", weapon="M416",
        match_id="m-1",
    )
    await rig.bus.publish(
        event_type="knock_event", data=knock.to_bus_payload(),
        priority=EventPriority.HIGH, source="test", match_id="m-1",
    )
    await rig.fight("fight-weak", ["team-b", "team-c"], 60.0)
    rig.clock.advance(5)
    await rig.fight("fight-strong", ["team-c", "team-d"], 90.0)
    return [
        (e["outcome"], e["rule"], e["margin_required"], e["margin_actual"])
        for e in rig.live.decision_log()
    ]


async def test_factory_byte_identical_three_ways(maker):
    baseline = await _scenario_decisions(None)

    async def _registered_empty(rig):
        provider = KnobProvider(session_factory=maker)
        await provider.refresh()
        register_provider(provider)

    with_registered = await _scenario_decisions(_registered_empty)
    assert with_registered == baseline

    async def _off_with_applied(rig):
        await _applied_margin(maker, 30.0)
        await _set_mode_row(maker, "off")
        provider = KnobProvider(session_factory=maker)
        await provider.refresh()
        register_provider(provider)
        assert provider.effective("margin_default", learned=True) == 15.0

    with_off = await _scenario_decisions(_off_with_applied)
    assert with_off == baseline


async def test_full_auto_ignores_applied_adjustments(maker):
    await _applied_margin(maker, 8.0)
    await _set_mode_row(maker, "suggest")
    provider = KnobProvider(session_factory=maker)
    await provider.refresh()
    register_provider(provider)
    rig = Rig()  # pinned to full_auto in Rig (Phase 28)
    await rig.fight("fight-b", ["team-b", "team-c"], 62.0)  # margin 12
    assert rig.director_service.snapshot()["active_camera"] == 1
    entry = rig.live.decision_log()[-1]
    assert entry["outcome"] == "rejected_challenger"
    assert entry["margin_required"] == 15.0


async def test_clone_fires_with_learned_margin_where_factory_holds(maker):
    await _applied_margin(maker, 8.0)
    await _set_mode_row(maker, "suggest")
    provider = KnobProvider(session_factory=maker)
    await provider.refresh()
    register_provider(provider)
    rig = Rig()
    await rig.director_service.set_mode("clone")
    await rig.fight("fight-b", ["team-b", "team-c"], 62.0)  # margin 12
    assert rig.director_service.snapshot()["active_camera"] == 2
    entry = rig.live.decision_log()[-1]
    assert entry["outcome"] == "switch"
    assert entry["margin_required"] == 8.0
    assert entry["mode"] == "clone"


async def test_clone_holds_where_full_auto_fires(maker):
    await _applied_margin(maker, 25.0)
    await _set_mode_row(maker, "suggest")
    provider = KnobProvider(session_factory=maker)
    await provider.refresh()
    register_provider(provider)
    rig = Rig()
    await rig.director_service.set_mode("clone")
    await rig.fight("fight-b", ["team-b", "team-c"], 67.0)  # margin 17
    assert rig.director_service.snapshot()["active_camera"] == 1
    entry = rig.live.decision_log()[-1]
    assert entry["outcome"] == "rejected_challenger"
    assert entry["margin_required"] == 25.0
    # Same evidence in full_auto (fresh rig): factory margin 15 fires.
    rig2 = Rig()
    await rig2.fight("fight-b", ["team-b", "team-c"], 67.0)
    assert rig2.director_service.snapshot()["active_camera"] == 2


async def test_clone_off_mode_falls_back_to_defaults(maker):
    await _applied_margin(maker, 8.0)
    await _set_mode_row(maker, "off")
    provider = KnobProvider(session_factory=maker)
    await provider.refresh()
    register_provider(provider)
    rig = Rig()
    await rig.director_service.set_mode("clone")
    await rig.fight("fight-b", ["team-b", "team-c"], 62.0)
    assert rig.director_service.snapshot()["active_camera"] == 1
    assert rig.live.decision_log()[-1]["margin_required"] == 15.0


async def test_apply_flips_clone_decision_and_rollback_restores(maker):
    await _set_mode_row(maker, "suggest")
    await _seed_r1(maker, 10, 3, prefix="flip")
    provider = KnobProvider(session_factory=maker)
    register_provider(provider)
    engine = LearningEngine(session_factory=maker, provider=provider)
    proposal = (await engine.run_analysis())[0]  # 15 -> 18, proposed only
    rig = Rig()
    await rig.director_service.set_mode("clone")
    # Proposed (not applied): margin 17 challenger still fires at 15.
    await rig.fight("fight-b", ["team-b", "team-c"], 67.0)
    assert rig.director_service.snapshot()["active_camera"] == 2
    # Apply 15->18 in a fresh rig: the same challenger now holds.
    await engine.apply_adjustment(proposal["id"], applied_by="op")
    rig2 = Rig()
    await rig2.director_service.set_mode("clone")
    await rig2.fight("fight-b", ["team-b", "team-c"], 67.0)
    assert rig2.director_service.snapshot()["active_camera"] == 1
    assert rig2.live.decision_log()[-1]["margin_required"] == 18.0
    # Rollback: exact prior value, decisions flip back.
    await engine.rollback_adjustment(proposal["id"], by="op")
    assert provider.effective("margin_default", learned=True) == 15.0
    rig3 = Rig()
    await rig3.director_service.set_mode("clone")
    await rig3.fight("fight-b", ["team-b", "team-c"], 67.0)
    assert rig3.director_service.snapshot()["active_camera"] == 2


# ── clone grab / release ───────────────────────────────────────────


async def test_clone_grab_manual_semantics_and_gate():
    rig = Rig()
    ds = rig.director_service
    await ds.set_mode("clone")
    # Ungrabbed clone: human writes are gated out.
    with pytest.raises(DirectorModeError):
        await ds.set_camera(3, updated_by="op")
    # Grab: instant, snapshot exposes it.
    state = await ds.grab_clone_control(updated_by="op")
    assert state["clone_grabbed"] is True
    assert state["clone_grabbed_by"] == "op"
    assert state["clone_grabbed_at"] is not None
    # Human writes now allowed.
    await ds.set_camera(3, updated_by="op")
    assert ds.snapshot()["active_camera"] == 3
    await ds.set_camera(1, updated_by="op")
    # AI publish blocked while grabbed.
    result = await ds.system_publish(
        scene="main", active_camera=2, updated_by="live_director"
    )
    assert result["changed"] is False
    # LiveDirector: zero AI writes, shadow entries continue.
    await rig.fight("fight-b", ["team-b", "team-c"], 90.0)
    assert ds.snapshot()["active_camera"] == 1
    assert ds.snapshot()["preview"] is None
    entry = rig.live.decision_log()[-1]
    assert entry["outcome"] == "hold"
    assert entry["rule"] == "clone_grabbed_no_write"
    assert entry["mode"] == "clone"
    # Explicit release resumes clone firing.
    await ds.release_clone_control(updated_by="op", reason="explicit")
    assert ds.snapshot()["clone_grabbed"] is False
    await rig.live.evaluate_now()
    assert ds.snapshot()["active_camera"] == 2


async def test_clone_grab_inactivity_auto_release():
    config = LiveDirectorConfig(clone_grab_inactivity_s=20.0)
    rig = Rig(config=config)
    ds = rig.director_service
    await ds.set_mode("clone")
    await ds.grab_clone_control(updated_by="op")
    await rig.fight("fight-b", ["team-b", "team-c"], 90.0)
    assert ds.snapshot()["clone_grabbed"] is True  # first eval observes
    assert ds.snapshot()["active_camera"] == 1
    rig.clock.advance(21)  # > 20 s inactivity, fight still fresh (<25 s)
    await rig.live.evaluate_now()
    assert ds.snapshot()["clone_grabbed"] is False
    # Release and firing happen in the same evaluation.
    assert ds.snapshot()["active_camera"] == 2


async def test_clone_grab_human_write_resets_inactivity_timer():
    config = LiveDirectorConfig(clone_grab_inactivity_s=20.0)
    rig = Rig(config=config)
    ds = rig.director_service
    await ds.set_mode("clone")
    await ds.grab_clone_control(updated_by="op")
    await rig.fight("fight-b", ["team-b", "team-c"], 90.0)
    rig.clock.advance(15)
    await ds.set_camera(3, updated_by="op")  # human write resets timer
    await ds.set_camera(1, updated_by="op")
    rig.clock.advance(15)  # 30 s since grab, 15 s since write
    await rig.live.evaluate_now()
    assert ds.snapshot()["clone_grabbed"] is True


async def test_grab_only_valid_in_clone():
    ds = DirectorService()
    with pytest.raises(DirectorModeError):
        await ds.grab_clone_control(updated_by="op")
    await ds.set_mode("manual")
    with pytest.raises(DirectorModeError):
        await ds.grab_clone_control(updated_by="op")


async def test_clone_mode_validation_and_snapshot():
    ds = DirectorService()
    state = await ds.set_mode("clone")
    assert state["mode"] == "clone"
    assert state["clone_grabbed"] is False
    with pytest.raises(ValueError):
        await ds.set_mode("bogus")


async def test_transitions_journal_logged():
    captured = []
    register_decision_sink(captured.append)
    ds = DirectorService()
    # Phase 28: fresh default is semi_auto; this sequence starts from
    # full_auto, so pin it without logging a transition of its own.
    ds._state["mode"] = "full_auto"
    await ds.set_mode("clone")
    await ds.grab_clone_control(updated_by="op")
    await ds.release_clone_control(updated_by="op", reason="explicit")
    await ds.set_mode("full_auto")
    transitions = [e for e in captured if e["outcome"] == "mode_transition"]
    assert [e["trigger"] for e in transitions] == [
        "mode_change", "grab", "release", "mode_change",
    ]
    assert transitions[0]["rule"] == "mode_full_auto_to_clone"
    assert transitions[1]["rule"] == "clone_grabbed"
    assert transitions[2]["rule"] == "clone_released_explicit"
    assert transitions[3]["rule"] == "mode_clone_to_full_auto"
    assert "op" in transitions[1]["reason"]


async def test_leaving_clone_drops_grab():
    ds = DirectorService()
    await ds.set_mode("clone")
    await ds.grab_clone_control(updated_by="op")
    state = await ds.set_mode("manual")
    assert state["clone_grabbed"] is False
    assert state["clone_grabbed_by"] is None
