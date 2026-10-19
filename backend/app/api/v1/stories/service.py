"""AI storyteller service (Phase 19).

DBStoryDataProvider implements the StorytellerAgent's StoryDataProvider
interface with real SQLAlchemy queries. Every method returns None / []
when the data it needs is absent — the agent then skips the story instead
of inventing numbers.

StoryService owns story persistence, trigger/dismiss lifecycle (trigger
publishes ``story.trigger`` on the event bus for the broadcast output)
and the per-tournament template/animation settings.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base_agent import AgentConfig
from app.agents.narrative_agents.storyteller import (
    MILESTONE_TARGETS,
    MILESTONE_WINDOW,
    StoryDataProvider,
    StorytellerAgent,
    StoryType,
)
from app.db.models.fight_event import FightEvent
from app.db.models.kill_event import KillEvent
from app.db.models.match import Match
from app.db.models.match_event import MatchEvent
from app.db.models.player import Player
from app.db.models.story import Story, StorySetting
from app.db.models.team import Team

logger = structlog.get_logger("aegis.api.stories")

#: Event-bus type pushed when a story goes to the broadcast output.
STORY_TRIGGER_EVENT = "story.trigger"
STORY_DISMISS_EVENT = "story.dismiss"


def _first_present(d: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def _parse_standings(event_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract [{team_id, placement}] from a match_end event's payload.

    Tolerates the few shapes the codebase has used; returns [] when the
    payload carries no standings (caller then skips honestly).
    """
    rows = _first_present(event_data, "standings", "results", "placements", default=[])
    if not isinstance(rows, list):
        return []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        team_id = _first_present(r, "team_id", "team", "id")
        placement = _first_present(r, "placement", "position", "rank", "place")
        if team_id is None or placement is None:
            continue
        try:
            placement = int(placement)
        except (TypeError, ValueError):
            continue
        out.append({"team_id": str(team_id), "placement": placement})
    return out


class DBStoryDataProvider(StoryDataProvider):
    """Real-data provider for the StorytellerAgent (SQLAlchemy)."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── identity ──────────────────────────────────────────────

    async def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        row = await self.db.get(Team, team_id)
        if row is None:
            return None
        return {"id": row.id, "name": row.name, "tag": row.tag}

    async def get_player(self, player_id: str) -> Optional[Dict[str, Any]]:
        row = await self.db.get(Player, player_id)
        if row is None:
            return None
        team_name = None
        if row.team_id:
            team = await self.db.get(Team, row.team_id)
            team_name = team.name if team else None
        return {
            "id": row.id,
            "name": row.name,
            "ign": row.ign,
            "team_id": row.team_id,
            "team_name": team_name,
        }

    # ── head-to-head ──────────────────────────────────────────

    async def get_head_to_head(
        self, entity_a_id: str, entity_b_id: str, kind: str = "team"
    ) -> Optional[Dict[str, Any]]:
        if kind == "player":
            return await self._player_h2h(entity_a_id, entity_b_id)
        return await self._team_h2h(entity_a_id, entity_b_id)

    async def _team_h2h(self, a_id: str, b_id: str) -> Optional[Dict[str, Any]]:
        """Fight outcomes where both teams clashed (winner_team_id set)."""
        stmt = (
            select(FightEvent)
            .where(
                FightEvent.team_ids.has_all([a_id, b_id]),
                FightEvent.winner_team_id.in_([a_id, b_id]),
            )
            .order_by(desc(FightEvent.start_timestamp_ms))
            .limit(200)
        )
        fights = (await self.db.execute(stmt)).scalars().all()
        if not fights:
            return None

        wins_a = sum(1 for f in fights if f.winner_team_id == a_id)
        wins_b = sum(1 for f in fights if f.winner_team_id == b_id)
        last_3 = ["a" if f.winner_team_id == a_id else "b" for f in fights[:3]]
        total_kills = sum(f.total_kills or 0 for f in fights)

        a = await self.get_team(a_id)
        b = await self.get_team(b_id)
        return {
            "entity_a_name": (a or {}).get("name") or a_id,
            "entity_b_name": (b or {}).get("name") or b_id,
            "wins_a": wins_a,
            "wins_b": wins_b,
            "total": len(fights),
            "last_3": last_3,
            "key_stat": f"{total_kills} total kills in {len(fights)} fights"
            if total_kills
            else None,
        }

    async def _player_h2h(self, a_id: str, b_id: str) -> Optional[Dict[str, Any]]:
        """Direct kill events between two players."""
        stmt = (
            select(KillEvent)
            .where(
                or_(
                    (KillEvent.killer_player_id == a_id)
                    & (KillEvent.victim_player_id == b_id),
                    (KillEvent.killer_player_id == b_id)
                    & (KillEvent.victim_player_id == a_id),
                )
            )
            .order_by(desc(KillEvent.timestamp_ms))
            .limit(200)
        )
        kills = (await self.db.execute(stmt)).scalars().all()
        if not kills:
            return None

        kills_a = sum(1 for k in kills if k.killer_player_id == a_id)
        kills_b = sum(1 for k in kills if k.killer_player_id == b_id)
        last_3 = ["a" if k.killer_player_id == a_id else "b" for k in kills[:3]]

        a = await self.get_player(a_id)
        b = await self.get_player(b_id)
        a_name = (a or {}).get("ign") or (a or {}).get("name") or a_id
        b_name = (b or {}).get("ign") or (b or {}).get("name") or b_id
        return {
            "entity_a_name": a_name,
            "entity_b_name": b_name,
            "wins_a": kills_a,
            "wins_b": kills_b,
            "total": len(kills),
            "last_3": last_3,
            "key_stat": f"{a_name} ne {b_name} ko {kills_a} baar finish kiya",
        }

    # ── player stats ──────────────────────────────────────────

    async def get_player_stats(self, player_id: str) -> Optional[Dict[str, Any]]:
        player = await self.get_player(player_id)
        if player is None:
            return None

        finishes = await self.db.scalar(
            select(func.count())
            .select_from(KillEvent)
            .where(KillEvent.killer_player_id == player_id)
        ) or 0

        clutch_kills = await self.db.scalar(
            select(func.count())
            .select_from(KillEvent)
            .where(
                KillEvent.killer_player_id == player_id,
                KillEvent.meta.op("->>")("clutch") == "true",
            )
        ) or 0

        row = await self.db.get(Player, player_id)
        return {
            "player_id": player_id,
            "name": player.get("name"),
            "ign": player.get("ign"),
            "finishes": finishes,
            # avg_damage is a per-match average on the player profile; rating
            # has no source in the DB, so it is omitted (renders "—").
            "avg_damage": round(row.avg_damage, 1) if row.avg_damage else None,
            "rating": None,
            "clutches": clutch_kills,
        }

    # ── team form / standings ─────────────────────────────────

    async def _match_end_standings(
        self,
        tournament_id: Optional[str] = None,
        match_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Newest-first [{match_id, standings:[{team_id, placement}]}]."""
        stmt = select(MatchEvent).where(MatchEvent.event_type == "match_end")
        if match_id:
            stmt = stmt.where(MatchEvent.match_id == match_id)
        if tournament_id:
            stmt = stmt.join(Match, Match.id == MatchEvent.match_id).where(
                Match.tournament_id == tournament_id
            )
        stmt = stmt.order_by(desc(MatchEvent.real_timestamp)).limit(limit)
        events = (await self.db.execute(stmt)).scalars().all()

        out = []
        for e in events:
            standings = _parse_standings(e.event_data or {})
            if standings:
                out.append({"match_id": e.match_id, "standings": standings})
        return out

    async def get_team_match_results(
        self, team_id: str, limit: int = 5, tournament_id: Optional[str] = None
    ) -> Optional[List[Dict[str, Any]]]:
        team = await self.get_team(team_id)
        if team is None:
            return None

        matches = await self._match_end_standings(
            tournament_id=tournament_id, limit=limit * 4
        )
        results = []
        for m in matches:
            for s in m["standings"]:
                if s["team_id"] == team_id or s["team_id"] == team.get("name"):
                    results.append(
                        {
                            "match_id": m["match_id"],
                            "placement": s["placement"],
                            "won": s["placement"] == 1,
                            "kills": None,  # not in standings payloads
                        }
                    )
                    break
            if len(results) >= limit:
                break
        if len(results) < 2:
            return None  # not enough history for a form story
        return results

    # ── win probability inputs ────────────────────────────────

    async def get_live_teams(self, match_id: str) -> Optional[List[Dict[str, Any]]]:
        """Latest team_position snapshot per team for the match."""
        stmt = (
            select(MatchEvent)
            .where(
                MatchEvent.match_id == match_id,
                MatchEvent.event_type == "team_position",
            )
            .order_by(desc(MatchEvent.timestamp_ms))
            .limit(500)
        )
        events = (await self.db.execute(stmt)).scalars().all()
        if not events:
            return None

        latest: Dict[str, Dict[str, Any]] = {}
        for e in events:
            tid = e.team_id or _first_present(e.event_data or {}, "team_id")
            if not tid or tid in latest:
                continue
            data = e.event_data or {}
            alive = _first_present(data, "alive_count", "alive", "players_alive")
            if alive is None:
                continue
            latest[tid] = {
                "team_id": tid,
                "team_name": None,  # resolved below
                "alive_count": int(alive),
                "kills": _first_present(data, "kills", "finishes", "team_kills") or 0,
                "in_zone": bool(
                    _first_present(data, "in_zone", "inside_zone", default=True)
                ),
            }

        if not latest:
            return None
        for tid, t in latest.items():
            team = await self.get_team(tid)
            t["team_name"] = (team or {}).get("name") or tid
        return list(latest.values())

    async def get_previous_win_prob(self, match_id: str) -> Optional[Dict[str, float]]:
        stmt = (
            select(Story)
            .where(
                Story.match_id == match_id,
                Story.story_type == StoryType.WIN_PROBABILITY.value,
            )
            .order_by(desc(Story.generated_at_utc))
            .limit(1)
        )
        row = (await self.db.execute(stmt)).scalars().first()
        if row is None:
            return None
        probs = (row.data or {}).get("probabilities") or []
        return {
            p["team_id"]: p["win_probability"]
            for p in probs
            if p.get("team_id") is not None and p.get("win_probability") is not None
        }

    # ── zone advantage ────────────────────────────────────────

    async def get_zone_context(self, match_id: str) -> Optional[Dict[str, Any]]:
        """Favoured team from past top-3 rate on the same map.

        Evidence: the last 5 finished matches on this match's map; the team
        with the most top-3 finishes (>= 2) is favoured. Current zone phase
        comes from the live match's latest zone_change event.
        """
        match = await self.db.get(Match, match_id)
        if match is None:
            return None

        zone_stmt = (
            select(MatchEvent)
            .where(
                MatchEvent.match_id == match_id,
                MatchEvent.event_type == "zone_change",
            )
            .order_by(desc(MatchEvent.timestamp_ms))
            .limit(1)
        )
        zone_event = (await self.db.execute(zone_stmt)).scalars().first()
        phase = _first_present((zone_event.event_data if zone_event else {}) or {},
                               "phase", "zone_phase", "phase_number")

        past = await self._match_end_standings(limit=60)
        # Same-map matches only (honest "similar" definition).
        match_ids = [m["match_id"] for m in past]
        maps: Dict[str, str] = {}
        if match_ids:
            rows = (
                await self.db.execute(
                    select(Match.id, Match.map_name).where(Match.id.in_(match_ids))
                )
            ).all()
            maps = {r[0]: r[1] for r in rows}
        same_map = [m for m in past if maps.get(m["match_id"]) == match.map_name][:5]
        if len(same_map) < 2:
            return None

        top3_counts: Dict[str, int] = {}
        for m in same_map:
            for s in m["standings"]:
                if s["placement"] <= 3:
                    top3_counts[s["team_id"]] = top3_counts.get(s["team_id"], 0) + 1
        if not top3_counts:
            return None

        favoured_id = max(top3_counts, key=lambda t: top3_counts[t])
        if top3_counts[favoured_id] < 2:
            return None

        team = await self.get_team(favoured_id)
        return {
            "phase": phase,
            "favored_team_id": favoured_id,
            "favored_team_name": (team or {}).get("name") or favoured_id,
            "similar_zones": len(same_map),
            "top3_count": top3_counts[favoured_id],
            "map_name": match.map_name,
        }

    # ── milestones ────────────────────────────────────────────

    async def get_milestones(self, player_id: str) -> List[Dict[str, Any]]:
        player = await self.get_player(player_id)
        if player is None:
            return []

        career_finishes = await self.db.scalar(
            select(func.count())
            .select_from(KillEvent)
            .where(KillEvent.killer_player_id == player_id)
        ) or 0

        name = player.get("ign") or player.get("name") or player_id
        out = []
        for target in MILESTONE_TARGETS:
            remaining = target - career_finishes
            if 0 < remaining <= MILESTONE_WINDOW:
                out.append(
                    {
                        "kind": "finish",
                        "player_name": name,
                        "current": career_finishes,
                        "target": target,
                        "remaining": remaining,
                    }
                )
        return out

    # ── comebacks ─────────────────────────────────────────────

    async def get_comeback_context(
        self, team_id: str, tournament_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        team = await self.get_team(team_id)
        if team is None:
            return None

        matches = await self._match_end_standings(
            tournament_id=tournament_id, limit=20
        )
        placements: List[int] = []
        for m in matches:
            for s in m["standings"]:
                if s["team_id"] == team_id or s["team_id"] == team.get("name"):
                    placements.append(s["placement"])
                    break
        # placements are newest-first; reverse to chronological.
        placements = placements[::-1]
        if len(placements) < 2:
            return None

        best: Optional[Dict[str, Any]] = None
        for i in range(len(placements)):
            for j in range(i + 1, len(placements)):
                jump = placements[i] - placements[j]  # lower placement = better
                if jump >= 8 and (best is None or jump > best["jump"]):
                    best = {
                        "from_placement": placements[i],
                        "to_placement": placements[j],
                        "jump": jump,
                        "matches_span": j - i,
                    }
        if best is None:
            return None
        best["team_name"] = team.get("name") or team_id
        return best


class StoryService:
    """Persistence + lifecycle for storyteller stories."""

    def __init__(self, db: AsyncSession):
        self.db = db

    def _agent(self) -> StorytellerAgent:
        # Event bus is optional at service level: the agent only needs it
        # for lifecycle hooks, so a None bus is acceptable in tests.
        try:
            from app.core.event_bus import event_bus
        except Exception:  # pragma: no cover - defensive
            event_bus = None  # type: ignore[assignment]
        return StorytellerAgent(
            AgentConfig(agent_type="storyteller"),
            event_bus,
            provider=DBStoryDataProvider(self.db),
        )

    # ── generation ────────────────────────────────────────────

    async def generate(
        self,
        match_id: str,
        types: List[str],
        context: Dict[str, Any],
        tournament_id: Optional[str] = None,
    ) -> List[Story]:
        agent = self._agent()
        generated = await agent.generate_stories(match_id, types, context)

        match = await self.db.get(Match, match_id)
        resolved_tournament = tournament_id or (match.tournament_id if match else None)

        stories: List[Story] = []
        for g in generated:
            story = Story(
                match_id=match_id if match else None,
                tournament_id=resolved_tournament,
                story_type=g["type"],
                title=g["title"],
                body=g["body"],
                data=g["data"],
                confidence=g["confidence"],
                status="generated",
            )
            self.db.add(story)
            stories.append(story)
        await self.db.commit()
        for s in stories:
            await self.db.refresh(s)
        logger.info(
            "stories generated",
            match_id=match_id,
            requested=len(types),
            generated=len(stories),
        )
        return stories

    # ── reads ─────────────────────────────────────────────────

    async def list_stories(
        self,
        match_id: Optional[str] = None,
        story_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Story]:
        stmt = select(Story).order_by(desc(Story.generated_at_utc)).limit(limit)
        if match_id:
            stmt = stmt.where(Story.match_id == match_id)
        if story_type:
            stmt = stmt.where(Story.story_type == story_type)
        if status:
            stmt = stmt.where(Story.status == status)
        return (await self.db.execute(stmt)).scalars().all()

    async def get_story(self, story_id: str) -> Optional[Story]:
        return await self.db.get(Story, story_id)

    # ── trigger / dismiss ─────────────────────────────────────

    async def _publish_bus_event(
        self,
        event_type: str,
        story: Story,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            from app.core.event_bus import event_bus

            if event_bus._running:
                data: Dict[str, Any] = {
                    "story_id": story.id,
                    "story_type": story.story_type,
                    "title": story.title,
                    "body": story.body,
                    "data": story.data or {},
                    "match_id": story.match_id,
                }
                if extra:
                    data.update(extra)
                await event_bus.publish(
                    event_type=event_type,
                    data=data,
                    source="story_service",
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("story event-bus publish failed", error=str(exc))

    async def trigger(
        self,
        story_id: str,
        username: str,
        template_id: Optional[str] = None,
        animation_style: Optional[str] = None,
    ) -> Optional[Story]:
        story = await self.get_story(story_id)
        if story is None:
            return None
        story.status = "triggered"
        story.triggered_by = username
        story.triggered_at_utc = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(story)
        extra: Dict[str, Any] = {}
        if template_id:
            extra["template_id"] = template_id
        if animation_style:
            extra["animation_style"] = animation_style
        await self._publish_bus_event(STORY_TRIGGER_EVENT, story, extra=extra)
        logger.info("story triggered to broadcast", story_id=story_id, by=username)
        return story

    async def dismiss(self, story_id: str, username: str) -> Optional[Story]:
        story = await self.get_story(story_id)
        if story is None:
            return None
        story.status = "dismissed"
        await self.db.commit()
        await self.db.refresh(story)
        await self._publish_bus_event(STORY_DISMISS_EVENT, story)
        logger.info("story dismissed", story_id=story_id, by=username)
        return story

    # ── settings ──────────────────────────────────────────────

    async def get_settings(
        self, tournament_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Tournament settings, falling back to global defaults."""
        stmt = select(StorySetting).where(
            StorySetting.tournament_id == tournament_id
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        settings = [
            {
                "story_type": r.story_type,
                "template": r.template,
                "animation_style": r.animation_style,
            }
            for r in rows
        ]
        if not settings and tournament_id is not None:
            global_rows = (
                await self.db.execute(
                    select(StorySetting).where(StorySetting.tournament_id.is_(None))
                )
            ).scalars().all()
            settings = [
                {
                    "story_type": r.story_type,
                    "template": r.template,
                    "animation_style": r.animation_style,
                }
                for r in global_rows
            ]
        return settings

    async def upsert_settings(
        self, tournament_id: Optional[str], settings: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        # Delete-then-insert: the nullable tournament_id key can't rely on a
        # unique constraint (NULLs are distinct in Postgres).
        existing = (
            await self.db.execute(
                select(StorySetting).where(
                    StorySetting.tournament_id == tournament_id
                    if tournament_id is not None
                    else StorySetting.tournament_id.is_(None)
                )
            )
        ).scalars().all()
        for row in existing:
            await self.db.delete(row)

        valid_types = {t.value for t in StoryType}
        for s in settings:
            if s.get("story_type") not in valid_types:
                continue
            self.db.add(
                StorySetting(
                    tournament_id=tournament_id,
                    story_type=s["story_type"],
                    template=s["template"],
                    animation_style=s["animation_style"],
                )
            )
        await self.db.commit()
        return await self.get_settings(tournament_id)
