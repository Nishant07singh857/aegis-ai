# backend/tests/unit/test_phase37_fixup_l3_replay_transitions.py
"""
Phase 37 fix-up — L-3 SCOPED (ledger §4).

The Bible hard-cut rule (§3, §9) governs live camera switching.
``replay_director.py`` builds replay sequences shown on air via the
replay queue, so its transitions are "cut" only. Montage/documentary
generators are deliberately left alone (post-produced packages, outside
the observed evidence) — see the ledger §4 wording, recorded there.
"""

import pytest
from unittest.mock import AsyncMock, Mock

from app.agents.base_agent import AgentConfig
from app.agents.highlight_agents.replay_director import (
    ReplayAngle,
    ReplayDirector,
    ReplayShot,
)


@pytest.mark.unit
class TestReplayDirectorTransitionsCutOnly:
    """Every shot a created replay sequence carries uses a hard cut."""

    @pytest.fixture
    def director(self):
        bus = AsyncMock()
        bus.publish = AsyncMock(return_value="test-event-id")
        bus.subscribe = Mock(return_value="subscriber-id")
        bus.unsubscribe = Mock(return_value=True)
        return ReplayDirector(
            config=AgentConfig(agent_id="replay_director", agent_type="replay_director"),
            event_bus=bus,
        )

    def test_all_created_shots_use_hard_cut(self, director):
        """Intro + action + outro shots are all "cut".

        Pre-fix the intro/outro were "fade" and action shots after the
        first were "dissolve" — this test covers all three creation
        sites (replay_director.py :195/:221/:235).
        """
        sequence = director.create_replay(
            clip_data={
                "clip_id": "clip-001",
                "title": "Test replay",
                "category": "multi_kill",
                "primary_player": "player_1",
                "primary_team": "team_001",
                "duration_seconds": 10,
            },
            available_angles=[
                ReplayAngle.PLAYER_POV,
                ReplayAngle.FREECAM,
                ReplayAngle.SPLIT_SCREEN,
            ],
        )

        shot_ids = [s.shot_id for s in sequence.shots]
        assert shot_ids == [
            f"{sequence.sequence_id}_intro",
            f"{sequence.sequence_id}_action_0",
            f"{sequence.sequence_id}_action_1",
            f"{sequence.sequence_id}_action_2",
            f"{sequence.sequence_id}_outro",
        ]
        assert all(s.transition == "cut" for s in sequence.shots), [
            (s.shot_id, s.transition) for s in sequence.shots
        ]

    def test_replay_shot_default_transition_is_cut(self):
        assert ReplayShot(
            shot_id="x", angle=ReplayAngle.PLAYER_POV
        ).transition == "cut"
