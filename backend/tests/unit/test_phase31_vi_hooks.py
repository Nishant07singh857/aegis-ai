# backend/tests/unit/test_phase31_vi_hooks.py
"""Phase 31 (coordinator integration) — VisionIntelligence hooks.

1. Clutch roster hook (B hook D): _on_team_status feeds the
   FightDetector RAW per-slot statuses, never the combined
   alive+knocked count (a knocked-only team must not read as a
   lone survivor).
2. Center-hold producer (H4a): a new zone reveal emits a
   center_hold intel_alert only when a NORMALIZED position resolves
   to a compound POI; minimap-region pixels never name a POI.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock, Mock

import pytest

from app.agents.base_agent import AgentConfig
from app.agents.fight_agents.fight_detector import FightDetector
from app.core.event_bus import BaseEvent, EventRecord
from app.services.vision_intelligence.attribution import (
    StaticSlotResolver,
    TeamAttribution,
)
from app.services.vision_intelligence.service import (
    VisionIntelligenceService,
)

pytestmark = pytest.mark.unit

BASE = 1_700_000_000.0


def _record(event_type: str, data: Dict[str, Any]) -> EventRecord:
    return EventRecord(
        event=BaseEvent(
            event_type=event_type, data=data, match_id="m-1", source="test"
        )
    )


def _capturing_bus():
    bus = Mock()
    bus.published: List = []

    async def _publish(event_type=None, data=None, **kwargs):
        bus.published.append((event_type, data))
        return "evt"

    bus.publish = AsyncMock(side_effect=_publish)
    bus.subscribe = Mock(return_value="sub")
    bus.unsubscribe = Mock(return_value=True)
    bus._running = True
    return bus


def _service(bus=None):
    bus = bus or _capturing_bus()
    resolver = StaticSlotResolver(
        {3: TeamAttribution(slot_number=3, team_id="team-a", team_name="Alpha")}
    )
    detector = FightDetector(
        config=AgentConfig(agent_id="fd", agent_type="fight_detector"),
        event_bus=_capturing_bus(),
    )
    return VisionIntelligenceService(
        bus=bus, slot_resolver=resolver, fight_detector=detector,
        clock=lambda: BASE,
    )


def _intel_alerts(bus, kind=None):
    return [d for t, d in bus.published
            if t == "intel_alert" and (kind is None or d.get("kind") == kind)]


async def test_team_status_feeds_detector_raw_slots_not_combined_count() -> None:
    svc = _service()
    # Combined count says 3, but the slots say exactly 1 ALIVE.
    await svc.handle_record(_record("team_status_update", {
        "observer_slot": 3,
        "slots": ["alive", "dead", "dead", "dead"],
        "alive_count": 3,
        "timestamp": BASE,
    }))
    assert svc.fight_detector._team_alive_exact["team-a"] == 1


async def test_team_status_unknown_slot_fails_closed() -> None:
    svc = _service()
    await svc.handle_record(_record("team_status_update", {
        "observer_slot": 3,
        "slots": ["alive", "dead", "dead", "unknown"],
        "alive_count": 1,
        "timestamp": BASE,
    }))
    assert svc.fight_detector._team_alive_exact["team-a"] is None


async def test_center_hold_producer_fires_on_compound_poi() -> None:
    bus = _capturing_bus()
    svc = _service(bus=bus)
    await svc.handle_record(_record("position_update", {
        "observer_slot": 3,
        "player_positions": {"teammate_0": [100, 100],
                             "teammate_1": [104, 100]},
        "timestamp": BASE,
    }))
    # Normalized position on Erangel's Georgopol Containers (compound);
    # stationary 100s+ (rotation tracker state seeded verbatim).
    svc.derivations._position_context["team-a"] = {
        "x": 0.14, "y": 0.42, "map_id": "erangel",
        "coordinate_space": "normalized_map",
    }
    svc.derivations.rotations.teams["team-a"] = {
        "active_rotation": None, "last_move_ts": BASE - 100,
    }
    await svc.handle_record(_record("zone_event", {
        "zone_phase": 3, "safe_center": [100, 100], "safe_radius": 50,
        "phase_source": "minimap_geometry", "timestamp": BASE + 5,
    }))
    holds = _intel_alerts(bus, "center_hold")
    assert len(holds) == 1
    assert holds[0]["team_id"] == "team-a"
    assert holds[0]["poi_type"] == "compound"


async def test_center_hold_producer_silent_on_minimap_pixels() -> None:
    bus = _capturing_bus()
    svc = _service(bus=bus)
    await svc.handle_record(_record("position_update", {
        "observer_slot": 3,
        "player_positions": {"teammate_0": [100, 100],
                             "teammate_1": [104, 100]},
        "timestamp": BASE,
    }))
    # Same geometry, but the position is minimap-region pixels:
    # POI naming must refuse, so no hold may be claimed.
    svc.derivations._position_context["team-a"] = {
        "x": 0.14, "y": 0.42, "map_id": "erangel",
        "coordinate_space": "minimap_region_pixels",
    }
    svc.derivations.rotations.teams["team-a"] = {
        "active_rotation": None, "last_move_ts": BASE - 100,
    }
    await svc.handle_record(_record("zone_event", {
        "zone_phase": 3, "safe_center": [100, 100], "safe_radius": 50,
        "phase_source": "minimap_geometry", "timestamp": BASE + 5,
    }))
    assert _intel_alerts(bus, "center_hold") == []
