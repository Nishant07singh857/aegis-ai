# backend/tests/unit/test_phase29_w4_pilot_service.py
"""Phase 29 (W4) — Observer Pilot service + journal + roster + API.

Fakes only where the brief allows: fake monitor (scripted pov_states),
fake executor (scripted TapResult, calls recorded), fake mode
provider, fake PilotControl (W3 pinned contract shape). The rules
engine, the journal writer and the roster provider are the REAL
modules, exercised against a file-backed sqlite DB (the pattern the
Phase 27 tests use: NullPool, Base.metadata.create_all on the one
table under test).

Covered: full_auto execute + journal + last_tap semantics (verified
AND failed_stay update it; blocked does not), semi_auto suggestion /
approve / reject / TTL expiry / dedupe, manual + clone-grabbed shadow,
kill-switch OFF (zero taps incl. override, API 409s), roster-absent
UNKNOWN honesty, killfeed name mapping (unmatched dropped), journal
persistence + API decisions read, roster provider DB load + refresh,
LiveDirector POV exposure default-off.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import get_current_user
from app.api.v1.observer_pilot.router import router as pilot_router
from app.core.event_bus import BaseEvent, EventRecord
from app.core.security import TokenPayload
from app.db.base import Base
from app.db.models.observer_slot import ObserverSlot
from app.db.models.player import Player
from app.db.models.team import Team
from app.services.observer_pilot.models import POVPilotDecision
from app.services.observer_pilot.pov_journal import POVJournal, set_pov_journal
from app.services.observer_pilot.roster import CachedRosterProvider
from app.services.observer_pilot.service import (
    ObserverPilotService,
    PilotServiceConfig,
    set_pilot_service,
)
from app.services.observer_pilot.types import (
    FeedPOVState,
    PlayerPOV,
    POVPlayerStatus,
)

pytestmark = pytest.mark.unit

ALIVE = POVPlayerStatus.ALIVE
DEAD = POVPlayerStatus.DEAD
NAMES = ["Alpha", "GodL", "Nova", "Rex"]


# ── fakes ────────────────────────────────────────────────────────


@dataclass
class FakeTapResult:
    status: str = "verified"
    taps_sent: int = 1
    detail: str = "fake tap"
    slot_number: Optional[int] = None
    to_index: Optional[int] = None


class FakeControl:
    """W3 PilotControl pinned shape: enabled + disabled_slots + allows."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.disabled_slots = set()

    def allows(self, slot) -> bool:
        return bool(self.enabled) and int(slot) not in self.disabled_slots


class FakeExecutor:
    def __init__(self, results: Optional[List[FakeTapResult]] = None):
        self.control = FakeControl()
        self._results = list(results or [])
        self.calls: List[dict] = []
        self.target_calls: List[dict] = []

    def _next(self, default_status="verified", taps=1):
        if self._results:
            return self._results.pop(0)
        return FakeTapResult(status=default_status, taps_sent=taps)

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


class FakeMonitor:
    def __init__(self, states: Optional[Dict[int, FeedPOVState]] = None):
        self._states = dict(states or {})

    def set(self, state: FeedPOVState):
        self._states[state.slot_number] = state

    def pov_state(self, slot_number):
        return self._states.get(int(slot_number))

    def pov_states(self):
        return dict(self._states)


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
    identity_known: bool = True,
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
        identity_known=identity_known,
        identity_source="both" if identity_known else "none",
    )


def dead_spectated_state(**kw) -> FeedPOVState:
    """Spectated idx0 DEAD, idx1 alive -> R1 decision to idx1."""
    return make_state(statuses=(DEAD, ALIVE, ALIVE, ALIVE), **kw)


# ── DB fixture (sqlite, NullPool — Phase 27 pattern) ─────────────


class Stack:
    def __init__(self, tmp_path):
        db_path = tmp_path / "pov.db"
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool
        )
        tables = [
            Base.metadata.tables[name]
            for name in (
                "pov_pilot_decisions", "observer_slots", "teams", "players",
            )
        ]

        async def _create():
            async with self.engine.begin() as conn:
                await conn.run_sync(
                    lambda c: Base.metadata.create_all(c, tables=tables)
                )

        asyncio.run(_create())
        self.maker = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    def dispose(self):
        asyncio.run(self.engine.dispose())


@pytest.fixture()
def stack(tmp_path):
    s = Stack(tmp_path)
    yield s
    s.dispose()
    set_pilot_service(None)
    set_pov_journal(None)


def build_service(stack, *, mode="full_auto", grabbed=False, executor=None,
                  monitor=None, clock=None, suggestion_ttl=20.0,
                  dead_confirm_evals=1):
    # dead_confirm_evals=1 isolates these W4 mode-policy tests from
    # the Phase 29 D-1 DEAD-confirmation gate (post-QA fix): their
    # subject is mode policy / suggestions / kill-switch, exercised
    # through single-evaluation R1 decisions. The gate's DEFAULT (2)
    # behaviour is covered in test_phase29_fixes.py.
    clock = clock or Clock()
    journal = POVJournal(session_factory=stack.maker, clock=clock)
    executor = executor or FakeExecutor()
    service = ObserverPilotService(
        monitor=monitor or FakeMonitor({1: dead_spectated_state()}),
        executor=executor,
        config=PilotServiceConfig(
            suggestion_ttl_s=suggestion_ttl,
            dead_confirm_evals=dead_confirm_evals,
        ),
        mode_provider=lambda: (mode, grabbed),
        engagement_provider=lambda: {"teams": {}},
        phase_provider=lambda: "mid_game",
        journal=journal,
        clock=clock,
    )
    return service, journal, executor, clock


async def journal_rows(stack) -> List[POVPilotDecision]:
    async with stack.maker() as session:
        rows = (await session.execute(
            select(POVPilotDecision).order_by(POVPilotDecision.ts)
        )).scalars().all()
        return list(rows)


# ── full_auto ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_auto_executes_and_journals(stack):
    service, journal, executor, _clock = build_service(stack)
    outcome = await service.evaluate_feed(dead_spectated_state())
    assert outcome["outcome"] == "executed"
    assert executor.calls and executor.calls[0]["to"] == 1
    assert executor.calls[0]["rule"] == "R1_SPECTATED_DEAD"
    await journal.drain()
    rows = await journal_rows(stack)
    assert len(rows) == 1
    row = rows[0]
    assert row.fate == "executed"
    assert row.verification == "verified"
    assert row.slot_number == 1 and row.from_index == 0 and row.to_index == 1
    assert row.rule == "R1_SPECTATED_DEAD"
    assert row.mode == "full_auto"
    assert row.taps_sent == 1


@pytest.mark.asyncio
async def test_last_tap_updates_only_when_tap_sent(stack):
    # verified (taps_sent=1) updates; failed_stay (tap sent, POV did
    # not move) also updates; blocked (taps_sent=0) does NOT.
    executor = FakeExecutor(results=[
        FakeTapResult(status="verified", taps_sent=1),
        FakeTapResult(status="failed_stay", taps_sent=1),
        FakeTapResult(status="blocked_kill_switch", taps_sent=0),
    ])
    service, journal, _ex, _c = build_service(stack, executor=executor)
    await service.evaluate_feed(dead_spectated_state(observed_at=1000.0), now=1000.0)
    assert service._last_tap_at[1] == 1000.0
    await service.evaluate_feed(dead_spectated_state(observed_at=2000.0), now=2000.0)
    assert service._last_tap_at[1] == 2000.0
    outcome = await service.evaluate_feed(
        dead_spectated_state(observed_at=3000.0), now=3000.0
    )
    assert outcome["outcome"] == "failed"
    assert service._last_tap_at[1] == 2000.0  # unchanged: nothing was sent
    await journal.drain()
    fates = [r.fate for r in await journal_rows(stack)]
    assert fates == ["executed", "executed", "failed"]


@pytest.mark.asyncio
async def test_clone_not_grabbed_auto_executes(stack):
    service, _j, executor, _c = build_service(stack, mode="clone", grabbed=False)
    outcome = await service.evaluate_feed(dead_spectated_state())
    assert outcome["outcome"] == "executed"
    assert executor.calls


# ── semi_auto suggestions ────────────────────────────────────────


@pytest.mark.asyncio
async def test_semi_auto_suggests_without_tapping_then_approve_executes(stack):
    service, journal, executor, clock = build_service(stack, mode="semi_auto")
    outcome = await service.evaluate_feed(dead_spectated_state())
    assert outcome["outcome"] == "suggested"
    assert executor.calls == []  # NO tap in semi_auto
    suggestion = outcome["suggestion"]
    await journal.drain()
    rows = await journal_rows(stack)
    assert [r.fate for r in rows] == ["suggested"]

    clock.t = 1001.0  # distinct ts so journal ordering is deterministic
    result = await service.approve_suggestion(suggestion["id"], by="director-user")
    assert result["executed"] is True
    assert executor.calls and executor.calls[0]["to"] == 1
    await journal.drain()
    rows = await journal_rows(stack)
    assert rows[-1].fate == "approved"
    assert rows[-1].decided_by == "director-user"
    assert rows[-1].verification == "verified"


@pytest.mark.asyncio
async def test_semi_auto_reject_journals_fate_and_never_taps(stack):
    service, journal, executor, clock = build_service(stack, mode="semi_auto")
    outcome = await service.evaluate_feed(dead_spectated_state())
    sid = outcome["suggestion"]["id"]
    clock.t = 1001.0
    await service.reject_suggestion(sid, by="director-user")
    assert executor.calls == []
    await journal.drain()
    rows = await journal_rows(stack)
    assert [r.fate for r in rows] == ["suggested", "rejected"]
    with pytest.raises(KeyError):
        await service.reject_suggestion(sid, by="director-user")


@pytest.mark.asyncio
async def test_suggestion_ttl_expiry(stack):
    clock = Clock(1000.0)
    service, journal, executor, _c = build_service(
        stack, mode="semi_auto", clock=clock, suggestion_ttl=20.0
    )
    outcome = await service.evaluate_feed(dead_spectated_state(), now=1000.0)
    sid = outcome["suggestion"]["id"]
    clock.t = 1021.0  # past the 20 s TTL
    assert service.pending_suggestions() == []
    await journal.drain()
    rows = await journal_rows(stack)
    assert [r.fate for r in rows] == ["suggested", "expired"]
    with pytest.raises(KeyError):
        await service.approve_suggestion(sid, by="director-user")
    assert executor.calls == []


@pytest.mark.asyncio
async def test_suggestion_same_target_dedupes(stack):
    service, journal, _ex, _c = build_service(stack, mode="semi_auto")
    first = await service.evaluate_feed(dead_spectated_state(observed_at=1000.0))
    second = await service.evaluate_feed(dead_spectated_state(observed_at=1001.0))
    assert first["suggestion"]["id"] == second["suggestion"]["id"]
    await journal.drain()
    rows = await journal_rows(stack)
    assert [r.fate for r in rows] == ["suggested"]  # no duplicate row


# ── manual / clone-grabbed shadow ────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,grabbed", [("manual", False), ("clone", True)])
async def test_shadow_modes_never_tap_or_suggest(stack, mode, grabbed):
    service, journal, executor, _c = build_service(
        stack, mode=mode, grabbed=grabbed
    )
    outcome = await service.evaluate_feed(dead_spectated_state())
    assert outcome["outcome"] == "shadow"
    assert executor.calls == []
    assert service.pending_suggestions() == []
    await journal.drain()
    rows = await journal_rows(stack)
    assert len(rows) == 1 and rows[0].fate == "shadow"


# ── kill-switch ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_kill_switch_off_zero_taps_including_override(stack):
    service, journal, executor, _c = build_service(stack, mode="full_auto")
    service.set_kill_switch(False)
    outcome = await service.evaluate_feed(dead_spectated_state())
    assert outcome["outcome"] == "suppressed_kill_switch"
    assert executor.calls == []
    result = await service.override(1, 2, by="op")
    assert result["status"] == "blocked_kill_switch"
    assert result["executed"] is False
    assert executor.target_calls == []  # checked before the executor
    await journal.drain()
    rows = await journal_rows(stack)
    assert {r.fate for r in rows} == {"shadow", "failed"}
    failed = [r for r in rows if r.fate == "failed"][0]
    assert failed.verification == "blocked_kill_switch"
    assert failed.rule == "manual_override"


@pytest.mark.asyncio
async def test_kill_switch_off_semi_auto_no_suggestion(stack):
    service, _j, executor, _c = build_service(stack, mode="semi_auto")
    service.set_kill_switch(False)
    await service.evaluate_feed(dead_spectated_state())
    assert service.pending_suggestions() == []
    assert executor.calls == []


# ── UNKNOWN honesty ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_identity_unknown_no_decision_no_tap(stack):
    service, journal, executor, _c = build_service(stack, mode="full_auto")
    state = dead_spectated_state(identity_known=False, spectated=None)
    # FeedPOVState with spectated None: rebuild honestly.
    state = FeedPOVState(
        slot_number=1, source_id=state.source_id, team_id=None,
        observed_at=1000.0, spectated_index=None, players=state.players,
        identity_known=False, identity_source="none",
    )
    outcome = await service.evaluate_feed(state)
    assert outcome["outcome"] == "no_action"
    assert executor.calls == []
    status = service.status()
    assert status["feeds"][0]["last_note"]["blocked_rule"] == "R0_NO_EVIDENCE"


@pytest.mark.asyncio
async def test_dwell_blocked_note_surfaces_why_camera_stayed(stack):
    # R3 decision at t=1000 executes; a second fight signal inside the
    # dwell window is blocked and the NOTE carries the dwell numbers.
    executor = FakeExecutor()
    service, _j, _ex, _c = build_service(stack, executor=executor)
    fighting = dict(statuses=(ALIVE, ALIVE, ALIVE, ALIVE),
                    hp_velocities=(None, -0.2, None, None))
    await service.evaluate_feed(make_state(observed_at=1000.0, **fighting), now=1000.0)
    assert service._last_tap_at[1] == 1000.0
    # Spectated now idx1 (post-tap), teammate idx2 drops HP at t=1003.
    state2 = make_state(spectated=1, observed_at=1003.0,
                        hp_velocities=(None, None, -0.2, None))
    outcome = await service.evaluate_feed(state2, now=1003.0)
    assert outcome["outcome"] == "no_action"
    note = service.status()["feeds"][0]["last_note"]
    assert note["blocked_rule"] == "R3_TEAMMATE_IN_FIGHT"
    assert "dwell" in note["reason"]


# ── killfeed involvement mapping ─────────────────────────────────


def _record(event_type: str, data: dict) -> EventRecord:
    return EventRecord(event=BaseEvent(event_type=event_type, data=data))


@pytest.mark.asyncio
async def test_killfeed_names_map_to_roster_indices(stack):
    service, _j, _ex, _c = build_service(stack)
    await service.handle_record(_record("kill_event", {
        "killer": "Nova", "victim": "SomeEnemy",
        "killerTeam": "EnemyTeam", "victimTeam": "Other",
        "timestamp": 1005.0,
    }))
    # Nova is roster idx2 on feed 1: a "killer" involvement lands;
    # the enemy victim name matches no roster -> dropped there.
    assert service._involvements[1] == [(2, "killer", 1005.0)]
    assert service._last_fight_signal_at[1] == 1005.0

    await service.handle_record(_record("knock_event", {
        "killer": "GodL", "victim": "Alpha", "timestamp": 1006.0,
    }))
    kinds = {(idx, kind) for idx, kind, _at in service._involvements[1]}
    assert (1, "knocked") in kinds and (0, "victim") in kinds


@pytest.mark.asyncio
async def test_killfeed_unmatched_names_dropped_and_counted(stack):
    service, _j, _ex, _c = build_service(stack)
    await service.handle_record(_record("kill_event", {
        "killer": "Nobody", "victim": "Noone", "timestamp": 1005.0,
    }))
    assert service._involvements.get(1, []) == []
    assert service.stats["killfeed_dropped_unmatched"] == 1


# ── roster provider ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_roster_provider_loads_slots_and_players(stack):
    async with stack.maker() as session:
        team = Team(id="team-1", name="Team One", tag="ONE", colors={})
        session.add(team)
        session.add(ObserverSlot(slot_number=3, team_id="team-1",
                                 status="feeding"))
        session.add(ObserverSlot(slot_number=4, team_id=None, status="idle"))
        for i, name in enumerate(["Alpha", "GodL"]):
            session.add(Player(id=f"p{i}", team_id="team-1", name=name,
                               ign=name))
        await session.commit()
    provider = CachedRosterProvider(session_factory=stack.maker)
    assert provider(3) is None  # cache empty until refresh: honest None
    await provider.refresh()
    binding = provider(3)
    assert binding is not None
    team_id, roster = binding
    assert team_id == "team-1"
    assert [name for _pid, name in roster] == ["Alpha", "GodL"]
    assert provider(4) is None  # unassigned slot: no roster, ever
    assert provider(9) is None


# ── API ──────────────────────────────────────────────────────────


def _principal(role: str) -> TokenPayload:
    return TokenPayload(user_id=f"u-{role}", username=f"{role}-user", role=role)


class ApiStack:
    def __init__(self, stack, mode="semi_auto"):
        self.service, self.journal, self.executor, self.clock = build_service(
            stack, mode=mode
        )
        set_pilot_service(self.service)
        set_pov_journal(self.journal)
        self.auth = {"user": _principal("operator")}

    def client(self, role: Optional[str] = None, authenticated=True) -> TestClient:
        if role is not None:
            self.auth["user"] = _principal(role)
        app = FastAPI()
        app.include_router(pilot_router, prefix="/api/v1")
        app.state.observer_pilot = self.service
        if authenticated:
            app.dependency_overrides[get_current_user] = lambda: self.auth["user"]
        return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def api_stack(stack):
    return ApiStack(stack)


def test_api_status_shape(api_stack):
    client = api_stack.client()
    res = client.get("/api/v1/observer-pilot/status")
    assert res.status_code == 200
    body = res.json()
    assert body["running"] is False
    assert body["kill_switch"]["enabled"] is True
    assert body["feeds"] and body["feeds"][0]["pov"]["identity_known"] is True
    assert body["feeds"][0]["pov"]["players"][0]["name"] == "Alpha"


def test_api_auth_gates(api_stack):
    assert api_stack.client(authenticated=False).get(
        "/api/v1/observer-pilot/status").status_code == 401
    assert api_stack.client(role="viewer").get(
        "/api/v1/observer-pilot/status").status_code == 403
    assert api_stack.client(role="operator").get(
        "/api/v1/observer-pilot/status").status_code == 200


async def test_api_suggestion_approve_reject_flow(api_stack):
    service = api_stack.service
    outcome = await service.evaluate_feed(dead_spectated_state())
    sid = outcome["suggestion"]["id"]
    client = api_stack.client()
    res = client.get("/api/v1/observer-pilot/status")
    assert res.json()["pending_suggestions"][0]["id"] == sid
    res = client.post(f"/api/v1/observer-pilot/suggestions/{sid}/approve")
    assert res.status_code == 200
    assert res.json()["executed"] is True
    # Second approve: already decided -> 404.
    res = client.post(f"/api/v1/observer-pilot/suggestions/{sid}/approve")
    assert res.status_code == 404
    res = client.post("/api/v1/observer-pilot/suggestions/nope/reject")
    assert res.status_code == 404


def test_api_kill_switch_blocks_override_with_409(api_stack):
    client = api_stack.client()
    res = client.post("/api/v1/observer-pilot/kill-switch",
                      json={"enabled": False})
    assert res.status_code == 200 and res.json()["enabled"] is False
    res = client.post("/api/v1/observer-pilot/override",
                      json={"slot_number": 1, "roster_index": 2})
    assert res.status_code == 409
    assert api_stack.executor.target_calls == []
    res = client.get("/api/v1/observer-pilot/status")
    assert res.json()["kill_switch"]["enabled"] is False
    assert res.json()["behaviour"] == "off_kill_switch"


def test_api_override_executes_and_decisions_list(api_stack):
    import time as _time

    # `with` keeps the TestClient portal loop alive across requests so
    # the journal's async persist (scheduled on that loop) completes —
    # the same persistence the production lifespan loop provides.
    with api_stack.client() as client:
        res = client.post("/api/v1/observer-pilot/override",
                          json={"slot_number": 1, "roster_index": 3})
        assert res.status_code == 200
        assert res.json()["status"] == "verified"
        assert api_stack.executor.target_calls[0]["to"] == 3
        res = client.get("/api/v1/observer-pilot/decisions")
        assert res.status_code == 200
        assert res.json()["journal_running"] is True
        rows = []
        for _ in range(50):
            res = client.get("/api/v1/observer-pilot/decisions?slot=1")
            rows = res.json()["decisions"]
            if rows:
                break
            _time.sleep(0.1)
        assert rows and rows[0]["fate"] == "override"
        assert rows[0]["rule"] == "manual_override"
        res = client.get("/api/v1/observer-pilot/decisions?slot=2")
        assert res.json()["decisions"] == []


def test_api_override_validation(api_stack):
    client = api_stack.client()
    res = client.post("/api/v1/observer-pilot/override",
                      json={"slot_number": 99, "roster_index": 0})
    assert res.status_code == 422


# ── real W3 executor contract (async, TapResult, PilotControl) ──


@pytest.mark.asyncio
async def test_service_with_real_w3_executor(stack):
    """The REAL POVTapExecutor wired into the service: async execute,
    TapResult.reason (not .detail), PilotControl.set_enabled, and the
    TapCalibration flags on the status surface."""
    import numpy as np

    from app.services.observer_pilot.config import ExecutorConfig, TapCalibration
    from app.services.observer_pilot.executor import POVTapExecutor, PilotControl

    class FakeADB:
        def __init__(self):
            self.calls = []

        async def tap(self, serial, x, y):
            self.calls.append((serial, x, y))
            return True

    adb = FakeADB()
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    executor = POVTapExecutor(
        adb=adb,
        config=ExecutorConfig(
            verify_timeout_s=0.15, verify_poll_s=0.01,
            calibration=TapCalibration(),
        ),
        control=PilotControl(),
        device_resolver=lambda slot, src: "SER123",
        frame_provider_for=lambda slot, src: frame,
        spectated_reader=lambda image: 1,  # camera lands on idx1
    )
    service, journal, _ex, _c = build_service(stack, executor=executor)
    outcome = await service.evaluate_feed(dead_spectated_state())
    assert outcome["outcome"] == "executed"
    assert outcome["verification"] == "verified"
    assert adb.calls and adb.calls[0][0] == "SER123"
    assert service._last_tap_at[1] == 1000.0
    await journal.drain()
    rows = await journal_rows(stack)
    assert rows[0].fate == "executed" and rows[0].verification == "verified"
    assert rows[0].evidence["tap_result"]["calibrated"] is False
    # Calibration surface: default profile present, NOT calibrated.
    status = service.status()
    assert status["calibration"]["available"] is True
    assert status["calibration"]["default_calibrated"] is False
    assert "default" in status["calibration"]["profiles"]
    # Kill-switch through the REAL PilotControl.
    assert service.set_kill_switch(False) is False
    assert service.kill_switch_enabled() is False


# ── LiveDirector POV exposure (default off = old behaviour) ──────


def test_live_director_pov_provider_optional_and_read_only():
    from unittest.mock import Mock

    from app.services.director_service import DirectorService
    from app.services.live_director import LiveDirector
    from app.services.vision_intelligence.attribution import StaticSlotResolver

    bus = Mock()
    director = LiveDirector(
        bus=bus, director_service=DirectorService(),
        slot_resolver=StaticSlotResolver({}),
    )
    assert director.pov_summary() == []  # no provider: honest empty
    assert director.status()["pov_feeds"] == []
    state = dead_spectated_state()
    director.set_pov_state_provider(lambda slot: state if slot == 1 else None)
    summary = director.pov_summary()
    assert summary == [{
        "slot_number": 1,
        "source_id": state.source_id,
        "team_id": "team-1",
        "spectated_index": 0,
        "spectated_name": "Alpha",
        "identity_known": True,
    }]
    assert director._pov_on_fight(1, True) is True
    assert director._pov_on_fight(1, False) is False
    assert director._pov_on_fight(2, True) is None  # unknown POV: no guess
