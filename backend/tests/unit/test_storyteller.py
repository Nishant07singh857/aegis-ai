# backend/tests/unit/test_storyteller.py
"""
Unit tests for the Phase 19 AI storyteller (W1 — backend).

Covers:
  * All 7 story types generate from REAL (injected) data.
  * Missing data -> None for every type (zero fake data — nothing invented).
  * Unknown stats render "—", never a fabricated number.
  * generate_stories dispatch: unknown types ignored, data-less types skipped.
  * Role gating: trigger/dismiss require director/admin (403 for everyone
    else); reads allow director/admin/operator.

No network, no DB, no real credentials — the agent is driven through an
in-memory StoryDataProvider.
"""

from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from app.agents.base_agent import AgentConfig
from app.agents.narrative_agents.storyteller import (
    StoryDataProvider,
    StorytellerAgent,
    StoryType,
)
from app.api.v1.stories.router import require_story_control, require_story_reader
from app.core.security import TokenPayload


# --------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------- #


def _principal(role: str = "viewer", **kwargs) -> TokenPayload:
    kwargs.setdefault("user_id", "u-1")
    kwargs.setdefault("username", f"test-{role}")
    return TokenPayload(role=role, **kwargs)


class FakeProvider(StoryDataProvider):
    """In-memory provider holding REAL data (no fabrication at runtime)."""

    TEAMS = {
        "team-soul": {"id": "team-soul", "name": "Soul", "tag": "SOUL"},
        "team-xspark": {"id": "team-xspark", "name": "XSpark", "tag": "XSP"},
        "team-godl": {"id": "team-godl", "name": "GodLike", "tag": "GODL"},
    }
    PLAYERS = {
        "p-scout": {
            "id": "p-scout", "name": "Scout", "ign": "Scout",
            "team_id": "team-soul", "team_name": "Soul",
        },
        "p-ninja": {
            "id": "p-ninja", "name": "NinjaBoy", "ign": "NinjaBoy",
            "team_id": "team-xspark", "team_name": "XSpark",
        },
    }

    async def get_team(self, team_id):
        return self.TEAMS.get(team_id)

    async def get_player(self, player_id):
        return self.PLAYERS.get(player_id)

    async def get_head_to_head(self, a_id, b_id, kind="team"):
        if kind == "team":
            return {
                "entity_a_name": "Soul", "entity_b_name": "XSpark",
                "wins_a": 8, "wins_b": 4, "total": 12,
                "last_3": ["a", "b", "a"],
                "key_stat": "120 total kills in 12 fights",
            }
        return {
            "entity_a_name": "Scout", "entity_b_name": "NinjaBoy",
            "wins_a": 7, "wins_b": 3, "total": 10,
            "last_3": ["a", "a", "b"],
            "key_stat": "Scout ne NinjaBoy ko 7 baar finish kiya",
        }

    async def get_player_stats(self, player_id):
        stats = {
            "p-scout": {
                "player_id": "p-scout", "name": "Scout", "ign": "Scout",
                "finishes": 54, "avg_damage": 962.7, "rating": None, "clutches": 3,
            },
            "p-ninja": {
                "player_id": "p-ninja", "name": "NinjaBoy", "ign": "NinjaBoy",
                "finishes": 48, "avg_damage": 810.2, "rating": None, "clutches": 1,
            },
        }
        return stats.get(player_id)

    async def get_team_match_results(self, team_id, limit=5, tournament_id=None):
        if team_id != "team-soul":
            return None
        return [
            {"match_id": "m5", "placement": 1, "won": True, "kills": 12},
            {"match_id": "m4", "placement": 2, "won": False, "kills": 9},
            {"match_id": "m3", "placement": 4, "won": False, "kills": 7},
            {"match_id": "m2", "placement": 1, "won": True, "kills": 11},
            {"match_id": "m1", "placement": 1, "won": True, "kills": 14},
        ][:limit]

    async def get_live_teams(self, match_id):
        return [
            {"team_id": "team-soul", "team_name": "Soul",
             "alive_count": 4, "kills": 9, "in_zone": True},
            {"team_id": "team-xspark", "team_name": "XSpark",
             "alive_count": 3, "kills": 7, "in_zone": True},
            {"team_id": "team-godl", "team_name": "GodLike",
             "alive_count": 2, "kills": 5, "in_zone": False},
        ]

    async def get_previous_win_prob(self, match_id):
        return {"team-soul": 29.0, "team-xspark": 30.0, "team-godl": 20.0}

    async def get_zone_context(self, match_id):
        return {
            "phase": 4,
            "favored_team_id": "team-soul",
            "favored_team_name": "Soul",
            "similar_zones": 5,
            "top3_count": 3,
            "map_name": "Erangel",
        }

    async def get_milestones(self, player_id):
        if player_id != "p-scout":
            return []
        return [{
            "kind": "finish", "player_name": "Scout",
            "current": 49, "target": 50, "remaining": 1,
        }]

    async def get_comeback_context(self, team_id, tournament_id=None):
        if team_id != "team-xspark":
            return None
        return {
            "team_name": "XSpark",
            "from_placement": 14, "to_placement": 3,
            "jump": 11, "matches_span": 2,
        }


class EmptyProvider(StoryDataProvider):
    """Every data call misses — every story must be skipped, never faked."""

    async def get_team(self, team_id):
        return None

    async def get_player(self, player_id):
        return None

    async def get_head_to_head(self, a_id, b_id, kind="team"):
        return None

    async def get_player_stats(self, player_id):
        return None

    async def get_team_match_results(self, team_id, limit=5, tournament_id=None):
        return None

    async def get_live_teams(self, match_id):
        return None

    async def get_previous_win_prob(self, match_id):
        return None

    async def get_zone_context(self, match_id):
        return None

    async def get_milestones(self, player_id):
        return []

    async def get_comeback_context(self, team_id, tournament_id=None):
        return None


@pytest.fixture()
def agent() -> StorytellerAgent:
    return StorytellerAgent(
        AgentConfig(agent_type="storyteller"), event_bus=None, provider=FakeProvider()
    )


@pytest.fixture()
def empty_agent() -> StorytellerAgent:
    return StorytellerAgent(
        AgentConfig(agent_type="storyteller"), event_bus=None, provider=EmptyProvider()
    )


# --------------------------------------------------------------------- #
# 1-7: every story type generates from real data
# --------------------------------------------------------------------- #


@pytest.mark.unit
class TestStoryGeneration:
    async def test_head_to_head_team(self, agent):
        story = await agent.generate_head_to_head("team-soul", "team-xspark")
        assert story is not None
        assert story["type"] == StoryType.HEAD_TO_HEAD.value
        assert "Soul" in story["body"] and "XSpark" in story["body"]
        assert "12" in story["body"]  # real encounter count
        assert story["data"]["entity_a"]["wins"] == 8
        assert story["data"]["total_encounters"] == 12
        assert story["confidence"] > 0.5

    async def test_head_to_head_player(self, agent):
        story = await agent.generate_head_to_head("p-scout", "p-ninja", kind="player")
        assert story is not None
        assert "Scout" in story["title"] and "NinjaBoy" in story["title"]
        assert story["data"]["entity_a"]["wins"] == 7

    async def test_win_probability_with_delta(self, agent):
        story = await agent.generate_win_probability("match-1")
        assert story is not None
        assert story["type"] == StoryType.WIN_PROBABILITY.value
        probs = {p["team_id"]: p for p in story["data"]["probabilities"]}
        # deltas computed against the previous calculation, not invented
        assert probs["team-soul"]["delta"] == pytest.approx(
            probs["team-soul"]["win_probability"] - 29.0, abs=0.2
        )
        total = sum(p["win_probability"] for p in story["data"]["probabilities"])
        assert total == pytest.approx(100.0, abs=1.0)
        assert "pichhle" in story["body"]  # Hinglish delta narration

    async def test_player_comparison(self, agent):
        story = await agent.generate_player_comparison("p-scout", "p-ninja")
        assert story is not None
        assert story["type"] == StoryType.PLAYER_COMPARISON.value
        a, b = story["data"]["player_a"], story["data"]["player_b"]
        assert (a["finishes"], b["finishes"]) == (54, 48)
        assert (a["clutches"], b["clutches"]) == (3, 1)
        # rating has no DB source -> must render "—", never a fabricated number
        assert a["rating"] is None and b["rating"] is None
        assert "—" in story["body"]

    async def test_team_form(self, agent):
        story = await agent.generate_team_form("team-soul")
        assert story is not None
        assert story["type"] == StoryType.TEAM_FORM.value
        assert story["data"]["results"] == ["W", "L", "L", "W", "W"]
        assert story["data"]["wins"] == 3
        assert "🔥" in story["body"]

    async def test_zone_advantage(self, agent):
        story = await agent.generate_zone_advantage("match-1")
        assert story is not None
        assert story["type"] == StoryType.ZONE_ADVANTAGE.value
        assert story["data"]["favored_team_name"] == "Soul"
        assert story["data"]["top3_count"] == 3
        assert "top-3" in story["body"]

    async def test_milestone(self, agent):
        story = await agent.generate_milestone("p-scout")
        assert story is not None
        assert story["type"] == StoryType.MILESTONE.value
        assert story["data"]["remaining"] == 1
        assert story["data"]["target"] == 50
        assert "1 aur finish" in story["body"]

    async def test_comeback(self, agent):
        story = await agent.generate_comeback("team-xspark")
        assert story is not None
        assert story["type"] == StoryType.COMEBACK.value
        assert story["data"]["jump"] == 11
        assert "14th se" in story["body"] and "3rd pe" in story["body"]

    async def test_story_envelope_shape(self, agent):
        story = await agent.generate_milestone("p-scout")
        assert set(story) == {
            "type", "title", "body", "data", "confidence", "generated_at",
        }
        assert story["generated_at"] <= time.time()
        assert 0.0 <= story["confidence"] <= 1.0


# --------------------------------------------------------------------- #
# Missing data -> None (ZERO fake data)
# --------------------------------------------------------------------- #


@pytest.mark.unit
class TestMissingDataSkipped:
    async def test_head_to_head_missing(self, empty_agent):
        assert await empty_agent.generate_head_to_head("a", "b") is None

    async def test_head_to_head_same_entity(self, agent):
        assert await agent.generate_head_to_head("x", "x") is None

    async def test_win_probability_missing(self, empty_agent):
        assert await empty_agent.generate_win_probability("match-1") is None

    async def test_player_comparison_missing(self, empty_agent):
        assert await empty_agent.generate_player_comparison("p1", "p2") is None

    async def test_player_comparison_one_side_missing(self, agent):
        # p-ninja exists but "ghost" does not -> skip, don't half-fabricate
        assert await agent.generate_player_comparison("p-ninja", "ghost") is None

    async def test_team_form_missing(self, empty_agent):
        assert await empty_agent.generate_team_form("team-x") is None

    async def test_team_form_unknown_team(self, agent):
        assert await agent.generate_team_form("team-ghost") is None

    async def test_zone_advantage_missing(self, empty_agent):
        assert await empty_agent.generate_zone_advantage("match-1") is None

    async def test_milestone_missing(self, empty_agent):
        assert await empty_agent.generate_milestone("p-scout") is None

    async def test_milestone_not_approaching(self, agent):
        # p-ninja has no approaching milestone -> skip, don't invent one
        assert await agent.generate_milestone("p-ninja") is None

    async def test_comeback_missing(self, empty_agent):
        assert await empty_agent.generate_comeback("team-x") is None

    async def test_comeback_no_significant_jump(self, agent):
        assert await agent.generate_comeback("team-soul") is None


# --------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------- #


@pytest.mark.unit
class TestDispatch:
    async def test_generate_stories_all_types(self, agent):
        stories = await agent.generate_stories(
            "match-1",
            [t.value for t in StoryType],
            context={
                "entity_a_id": "team-soul",
                "entity_b_id": "team-xspark",
                "kind": "team",
                "player_a_id": "p-scout",
                "player_b_id": "p-ninja",
                "team_id": "team-soul",
                "player_id": "p-scout",
            },
        )
        types = {s["type"] for s in stories}
        # comeback needs team-xspark context; milestone needs p-scout — both
        # present, but comeback context here targets team-soul (no jump) so
        # it is honestly skipped.
        assert StoryType.HEAD_TO_HEAD.value in types
        assert StoryType.WIN_PROBABILITY.value in types
        assert StoryType.PLAYER_COMPARISON.value in types
        assert StoryType.TEAM_FORM.value in types
        assert StoryType.ZONE_ADVANTAGE.value in types
        assert StoryType.MILESTONE.value in types
        assert StoryType.COMEBACK.value not in types  # no jump for team-soul

    async def test_unknown_type_ignored(self, agent):
        stories = await agent.generate_stories(
            "match-1", ["audience_poll", "not_a_type"], context={}
        )
        assert stories == []

    async def test_empty_provider_generates_nothing(self, empty_agent):
        stories = await empty_agent.generate_stories(
            "match-1", [t.value for t in StoryType], context={"team_id": "x"}
        )
        assert stories == []


# --------------------------------------------------------------------- #
# Role gating — trigger/dismiss are director/admin ONLY
# --------------------------------------------------------------------- #


@pytest.mark.unit
class TestStoryRoles:
    async def test_trigger_allows_director(self):
        user = await require_story_control(user=_principal("director"))
        assert user.username == "test-director"

    async def test_trigger_allows_admin(self):
        user = await require_story_control(user=_principal("admin"))
        assert user.username == "test-admin"

    @pytest.mark.parametrize("role", ["operator", "spotter", "observer", "viewer"])
    async def test_trigger_rejects_non_director_admin(self, role):
        with pytest.raises(HTTPException) as exc:
            await require_story_control(user=_principal(role))
        assert exc.value.status_code == 403

    async def test_read_allows_operator(self):
        user = await require_story_reader(user=_principal("operator"))
        assert user.username == "test-operator"

    async def test_read_allows_director_and_admin(self):
        assert (await require_story_reader(user=_principal("director"))).role == "director"
        assert (await require_story_reader(user=_principal("admin"))).role == "admin"

    @pytest.mark.parametrize("role", ["spotter", "observer", "viewer"])
    async def test_read_rejects_others(self, role):
        with pytest.raises(HTTPException) as exc:
            await require_story_reader(user=_principal(role))
        assert exc.value.status_code == 403
