# backend/tests/unit/test_phase35_w3_rotation_payoff.py
"""Phase 35 (Worker W3) — items 6 + 7 ONLY.

Item 6 — rotation enrichment drop (verification Correction 1 / M-6):
the vision bus event carries mode/bearing/speed-px/s, the translator
preserved them, but the bridge dropped them at ``note_rotation()``,
whose signature had no room for them. These tests prove the full
chain intake -> bridge -> director now carries EVERY field intact
(team, occupancy, POI, mode, bearing, speed, occupancy basis), that
the enrichment is stored as audit-only context in the engagement
snapshot, and that it is NEVER scored (mode alone never creates
PRE_COMBAT; bearing/speed never change a state).

Item 7 — ``resolve_winner`` dead code (verification M-7): wiring
``_prune`` through ``build_winner_preference`` was considered and
REJECTED — it would need exact per-team alive counts that no code
supplies, and synthesising them from wipe observations would
fabricate measured counts and change behaviour (single-team fight,
no wipe observed). The builders stay tested-but-unwired and the
stale hook-note docstring is corrected. These tests pin that
decision: no production caller, the docstring no longer claims the
wiring, and the live wipe-inference prune path still produces the
identical preferences.
"""

from __future__ import annotations

import ast
import json
import pathlib
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from app.core.event_bus import BaseEvent, EventRecord, EventBus, EventPriority
from app.services.director_service import DirectorService
from app.services.live_director import LiveDirector
from app.services.live_director.engagement import (
    EngagementState,
    EngagementTracker,
)
from app.services.live_director.payoff import (
    build_winner_preference,
    resolve_winner,
)
from app.services.live_director.rotation_intake import (
    BRIDGE_EVENT_TYPES,
    build_rotation_bridge,
    handle_rotation_record,
    translate_rotation_event,
)
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


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


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
    """LiveDirector + DirectorService + real bus, team A on PGM."""

    def __init__(self, phase: str = "mid_game", mode: str | None = None,
                 **live_kwargs):
        self.clock = FakeClock()
        self.phase = phase
        self.bus = _real_bus()
        self.director_service = DirectorService()
        if mode is not None:
            self.director_service._state["mode"] = mode
        self.live = LiveDirector(
            bus=self.bus,
            director_service=self.director_service,
            slot_resolver=StaticSlotResolver(SLOTS),
            phase_provider=lambda: self.phase,
            clock=self.clock,
            **live_kwargs,
        )
        self.live.start()

    async def publish(self, event_type: str, data: Dict[str, Any]) -> None:
        await self.bus.publish(
            event_type=event_type,
            data=data,
            priority=EventPriority.HIGH,
            source="test",
            match_id="m-1",
        )

    def pgm(self) -> Any:
        return self.director_service.snapshot()["active_camera"]

    def preview(self) -> Any:
        return self.director_service.snapshot()["preview"]


def _bridge_rig(rig: Rig):
    bridge = build_rotation_bridge(rig.live)
    rig.bus.subscribe(
        subscriber_id="phase35_w3_rotation_intake_test",
        callback=bridge,
        event_types=list(BRIDGE_EVENT_TYPES),
    )
    return bridge


def _rotation_detail(rig: Rig, team_id: str) -> Any:
    return rig.live.engagement_snapshot()["teams"][team_id]["rotation_detail"]


# ── Item 6: bridge forwards the full spec ─────────────────────────


class _RecordingDirector:
    """Stand-in for LiveDirector: records note_rotation calls."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def note_rotation(self, team_id, **kwargs):
        self.calls.append({"team_id": team_id, **kwargs})
        return None

    async def note_potential_fight(self, *args, **kwargs):  # pragma: no cover
        return None


async def test_bridge_forwards_every_enrichment_field():
    fake = _RecordingDirector()
    spec = await handle_rotation_record(
        "rotation_event",
        {
            "team_id": "team-b",
            "occupied_by": "team-c",
            "poi": "Pochinki",
            "occupancy_basis": "shared_current_area_poi",
            "rotation_mode": "vehicle",
            "bearing_deg": 90.0,
            "speed_px_s": 20.0,
        },
        fake,
    )
    assert spec is not None
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["team_id"] == "team-b"
    assert call["active"] is True
    assert call["occupied_by"] == "team-c"
    assert call["poi"] == "Pochinki"
    assert call["occupancy_basis"] == "shared_current_area_poi"
    assert call["rotation_mode"] == "vehicle"
    assert call["bearing_deg"] == 90.0
    assert call["speed_px_s"] == 20.0


async def test_bridge_callback_forwards_enrichment_over_event_record():
    fake = _RecordingDirector()
    bridge = build_rotation_bridge(fake)
    await bridge(EventRecord(event=BaseEvent(
        event_type="rotation_event",
        data={
            "team_id": "team-b",
            "rotation_mode": "on_foot",
            "bearing_deg": 270.0,
            "speed_px_s": 5.0,
        },
        match_id="m-1", source="test",
    )))
    assert bridge.stats["forwarded"] == 1
    assert bridge.stats["errors"] == 0
    assert fake.calls[0]["rotation_mode"] == "on_foot"
    assert fake.calls[0]["bearing_deg"] == 270.0
    assert fake.calls[0]["speed_px_s"] == 5.0
    assert fake.calls[0]["occupied_by"] is None


# ── Item 6: end-to-end probe — intake -> bridge -> director ──────


async def test_end_to_end_full_rotation_event_arrives_intact():
    rig = Rig()
    _bridge_rig(rig)
    assert rig.pgm() == 1
    await rig.publish(
        "rotation_event",
        {
            "team_id": "team-b",
            "occupied_by": "team-c",
            "poi": "Pochinki",
            "occupancy_basis": "shared_current_area_poi",
            "rotation_mode": "vehicle",
            "bearing_deg": 90.0,
            "speed_px_s": 20.0,
        },
    )
    # Occupancy still derives PRE_COMBAT and pre-positions PVW only.
    assert rig.live.engagement.state_of("team-b") == EngagementState.PRE_COMBAT
    preview = rig.preview()
    assert preview is not None and preview["team_id"] == "team-b"
    assert preview["slot"] == 2
    assert rig.pgm() == 1  # PGM never blind-cuts on a rotation
    # ...and EVERY field arrived intact at the director, none dropped.
    detail = _rotation_detail(rig, "team-b")
    assert detail is not None
    assert detail["occupied_by"] == "team-c"
    assert detail["poi"] == "Pochinki"
    assert detail["occupancy_basis"] == "shared_current_area_poi"
    assert detail["rotation_mode"] == "vehicle"
    assert detail["bearing_deg"] == 90.0
    assert detail["speed_px_s"] == 20.0
    assert detail["at"] == 1000.0
    # Screen-derived units only: no physical units anywhere.
    blob = json.dumps(detail)
    assert "km/h" not in blob and "km_h" not in blob
    assert "distance_m" not in blob and "speed_m_s" not in blob


async def test_end_to_end_plain_rotation_keeps_enrichment_without_precombat():
    rig = Rig()
    _bridge_rig(rig)
    await rig.publish(
        "rotation_event",
        {
            "team_id": "team-b",
            "rotation_mode": "on_foot",
            "bearing_deg": 270.0,
            "speed_px_s": 5.0,
        },
    )
    # Enrichment is never scored: vehicle/on-foot mode alone is
    # movement, not convergence — ROTATING, no PRE_COMBAT, no PGM cut.
    assert rig.live.engagement.state_of("team-b") == EngagementState.ROTATING
    assert rig.preview() is None
    assert rig.pgm() == 1
    detail = _rotation_detail(rig, "team-b")
    assert detail is not None
    assert detail["rotation_mode"] == "on_foot"
    assert detail["bearing_deg"] == 270.0
    assert detail["speed_px_s"] == 5.0
    assert "occupied_by" not in detail and "poi" not in detail


# ── Item 6: tracker-level enrichment handling ─────────────────────


def test_tracker_stores_enrichment_without_changing_state():
    clock = FakeClock()
    tracker = EngagementTracker(clock=clock)
    tracker.observe_phase("mid_game")
    tracker.note_rotation(
        "team-b", active=True, rotation_mode="vehicle",
        bearing_deg=90.0, speed_px_s=20.0,
    )
    assert tracker.state_of("team-b") == EngagementState.ROTATING
    detail = tracker.snapshot()["teams"]["team-b"]["rotation_detail"]
    assert detail == {
        "at": 1000.0, "rotation_mode": "vehicle",
        "bearing_deg": 90.0, "speed_px_s": 20.0,
    }
    # A bare rotation (no enrichment) stores no enrichment.
    tracker.note_rotation("team-c", active=True)
    assert tracker.snapshot()["teams"]["team-c"]["rotation_detail"] is None
    assert tracker.state_of("team-c") == EngagementState.ROTATING


def test_tracker_drops_invalid_enrichment_instead_of_storing_it():
    clock = FakeClock()
    tracker = EngagementTracker(clock=clock)
    tracker.observe_phase("mid_game")
    tracker.note_rotation(
        "team-b", active=True, rotation_mode="teleport",
        bearing_deg=float("nan"), speed_px_s=-3.0,
    )
    assert tracker.state_of("team-b") == EngagementState.ROTATING
    assert tracker.snapshot()["teams"]["team-b"]["rotation_detail"] is None
    # The translator applies the same drops at the bridge boundary.
    spec = translate_rotation_event({
        "team_id": "team-b", "rotation_mode": "flying",
        "bearing_deg": "not-a-number", "speed_px_s": -1.0,
    })
    assert spec is not None
    assert spec["rotation_mode"] is None
    assert spec["bearing_deg"] is None
    assert spec["speed_px_s"] is None


def test_tracker_clears_enrichment_on_rotation_end_and_on_staleness():
    clock = FakeClock()
    tracker = EngagementTracker(clock=clock)
    tracker.observe_phase("mid_game")
    tracker.note_rotation(
        "team-b", active=True, rotation_mode="vehicle",
        bearing_deg=90.0, speed_px_s=20.0,
    )
    assert tracker.snapshot()["teams"]["team-b"]["rotation_detail"] is not None
    tracker.note_rotation("team-b", active=False)
    assert tracker.snapshot()["teams"]["team-b"]["rotation_detail"] is None
    assert tracker.state_of("team-b") == EngagementState.IDLE

    tracker.note_rotation(
        "team-c", active=True, rotation_mode="vehicle", speed_px_s=20.0,
    )
    clock.advance(21)  # past the 20s rotating TTL
    tracker.tick()
    assert tracker.snapshot()["teams"]["team-c"]["rotation_detail"] is None
    assert tracker.state_of("team-c") == EngagementState.IDLE


# ── Item 7: decision pins ─────────────────────────────────────────


def _app_sources() -> Dict[str, pathlib.Path]:
    root = pathlib.Path(__file__).resolve().parents[2] / "app"
    return {str(p.relative_to(root)): p for p in root.rglob("*.py")}


def test_no_production_caller_of_resolve_winner_or_build_winner_preference():
    """AST proof: outside payoff.py itself, no production code calls
    either builder. The only in-module call is build_winner_preference
    -> resolve_winner. If a real per-team alive-count source ever
    lands, this test must be deliberately revisited (see HOOK STATUS)."""
    callers = []
    for rel, path in _app_sources().items():
        tree = ast.parse(path.read_text(), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in ("resolve_winner", "build_winner_preference"):
                    callers.append((rel, node.func.id, node.lineno))
    # Exact assertion: exactly one call site, inside payoff.py.
    assert len(callers) == 1
    assert callers[0][0] == "services/live_director/payoff.py"
    assert callers[0][1] == "resolve_winner"


def test_payoff_docstring_no_longer_claims_prune_wiring():
    payoff_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "app" / "services" / "live_director" / "payoff.py"
    )
    text = payoff_path.read_text()
    assert "HOOK NOTE" not in text
    assert "build_winner_preference`` is called from ``_prune" not in text
    assert "_team_alive_at_resolve`` does not exist yet" not in text
    assert "HOOK STATUS" in text
    assert "TESTED-BUT-UNWIRED" in text
    assert "synthetic alive" in text


def test_builders_still_work_on_exact_counts_only():
    pref = build_winner_preference(
        {"fight_id": "f1", "teams_involved": ["teamA", "teamB"]},
        {"teamA": 2, "teamB": 0},
        now=1000.0, ttl_s=60.0,
    )
    assert pref is not None and pref.preferred_team_id == "teamA"
    assert pref.is_active(1050.0) is True
    # Unknown counts stay ambiguous — never guessed.
    assert resolve_winner({"teamA": 1, "teamB": None}, ["teamA", "teamB"]) is None
    assert build_winner_preference(
        {"fight_id": "f1", "teams_involved": ["teamA", "teamB"]},
        {"teamA": 1, "teamB": 1}, now=1000.0,
    ) is None


async def test_live_prune_wipe_inference_preference_unchanged():
    """The live path still produces the identical preference WITHOUT
    the builders: sole unwiped team wins the next first-place tie."""
    rig = Rig(mode="full_auto")
    rig.director_service._state["active_camera"] = 4
    rig.live._last_known_slot = 4
    rig.live._scene_started_at = 900.0
    rig.live._eliminated_teams = {"team-a"}
    rig.live._fights["f1"] = {
        "fight_id": "f1", "teams": ["team-a", "team-c"],
        "intensity": 40.0, "is_clutch": False,
        "started_at": 900.0, "last_activity_at": 970.0,
    }
    rig.live._combat_heat["team-a"] = [(1000.0, 50.0)]
    rig.live._combat_heat["team-c"] = [(1000.0, 50.0)]
    entry = await rig.live.evaluate_now()
    assert entry is not None and entry["outcome"] == "switch"
    assert rig.director_service.snapshot()["active_camera"] == 3
    assert rig.live._payoff_preference is None  # one-shot: consumed


async def test_live_prune_single_team_fight_without_wipe_has_no_preference():
    """Edge the synthetic-count wiring would break: a single-team
    fight with no wipe observed must NOT gain a winner preference
    (resolve_winner on synthesised {team: 1} would wrongly give one)."""
    rig = Rig()
    rig.live._fights["f1"] = {
        "fight_id": "f1", "teams": ["team-b"],
        "intensity": 40.0, "is_clutch": False,
        "started_at": 900.0, "last_activity_at": 900.0,
    }
    rig.live._prune(1000.0)
    assert rig.live._payoff_preference is None
    assert "f1" not in rig.live._fights
