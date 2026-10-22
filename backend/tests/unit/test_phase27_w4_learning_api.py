# backend/tests/unit/test_phase27_w4_learning_api.py
"""Phase 27 (W4) — Director Learning API + clone-mode API completion.

Covers:
  * auth probes: unauthenticated 401 everywhere; viewer / spotter /
    observer 403 on reads AND writes; operator reads OK but 403 on
    writes; director/admin full access.
  * director mode endpoint accepts "clone"; DirectorState carries the
    clone grab fields.
  * clone grab: 409 outside clone mode (service message surfaced),
    success in clone, release flow, release-when-not-grabbed is an
    honest no-op state (clone_grabbed stays false).
  * learning status: every number from real seeded rows (journal
    stats, fate contributors by decided_by, learned_from counts,
    profile existence, knob effective values vs defaults, journal
    drop counters).
  * proposals via API: analyze creates a proposal from W3-style seeded
    evidence; approve changes the engine's effective value (asserted
    via the provider-backed status); double-decide -> 409; unknown
    id -> 404; reject path; rollback via API restores the exact value.
  * learning mode PUT validation (422) + mode effect (off -> analyze
    creates nothing).

The learning stack (engine/provider/journal) is built on a file-backed
sqlite DB with NullPool so the TestClient's event loop and the test's
own seeding loop never share a connection.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.api.deps import get_current_user
from app.api.v1.director.router import router as director_router
from app.api.v1.director.schemas import ModeRequest
from app.api.v1.director_learning.router import (
    router as learning_router,
)
from app.core.security import TokenPayload
from app.db.base import Base
from app.services import director_service as director_module
from app.services.director_learning.engine import (
    LearningEngine,
    set_engine,
)
from app.services.director_learning.journal import (
    DecisionJournal,
    clear_sinks,
    set_journal,
)
from app.services.director_learning.knobs import (
    KnobProvider,
    clear_provider,
    register_provider,
)
from app.services.director_learning.models import (
    LEARNING_STATE_ID,
    DirectorDecision,
    DirectorDecisionLabel,
    DirectorLearningState,
    DirectorShadowDivergence,
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

BASE = "/api/v1/director-learning"
DIRECTOR = "/api/v1/director"


def _principal(role: str) -> TokenPayload:
    return TokenPayload(
        user_id=f"u-{role}", username=f"{role}-user", role=role
    )


# ── stack fixture ──────────────────────────────────────────────────


class Stack:
    def __init__(self, tmp_path):
        db_path = tmp_path / "learning.db"
        self.db_engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool
        )

        async def _create():
            async with self.db_engine.begin() as conn:
                await conn.run_sync(
                    lambda sync_conn: Base.metadata.create_all(
                        sync_conn, tables=LEARNING_TABLES
                    )
                )

        asyncio.run(_create())
        self.maker = async_sessionmaker(
            self.db_engine, class_=AsyncSession, expire_on_commit=False
        )
        self.provider = KnobProvider(session_factory=self.maker)
        self.engine = LearningEngine(
            session_factory=self.maker, provider=self.provider
        )
        self.journal = DecisionJournal(session_factory=self.maker)
        register_provider(self.provider)
        set_engine(self.engine)
        set_journal(self.journal)
        self.auth = {"user": _principal("director")}

    def app(self, authenticated: bool = True) -> FastAPI:
        app = FastAPI()
        app.include_router(director_router, prefix="/api/v1")
        app.include_router(learning_router, prefix="/api/v1")
        if authenticated:
            app.dependency_overrides[get_current_user] = lambda: self.auth[
                "user"
            ]
        return app

    def client(self, role: str | None = None) -> TestClient:
        if role is not None:
            self.auth["user"] = _principal(role)
        return TestClient(self.app())

    def dispose(self):
        asyncio.run(self.db_engine.dispose())


@pytest.fixture()
def stack(tmp_path):
    # Reset the process-wide director singleton + learning globals so
    # no state leaks between tests (the routers use the singletons).
    director_module.director_service._state.clear()
    director_module.director_service._state.update(
        director_module._default_state()
    )
    director_module.director_service._suggestions.clear()
    s = Stack(tmp_path)
    yield s
    s.dispose()
    set_engine(None)
    set_journal(None)
    clear_provider()
    clear_sinks()
    director_module.director_service._state.clear()
    director_module.director_service._state.update(
        director_module._default_state()
    )
    director_module.director_service._suggestions.clear()


# ── seeding (W3-style) ─────────────────────────────────────────────


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


async def _seed(stack: Stack, *rows):
    async with stack.maker() as session:
        for row in rows:
            session.add(row)
        await session.commit()


def _seed_r1(stack: Stack, n_decided: int, n_approved: int, prefix="r1"):
    rows = []
    for i in range(n_decided):
        sid = f"{prefix}-sug-{i}"
        rows.append(DirectorDecision(**_base_decision(suggestion_id=sid)))
        rows.append(
            DirectorSuggestionFate(
                id=str(uuid.uuid4()), suggestion_id=sid,
                fate="approved" if i < n_approved else "rejected",
                decided_by="seed-op",
                ts=datetime.now(timezone.utc) + timedelta(seconds=i),
            )
        )
    asyncio.run(_seed(stack, *rows))


def _set_learning_mode_row(stack: Stack, mode: str):
    asyncio.run(
        _seed(
            stack,
            DirectorLearningState(
                id=LEARNING_STATE_ID, learning_mode=mode,
                updated_by="test",
            ),
        )
    )


def _knob(status_body: dict, name: str) -> dict:
    return next(k for k in status_body["knobs"] if k["name"] == name)


# ── auth probes ────────────────────────────────────────────────────


def test_unauthenticated_401_everywhere(stack):
    client = TestClient(stack.app(authenticated=False))
    assert client.get(f"{BASE}/status").status_code == 401
    assert client.get(f"{BASE}/proposals").status_code == 401
    assert client.get(f"{BASE}/adjustments").status_code == 401
    assert client.get(f"{BASE}/mode").status_code == 401
    assert client.post(f"{BASE}/analyze").status_code == 401
    assert client.put(f"{BASE}/mode", json={"mode": "auto"}).status_code == 401
    assert client.post(f"{DIRECTOR}/clone/grab").status_code == 401


@pytest.mark.parametrize("role", ["viewer", "spotter", "observer"])
def test_non_staff_roles_403_on_reads_and_writes(stack, role):
    client = stack.client(role)
    assert client.get(f"{BASE}/status").status_code == 403
    assert client.get(f"{BASE}/proposals").status_code == 403
    assert client.get(f"{BASE}/adjustments").status_code == 403
    assert client.get(f"{BASE}/mode").status_code == 403
    assert client.post(f"{BASE}/analyze").status_code == 403
    assert client.put(f"{BASE}/mode", json={"mode": "auto"}).status_code == 403
    assert (
        client.post(f"{BASE}/proposals/whatever/approve").status_code == 403
    )
    assert (
        client.post(f"{BASE}/adjustments/whatever/rollback").status_code
        == 403
    )


def test_operator_reads_ok_writes_403(stack):
    client = stack.client("operator")
    assert client.get(f"{BASE}/status").status_code == 200
    assert client.get(f"{BASE}/proposals").status_code == 200
    assert client.get(f"{BASE}/adjustments").status_code == 200
    assert client.get(f"{BASE}/mode").status_code == 200
    # Writes are director/admin only.
    assert client.post(f"{BASE}/analyze").status_code == 403
    assert client.put(f"{BASE}/mode", json={"mode": "auto"}).status_code == 403
    assert (
        client.post(f"{BASE}/proposals/whatever/approve").status_code == 403
    )
    assert (
        client.post(f"{BASE}/proposals/whatever/reject").status_code == 403
    )
    assert (
        client.post(f"{BASE}/adjustments/whatever/rollback").status_code
        == 403
    )


def test_admin_full_access(stack):
    client = stack.client("admin")
    assert client.get(f"{BASE}/status").status_code == 200
    assert client.post(f"{BASE}/analyze").status_code == 200


# ── director mode + clone endpoints ────────────────────────────────


def test_mode_schema_accepts_clone():
    assert ModeRequest(mode="clone").mode == "clone"
    assert ModeRequest(mode=" Clone ").mode == "clone"
    with pytest.raises(Exception):
        ModeRequest(mode="turbo")


def test_mode_endpoint_accepts_clone_and_state_carries_grab_fields(stack):
    client = stack.client("operator")
    resp = client.post(f"{DIRECTOR}/mode", json={"mode": "clone"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "clone"
    assert body["clone_grabbed"] is False
    assert body["clone_grabbed_by"] is None
    assert body["clone_grabbed_at"] is None
    state = client.get(f"{DIRECTOR}/state")
    assert state.status_code == 200
    assert state.json()["mode"] == "clone"


def test_grab_409_outside_clone_with_service_message(stack):
    client = stack.client("operator")
    resp = client.post(f"{DIRECTOR}/clone/grab")
    assert resp.status_code == 409
    assert "CLONE" in resp.json()["detail"]


def test_grab_release_flow_in_clone(stack):
    client = stack.client("director")
    client.post(f"{DIRECTOR}/mode", json={"mode": "clone"})

    grabbed = client.post(f"{DIRECTOR}/clone/grab")
    assert grabbed.status_code == 200
    body = grabbed.json()
    assert body["clone_grabbed"] is True
    assert body["clone_grabbed_by"] == "director-user"
    assert body["clone_grabbed_at"] is not None

    released = client.post(f"{DIRECTOR}/clone/release")
    assert released.status_code == 200
    assert released.json()["clone_grabbed"] is False
    assert released.json()["clone_grabbed_by"] is None

    # Release when not grabbed: honest no-op — 200 with the UNCHANGED
    # state (still not grabbed), not a fabricated release.
    again = client.post(f"{DIRECTOR}/clone/release")
    assert again.status_code == 200
    assert again.json()["clone_grabbed"] is False


def test_clone_write_roles(stack):
    # Viewer cannot grab (director router writes are operator+).
    client = stack.client("viewer")
    assert client.post(f"{DIRECTOR}/clone/grab").status_code == 403
    assert client.post(f"{DIRECTOR}/clone/release").status_code == 403


# ── status: real numbers only ──────────────────────────────────────


def test_status_numbers_come_from_seeded_rows(stack):
    did = str(uuid.uuid4())
    asyncio.run(
        _seed(
            stack,
            DirectorDecision(**_base_decision(id=did, suggestion_id="s-1")),
            DirectorDecision(**_base_decision(suggestion_id="s-2")),
            DirectorSuggestionFate(
                id=str(uuid.uuid4()), suggestion_id="s-1",
                fate="approved", decided_by="dir-a",
                ts=datetime.now(timezone.utc),
            ),
            DirectorSuggestionFate(
                id=str(uuid.uuid4()), suggestion_id="s-2",
                fate="rejected", decided_by="dir-a",
                ts=datetime.now(timezone.utc),
            ),
            DirectorSuggestionFate(
                id=str(uuid.uuid4()), suggestion_id="s-3",
                fate="expired", decided_by=None,
                ts=datetime.now(timezone.utc),
            ),
            DirectorDecisionLabel(
                id=str(uuid.uuid4()), decision_id=did, label="fizzle",
                confidence=0.9, confidence_band="high", evidence={},
                labeler_version="test",
                labeled_at=datetime.now(timezone.utc),
            ),
            DirectorShadowDivergence(
                id=str(uuid.uuid4()), ts=datetime.now(timezone.utc),
                kind="human_switched_ai_held", phase="mid_game",
                human_action={}, ai_candidate={}, detail={},
            ),
        )
    )
    client = stack.client("operator")
    resp = client.get(f"{BASE}/status")
    assert resp.status_code == 200
    body = resp.json()

    assert body["learning_mode"] == "suggest"  # default, no state row
    assert body["journal_stats"]["decisions_total"] == 2
    assert body["journal_stats"]["labels_total"] == 1
    assert body["journal_stats"]["fates_by_fate"] == {
        "approved": 1, "rejected": 1, "expired": 1,
    }
    # Contributors: only dir-a, 2 decisions (the expiry has no decider).
    assert body["profile"]["contributors"] == [
        {"username": "dir-a", "decided_count": 2}
    ]
    # learned_from: 1 labeled, 2 decided fates (expiry excluded), 1 divergence.
    assert body["profile"]["learned_from"] == {
        "labeled_decisions": 1, "decided_fates": 2, "divergences": 1,
    }
    # Cold start: no applied adjustments -> no profile, defaults served.
    assert body["profile"]["exists"] is False
    assert body["profile"]["applied_count"] == 0
    knob = _knob(body, "margin_default")
    assert knob["default"] == 15.0
    assert knob["effective_learned"] == 15.0
    assert knob["effective_factory"] == 15.0
    assert len(body["knobs"]) == 11
    # Journal drop counters ride along from the in-process journal.
    assert body["journal"] is not None
    assert body["journal"]["stats"]["decisions_dropped"] == 0
    assert body["journal"]["stats"]["fates_dropped"] == 0


# ── proposals lifecycle via API ────────────────────────────────────


def test_analyze_approve_rollback_cycle_changes_effective_value(stack):
    _set_learning_mode_row(stack, "suggest")
    _seed_r1(stack, 10, 3)  # R1: approval rate 0.30 < 0.40
    client = stack.client("director")

    created = client.post(f"{BASE}/analyze")
    assert created.status_code == 200
    proposals = created.json()
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["knob"] == "margin_default"
    assert proposal["status"] == "proposed"
    assert proposal["before_value"] == 15.0
    assert proposal["after_value"] == 18.0
    assert proposal["evidence"]["approval_rate"] == 0.3
    assert "0.30" in proposal["reason"]

    # Listed as pending; effective value NOT yet changed.
    pending = client.get(f"{BASE}/proposals")
    assert [p["id"] for p in pending.json()] == [proposal["id"]]
    status_body = client.get(f"{BASE}/status").json()
    assert _knob(status_body, "margin_default")["effective_learned"] == 15.0
    assert status_body["profile"]["exists"] is False

    # Approve via API -> engine effective value moves; factory doesn't.
    approved = client.post(f"{BASE}/proposals/{proposal['id']}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "applied"
    assert approved.json()["applied_by"] == "director-user"
    status_body = client.get(f"{BASE}/status").json()
    knob = _knob(status_body, "margin_default")
    assert knob["effective_learned"] == 18.0
    assert knob["effective_factory"] == 15.0
    assert status_body["profile"]["exists"] is True
    assert status_body["profile"]["applied_count"] == 1

    # Already decided -> 409 on both verbs.
    assert (
        client.post(f"{BASE}/proposals/{proposal['id']}/approve").status_code
        == 409
    )
    assert (
        client.post(f"{BASE}/proposals/{proposal['id']}/reject").status_code
        == 409
    )

    # Rollback via API -> compensating row, exact value restored.
    rolled = client.post(f"{BASE}/adjustments/{proposal['id']}/rollback")
    assert rolled.status_code == 200
    compensating = rolled.json()
    assert compensating["parent_id"] == proposal["id"]
    assert compensating["status"] == "applied"
    assert compensating["after_value"] == 15.0
    assert compensating["applied_by"] == "director-user"
    status_body = client.get(f"{BASE}/status").json()
    assert _knob(status_body, "margin_default")["effective_learned"] == 15.0

    # Adjustments list shows the whole append-only trail.
    rows = client.get(f"{BASE}/adjustments").json()
    by_id = {r["id"]: r for r in rows}
    assert by_id[proposal["id"]]["status"] == "rolled_back"
    assert by_id[compensating["id"]]["status"] == "applied"
    applied_only = client.get(
        f"{BASE}/adjustments", params={"status": "applied"}
    ).json()
    assert [r["id"] for r in applied_only] == [compensating["id"]]


def test_reject_via_api_and_unknown_ids_404(stack):
    _set_learning_mode_row(stack, "suggest")
    _seed_r1(stack, 10, 2, prefix="rej")
    client = stack.client("director")

    proposal = client.post(f"{BASE}/analyze").json()[0]
    rejected = client.post(f"{BASE}/proposals/{proposal['id']}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["applied_by"] == "director-user"
    # Value never moved.
    status_body = client.get(f"{BASE}/status").json()
    assert _knob(status_body, "margin_default")["effective_learned"] == 15.0
    assert status_body["profile"]["exists"] is False

    assert (
        client.post(f"{BASE}/proposals/nope/approve").status_code == 404
    )
    assert client.post(f"{BASE}/proposals/nope/reject").status_code == 404
    assert (
        client.post(f"{BASE}/adjustments/nope/rollback").status_code == 404
    )
    # Rollback of a non-applied row -> 409.
    assert (
        client.post(
            f"{BASE}/adjustments/{proposal['id']}/rollback"
        ).status_code
        == 409
    )


def test_analyze_empty_db_creates_nothing(stack):
    client = stack.client("director")
    resp = client.post(f"{BASE}/analyze")
    assert resp.status_code == 200
    assert resp.json() == []


# ── learning mode endpoints ────────────────────────────────────────


def test_mode_get_put_and_validation(stack):
    client = stack.client("director")
    assert client.get(f"{BASE}/mode").json() == {"mode": "suggest"}
    resp = client.put(f"{BASE}/mode", json={"mode": "auto"})
    assert resp.status_code == 200
    assert resp.json() == {"mode": "auto"}
    assert client.get(f"{BASE}/mode").json() == {"mode": "auto"}
    assert client.put(f"{BASE}/mode", json={"mode": "turbo"}).status_code == 422
    assert client.put(f"{BASE}/mode", json={"mode": ""}).status_code == 422
    # Mode persisted — a fresh read through the engine agrees.
    assert asyncio.run(stack.engine.get_learning_mode()) == "auto"


def test_mode_off_makes_analyze_create_nothing(stack):
    _set_learning_mode_row(stack, "suggest")
    _seed_r1(stack, 10, 0, prefix="off")
    client = stack.client("director")
    assert client.put(f"{BASE}/mode", json={"mode": "off"}).status_code == 200
    created = client.post(f"{BASE}/analyze")
    assert created.status_code == 200
    assert created.json() == []
    assert client.get(f"{BASE}/adjustments").json() == []
