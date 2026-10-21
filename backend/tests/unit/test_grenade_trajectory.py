# backend/tests/unit/test_grenade_trajectory.py
"""Phase 23 (W4) — grenade trajectory estimation tests.

The honesty contract under test:
  * known endpoints -> one arc, estimated=True, method/basis labels set;
  * missing either endpoint -> NO record, ever;
  * victim-position explosion endpoint -> labelled proxy, never "observed";
  * mismatched / undeclared coordinate spaces -> NO record;
  * today's production UTILITY shape (frame-pixel flash, no thrower)
    -> NO record (counted as explosion_only).
"""

from __future__ import annotations

import math
from typing import Any, Dict

import pytest

from app.core.event_bus import BaseEvent, EventRecord
from app.services.grenade_trajectory import (
    GrenadeTrajectoryService,
    TrajectoryStore,
    estimate_arc,
    is_grenade_utility,
    is_grenade_weapon,
)


def _record(event_type: str, data: Dict[str, Any], match_id: str = "m-1") -> EventRecord:
    return EventRecord(
        event=BaseEvent(
            event_type=event_type, data=data, match_id=match_id, source="test"
        )
    )


def _service() -> GrenadeTrajectoryService:
    return GrenadeTrajectoryService(bus=None, store=TrajectoryStore())


# ── engine ────────────────────────────────────────────────────────


def test_engine_known_endpoints_produce_labelled_arc():
    arc = estimate_arc((0.0, 0.0), (30.0, 40.0), coordinate_space="test_space")
    assert arc is not None
    assert arc["estimated"] is True
    assert arc["method"] == "endpoint_arc_estimate"
    assert arc["confidence_basis"] == "endpoints_only"
    assert arc["distance"] == 50.0  # 3-4-5 triangle
    assert arc["distance_units"] == "coordinate_units"
    # 45-degree assumed launch: apex = R * tan(45) / 4 = R / 4.
    assert arc["apex_height"] == 12.5
    assert arc["launch_angle_basis"] == "assumed_default_45deg"
    # No calibrated scale -> no invented seconds or m/s.
    assert arc["flight_time_s"] is None
    assert arc["launch_speed_mps"] is None
    points = arc["arc_points"]
    assert (points[0]["x"], points[0]["y"]) == (0.0, 0.0)
    assert (points[-1]["x"], points[-1]["y"]) == (30.0, 40.0)
    assert points[0]["h"] == 0.0 and points[-1]["h"] == 0.0
    assert max(p["h"] for p in points) == pytest.approx(12.5)


def test_engine_calibrated_scale_reports_si_values():
    arc = estimate_arc(
        (0.0, 0.0), (30.0, 40.0),
        coordinate_space="minimap_region_pixels", units_per_meter=10.0,
    )
    assert arc is not None
    assert arc["distance"] == 5.0
    assert arc["distance_units"] == "meters"
    assert arc["apex_height"] == 1.25
    # v = sqrt(R g / sin(2*45deg)) = sqrt(5 * 9.8); T = 2 v sin(45) / g.
    expected_t = 2 * math.sqrt(49.0) * math.sin(math.radians(45)) / 9.8
    assert arc["flight_time_s"] == pytest.approx(expected_t, abs=0.01)


def test_engine_zero_range_is_no_arc():
    assert estimate_arc((5, 5), (5, 5), coordinate_space="s") is None
    assert estimate_arc(None, (5, 5), coordinate_space="s") is None


# ── weapon / utility matchers ─────────────────────────────────────


@pytest.mark.parametrize(
    "weapon", ["frag_grenade", "Frag Grenade", "FRAG_GRENADE", "grenade", "nade"]
)
def test_grenade_weapons_match(weapon):
    assert is_grenade_weapon(weapon)


@pytest.mark.parametrize(
    "weapon",
    ["M416", "Kar98K", "molotov", "smoke_grenade", "stun_grenade",
     "Unknown", "", None],
)
def test_non_frag_weapons_do_not_match(weapon):
    assert not is_grenade_weapon(weapon)


def test_grenade_utility_matcher():
    assert is_grenade_utility("grenade")
    assert is_grenade_utility("frag_grenade")
    assert is_grenade_utility("explosion")
    assert not is_grenade_utility("smoke")
    assert not is_grenade_utility("molotov")


# ── store label enforcement ───────────────────────────────────────


def test_store_refuses_unlabelled_or_endpointless_records():
    store = TrajectoryStore()
    with pytest.raises(ValueError):
        store.add({"match_id": "m", "thrower": {"position": [0, 0]},
                   "explosion": {"position": [1, 1]}})
    with pytest.raises(ValueError):
        store.add({"match_id": "m", "estimated": True,
                   "thrower": {"position": [0, 0]}, "explosion": {}})


# ── consumer: kill path ───────────────────────────────────────────

_KILL_BASE = {
    "id": "kill-1",
    "timestamp": 1_700_000_000.0,
    "killer": "Alpha",
    "killerTeam": "T1",
    "victim": "Bravo",
    "victimTeam": "T2",
    "weapon": "frag_grenade",
    "isKnock": False,
}


@pytest.mark.asyncio
async def test_kill_with_both_endpoints_produces_estimated_record():
    svc = _service()
    data = {
        **_KILL_BASE,
        "thrower_position": [100.0, 100.0],
        "explosion_position": [130.0, 140.0],
        "coordinate_space": "minimap_region_pixels",
    }
    await svc.handle_record(_record("kill_event", data))
    records = svc.store.list("m-1")
    assert len(records) == 1
    rec = records[0]
    assert rec["estimated"] is True
    assert rec["method"] == "endpoint_arc_estimate"
    assert rec["confidence_basis"] == "endpoints_only"
    assert rec["source"] == "kill_event.weapon"
    assert rec["thrower"]["player"] == "Alpha"
    assert rec["thrower"]["position_basis"] == "event_field"
    assert rec["explosion"]["position_basis"] == "observed_event_field"
    assert rec["distance"] == 50.0
    assert rec["arc_points"][0]["x"] == 100.0


@pytest.mark.asyncio
async def test_kill_victim_position_is_labelled_proxy():
    svc = _service()
    data = {
        **_KILL_BASE,
        "id": "kill-2",
        "thrower_position": [0.0, 0.0],
        "victim_position": [30.0, 40.0],
        "coordinate_space": "minimap_region_pixels",
    }
    await svc.handle_record(_record("knock_event", data))
    records = svc.store.list("m-1")
    assert len(records) == 1
    assert records[0]["explosion"]["position_basis"].startswith(
        "victim_position_proxy"
    )
    assert records[0]["victim"]["player"] == "Bravo"


@pytest.mark.asyncio
async def test_kill_missing_thrower_produces_nothing():
    svc = _service()
    data = {
        **_KILL_BASE,
        "id": "kill-3",
        "explosion_position": [130.0, 140.0],
        "coordinate_space": "minimap_region_pixels",
    }
    await svc.handle_record(_record("kill_event", data))
    assert svc.store.list("m-1") == []
    assert svc.stats.explosion_only == 1
    assert svc.stats.skipped_missing_endpoint == 1


@pytest.mark.asyncio
async def test_kill_non_grenade_weapon_produces_nothing():
    svc = _service()
    data = {
        **_KILL_BASE,
        "id": "kill-4",
        "weapon": "M416",
        "thrower_position": [0.0, 0.0],
        "explosion_position": [30.0, 40.0],
        "coordinate_space": "minimap_region_pixels",
    }
    await svc.handle_record(_record("kill_event", data))
    assert svc.store.list("m-1") == []


@pytest.mark.asyncio
async def test_kill_mismatched_spaces_produce_nothing():
    svc = _service()
    data = {
        **_KILL_BASE,
        "id": "kill-5",
        "thrower_position": [0.0, 0.0],
        "thrower_position_space": "frame_pixels",
        "explosion_position": [30.0, 40.0],
        "explosion_position_space": "minimap_region_pixels",
    }
    await svc.handle_record(_record("kill_event", data))
    assert svc.store.list("m-1") == []
    assert svc.stats.skipped_space == 1


@pytest.mark.asyncio
async def test_kill_undeclared_space_produces_nothing():
    svc = _service()
    data = {
        **_KILL_BASE,
        "id": "kill-6",
        "thrower_position": [0.0, 0.0],
        "explosion_position": [30.0, 40.0],
    }
    await svc.handle_record(_record("kill_event", data))
    assert svc.store.list("m-1") == []
    assert svc.stats.skipped_space == 1


@pytest.mark.asyncio
async def test_duplicate_event_produces_one_record():
    svc = _service()
    data = {
        **_KILL_BASE,
        "thrower_position": [0.0, 0.0],
        "explosion_position": [30.0, 40.0],
        "coordinate_space": "minimap_region_pixels",
    }
    await svc.handle_record(_record("kill_event", data))
    await svc.handle_record(_record("kill_event", dict(data)))
    assert len(svc.store.list("m-1")) == 1


# ── consumer: position-tracking fallback ──────────────────────────


@pytest.mark.asyncio
async def test_tracked_positions_pair_up_within_freshness_window():
    svc = _service()
    await svc.handle_record(
        _record(
            "position_update",
            {
                "timestamp": 1_700_000_000.0,
                "coordinate_space": "minimap_region_pixels",
                "positions_by_player": {
                    "Alpha": [0.0, 0.0],
                    "Bravo": [30.0, 40.0],
                },
            },
        )
    )
    data = {**_KILL_BASE, "id": "kill-7", "timestamp": 1_700_000_005.0}
    await svc.handle_record(_record("kill_event", data))
    records = svc.store.list("m-1")
    assert len(records) == 1
    assert records[0]["thrower"]["position_basis"] == (
        "last_known_position_tracking"
    )
    assert records[0]["explosion"]["position_basis"] == (
        "victim_position_proxy_last_known_tracking"
    )


@pytest.mark.asyncio
async def test_stale_tracked_positions_produce_nothing():
    svc = _service()
    await svc.handle_record(
        _record(
            "position_update",
            {
                "timestamp": 1_700_000_000.0,
                "coordinate_space": "minimap_region_pixels",
                "positions_by_player": {
                    "Alpha": [0.0, 0.0],
                    "Bravo": [30.0, 40.0],
                },
            },
        )
    )
    # 10 minutes later: positions are history, not endpoints.
    data = {**_KILL_BASE, "id": "kill-8", "timestamp": 1_700_000_600.0}
    await svc.handle_record(_record("kill_event", data))
    assert svc.store.list("m-1") == []


# ── consumer: utility path ────────────────────────────────────────


@pytest.mark.asyncio
async def test_production_utility_shape_produces_nothing():
    """Today's vision runtime: grenade flash at frame pixels, thrower
    unknown -> explosion-only observation, NO trajectory record."""
    svc = _service()
    await svc.handle_record(
        _record(
            "utility_event",
            {
                "id": "util-1",
                "timestamp": 1_700_000_000.0,
                "utility_type": "grenade",
                "position": [640.0, 360.0],
                "position_space": "frame_pixels",
                "detector": "utility_detector",
            },
        )
    )
    assert svc.store.list("m-1") == []
    assert svc.stats.explosion_only == 1


@pytest.mark.asyncio
async def test_utility_with_thrower_endpoint_produces_record():
    svc = _service()
    await svc.handle_record(
        _record(
            "utility_event",
            {
                "id": "util-2",
                "timestamp": 1_700_000_000.0,
                "utility_type": "grenade",
                "position": [130.0, 140.0],
                "thrower_position": [100.0, 100.0],
                "thrower": "Alpha",
                "coordinate_space": "minimap_region_pixels",
            },
        )
    )
    records = svc.store.list("m-1")
    assert len(records) == 1
    assert records[0]["source"] == "utility_event"
    assert records[0]["estimated"] is True


@pytest.mark.asyncio
async def test_smoke_utility_produces_nothing():
    svc = _service()
    await svc.handle_record(
        _record(
            "utility_event",
            {
                "id": "util-3",
                "timestamp": 1_700_000_000.0,
                "utility_type": "smoke",
                "position": [130.0, 140.0],
                "thrower_position": [100.0, 100.0],
                "coordinate_space": "minimap_region_pixels",
            },
        )
    )
    assert svc.store.list("m-1") == []


# ── API ───────────────────────────────────────────────────────────


def test_api_returns_records_with_estimated_label():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.analysis.router import router as analysis_router
    from app.api.deps import TokenPayload, get_current_user
    from app.services.grenade_trajectory import trajectory_store

    trajectory_store.clear()
    svc = GrenadeTrajectoryService(bus=None, store=trajectory_store)
    import asyncio

    data = {
        **_KILL_BASE,
        "id": "kill-api-1",
        "thrower_position": [0.0, 0.0],
        "explosion_position": [30.0, 40.0],
        "coordinate_space": "normalized_0_1",
    }
    asyncio.run(
        svc.handle_record(_record("kill_event", data, match_id="m-api"))
    )

    app = FastAPI()
    app.include_router(analysis_router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: TokenPayload(
        user_id="1", username="tester", role="operator"
    )
    client = TestClient(app)
    try:
        resp = client.get(
            "/api/v1/analysis/grenade-trajectories", params={"match_id": "m-api"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["estimated"] is True
        assert "ESTIMATED" in body["meta"]["coverage"]

        empty = client.get(
            "/api/v1/analysis/grenade-trajectories",
            params={"match_id": "no-such-match"},
        )
        assert empty.status_code == 200
        assert empty.json()["data"] == []
    finally:
        trajectory_store.clear()
