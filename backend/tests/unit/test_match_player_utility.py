# backend/tests/unit/test_match_player_utility.py
"""Unit tests for the Phase 21/T5b healing + grenades stats surface.

  * the stats model carries healing + grenades_used columns
  * player_stats_from_utility() copies the REAL agent counters
    (utility_tracker.PlayerUtilityStats, team_tracker-style dicts)
  * MatchService.record_player_stats() persists both fields and also
    accepts the agent field names (healing_done / grenades)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from app.agents.team_agents.utility_tracker import PlayerUtilityStats
from app.services.match_service import (
    MatchPlayerStats,
    MatchService,
    player_stats_from_utility,
)


def test_stats_model_has_healing_and_grenades() -> None:
    columns = MatchPlayerStats.__table__.columns
    assert "healing" in columns and "grenades_used" in columns
    # Python-side defaults of 0 (applied at insert, like the other
    # counters on this model): an unrecorded counter reads as zero.
    assert columns["healing"].default.arg == 0
    assert columns["grenades_used"].default.arg == 0


def test_from_utility_tracker_stats_object() -> None:
    stats = PlayerUtilityStats(
        player_id="p1",
        player_name="Scout",
        grenades_used=7,
        healing_done=230,
    )
    line = player_stats_from_utility("p1", "t1", stats)
    assert line["healing"] == 230
    assert line["grenades_used"] == 7
    assert line["player_id"] == "p1" and line["team_id"] == "t1"


def test_from_team_tracker_style_dict() -> None:
    # team_tracker player records use `grenades` / `healing_done` keys.
    line = player_stats_from_utility(
        "p2", "t2", {"healing_done": 90, "grenades": 4}, kills=9
    )
    assert line["healing"] == 90
    assert line["grenades_used"] == 4
    assert line["kills"] == 9


def test_from_utility_missing_counters_stay_zero() -> None:
    line = player_stats_from_utility("p3", "t3", {})
    assert line["healing"] == 0 and line["grenades_used"] == 0


async def test_record_player_stats_persists_healing_and_grenades() -> None:
    session = AsyncMock()
    session.add = Mock()
    svc = MatchService(session)
    svc._get_match_or_raise = AsyncMock(return_value=Mock(id="m1"))

    created = await svc.record_player_stats(
        "m1",
        [
            {
                "player_id": "p1",
                "team_id": "t1",
                "kills": 5,
                "healing": 180,
                "grenades_used": 6,
            },
            {
                "player_id": "p2",
                "team_id": "t1",
                # agent field names are accepted too
                "healing_done": 75,
                "grenades": 3,
            },
        ],
    )

    assert created[0].healing == 180 and created[0].grenades_used == 6
    assert created[1].healing == 75 and created[1].grenades_used == 3
    added = [call.args[0] for call in session.add.call_args_list]
    assert [a.player_id for a in added] == ["p1", "p2"]
