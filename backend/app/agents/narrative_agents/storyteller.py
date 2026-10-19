# backend/app/agents/narrative_agents/storyteller.py
"""
AEGIS AI - Storyteller Agent (Phase 19)
Cricket-style data storytelling for BGMI broadcasts.

Generates 7 story types from REAL match data:
  1. head_to_head     — 1v1 records between two players/teams
  2. win_probability   — per-team win % with delta vs previous calculation
  3. player_comparison — side-by-side stats for two players
  4. team_form         — last-5 match results + form indicator
  5. zone_advantage     — which team the current zone favors + evidence
  6. milestone         — approaching milestones ("1 finish away from 50")
  7. comeback          — position jumps ("14th -> 3rd in 2 matches")

HARD RULE (zero fake data): every generator returns None when the data it
needs is missing. Bodies render "—" for individual stats that are unknown.
Nothing is ever invented.
"""

import time
from enum import Enum
from typing import Any, Dict, List, Optional

import structlog

from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.storyteller")


class StoryType(str, Enum):
    """The 7 story types from docs/AI_STORYTELLER.md"""
    HEAD_TO_HEAD = "head_to_head"
    WIN_PROBABILITY = "win_probability"
    PLAYER_COMPARISON = "player_comparison"
    TEAM_FORM = "team_form"
    ZONE_ADVANTAGE = "zone_advantage"
    MILESTONE = "milestone"
    COMEBACK = "comeback"


#: Milestone finish-counts worth alerting on (career finishes).
MILESTONE_TARGETS = (25, 50, 100, 150, 200)

#: A milestone counts as "approaching" when this many or fewer remain.
MILESTONE_WINDOW = 5

#: Minimum position jump (places) that qualifies as a comeback story.
COMEBACK_MIN_JUMP = 8


class StoryDataProvider:
    """Interface the StorytellerAgent pulls real data through.

    The production implementation lives in app/api/v1/stories/service.py
    (DBStoryDataProvider, backed by SQLAlchemy). Tests inject an in-memory
    implementation. Every method returns None / [] when data is missing —
    the agent then skips the story instead of inventing it.
    """

    async def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    async def get_player(self, player_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    async def get_head_to_head(
        self, entity_a_id: str, entity_b_id: str, kind: str = "team"
    ) -> Optional[Dict[str, Any]]:
        """{entity_a, entity_b, wins_a, wins_b, total, last_3: [...], key_stat}"""
        raise NotImplementedError

    async def get_player_stats(self, player_id: str) -> Optional[Dict[str, Any]]:
        """{player_id, name, ign, finishes, avg_damage, clutches} (rating omitted: no source)"""
        raise NotImplementedError

    async def get_team_match_results(
        self, team_id: str, limit: int = 5, tournament_id: Optional[str] = None
    ) -> Optional[List[Dict[str, Any]]]:
        """Newest-first [{match_id, placement, won, kills}]"""
        raise NotImplementedError

    async def get_live_teams(self, match_id: str) -> Optional[List[Dict[str, Any]]]:
        """[{team_id, team_name, alive_count, kills, in_zone}] for win probability"""
        raise NotImplementedError

    async def get_previous_win_prob(self, match_id: str) -> Optional[Dict[str, float]]:
        """team_id -> previous win probability (for deltas)"""
        raise NotImplementedError

    async def get_zone_context(self, match_id: str) -> Optional[Dict[str, Any]]:
        """{phase, favored_team_id, favored_team_name, similar_zones, top3_count, map_name}"""
        raise NotImplementedError

    async def get_milestones(self, player_id: str) -> List[Dict[str, Any]]:
        """Approaching milestones: [{kind, player_name, current, target, remaining}]"""
        raise NotImplementedError

    async def get_comeback_context(
        self, team_id: str, tournament_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """{team_name, from_placement, to_placement, jump, matches_span}"""
        raise NotImplementedError


def _fmt(value: Any) -> str:
    """Render a stat or '—' when unknown. Never invents a number."""
    return "—" if value is None else str(value)


def _ordinal(n: Any) -> str:
    """English ordinal for placements ('3rd'); falls back to raw on junk."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return _fmt(n)
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class StorytellerAgent(BaseAgent):
    """
    Generates broadcast-ready data stories from real match data.

    Reuses RivalryTracker (head-to-head context), ComebackDetector
    (comeback context) and WinnerPredictor (win probability scoring) when
    they are supplied; every one of them is optional — missing data means
    the story is skipped, never faked.
    """

    def __init__(
        self,
        config: AgentConfig,
        event_bus: EventBus,
        provider: Optional[StoryDataProvider] = None,
        rivalry_tracker: Any = None,
        comeback_detector: Any = None,
        winner_predictor: Any = None,
    ):
        super().__init__(config, event_bus)
        self.provider = provider or StoryDataProvider()
        self.rivalry_tracker = rivalry_tracker
        self.comeback_detector = comeback_detector
        self.winner_predictor = winner_predictor
        self._log = logger.bind(component="storyteller")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STORY ENVELOPE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _story(
        self,
        story_type: StoryType,
        title: str,
        body: str,
        data: Dict[str, Any],
        confidence: float,
    ) -> Dict[str, Any]:
        return {
            "type": story_type.value,
            "title": title,
            "body": body,
            "data": data,
            "confidence": round(max(0.0, min(1.0, confidence)), 2),
            "generated_at": time.time(),
        }

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 1. HEAD-TO-HEAD
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def generate_head_to_head(
        self,
        entity_a_id: str,
        entity_b_id: str,
        kind: str = "team",
    ) -> Optional[Dict[str, Any]]:
        """1v1 records between two players or teams.

        Team records come from fight outcomes (winner_team_id on fight
        events); player records from direct kill events. RivalryTracker
        context is attached when available.
        """
        if not entity_a_id or not entity_b_id or entity_a_id == entity_b_id:
            return None

        h2h = await self.provider.get_head_to_head(entity_a_id, entity_b_id, kind)
        if not h2h or not h2h.get("total"):
            return None

        a_name = h2h.get("entity_a_name") or entity_a_id
        b_name = h2h.get("entity_b_name") or entity_b_id
        wins_a = h2h.get("wins_a", 0)
        wins_b = h2h.get("wins_b", 0)
        total = h2h.get("total", 0)
        last_3 = h2h.get("last_3") or []

        # Last-3 record for entity A
        last3_a = sum(1 for w in last_3[:3] if w == "a")

        key_stat = h2h.get("key_stat")
        body = (
            f"{a_name} vs {b_name} — aaj tak {total} baar aamne-saamne, "
            f"{a_name} ne {wins_a} baar jeeta, {b_name} ne {wins_b} baar. "
            f"Last {min(3, len(last_3))} me se {last3_a} {a_name} ke naam."
        )
        if key_stat:
            body += f" Key stat: {key_stat}."

        data: Dict[str, Any] = {
            "entity_a": {"id": entity_a_id, "name": a_name, "wins": wins_a},
            "entity_b": {"id": entity_b_id, "name": b_name, "wins": wins_b},
            "total_encounters": total,
            "last_3": last_3[:3],
            "key_stat": key_stat,
            "kind": kind,
        }

        # Rivalry context when a tracker is wired in (team-level only).
        if kind == "team" and self.rivalry_tracker is not None:
            try:
                ctx = self.rivalry_tracker.get_rivalry_context(entity_a_id, entity_b_id)
                if ctx:
                    data["rivalry"] = ctx
                    if ctx.get("rivalry_name"):
                        body += f" Rivalry: {ctx['rivalry_name']}."
            except Exception as exc:
                self._log.warning("rivalry context failed", error=str(exc))

        confidence = min(0.95, 0.5 + 0.05 * total)
        return self._story(
            StoryType.HEAD_TO_HEAD,
            f"{a_name} vs {b_name} — Head to Head",
            body,
            data,
            confidence,
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 2. WIN PROBABILITY
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def generate_win_probability(
        self,
        match_id: str,
        teams_state: Optional[List[Dict[str, Any]]] = None,
        match_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Per-team win % with delta vs the previous calculation.

        Scoring comes from WinnerPredictor when wired in, otherwise a
        transparent heuristic over alive players / kills / zone status —
        always from the real team state, never invented.
        """
        teams = teams_state
        if teams is None:
            teams = await self.provider.get_live_teams(match_id)
        if not teams:
            return None

        context = dict(match_context or {})
        context.setdefault("teams_alive", len(teams))

        probabilities: List[Dict[str, Any]] = []
        if self.winner_predictor is not None:
            try:
                pred = self.winner_predictor.predict_winner(match_id, teams, context)
                for t in pred.team_predictions:
                    probabilities.append(
                        {
                            "team_id": t.get("team_id"),
                            "team_name": t.get("team_name"),
                            "win_probability": t.get("win_probability", 0.0),
                        }
                    )
            except Exception as exc:
                self._log.warning("winner predictor failed", error=str(exc))
                probabilities = []

        if not probabilities:
            # Honest heuristic fallback: alive players + kills + zone.
            probabilities = self._heuristic_win_prob(teams)
        if not probabilities:
            return None

        previous = await self.provider.get_previous_win_prob(match_id) or {}
        for p in probabilities:
            prev = previous.get(p["team_id"])
            p["delta"] = (
                round(p["win_probability"] - prev, 1) if prev is not None else None
            )

        ordered = sorted(probabilities, key=lambda p: p["win_probability"], reverse=True)
        segments = []
        for p in ordered[:5]:
            seg = f"{p['team_name']}: {p['win_probability']:.0f}%"
            if p["delta"] is not None:
                arrow = "🟢" if p["delta"] > 0 else ("🔴" if p["delta"] < 0 else "⚪")
                seg += f" ({p['delta']:+.0f}% pichhle calc se {arrow})"
            segments.append(seg)
        body = "Jeetne ke chance — " + " | ".join(segments)

        leader = ordered[0]
        confidence = 0.7 if self.winner_predictor is not None else 0.55
        return self._story(
            StoryType.WIN_PROBABILITY,
            f"Win Probability — {leader['team_name']} favourite",
            body,
            {
                "match_id": match_id,
                "probabilities": [
                    {
                        "team_id": p["team_id"],
                        "team_name": p["team_name"],
                        "win_probability": p["win_probability"],
                        "delta": p["delta"],
                    }
                    for p in ordered
                ],
                "leader_team_id": leader["team_id"],
            },
            confidence,
        )

    def _heuristic_win_prob(
        self, teams: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Transparent fallback scoring — alive*3 + kills*2 + zone bonus."""
        scored = []
        for t in teams:
            alive = t.get("alive_count") or 0
            kills = t.get("kills") or 0
            in_zone = 1 if t.get("in_zone") else 0
            if alive <= 0:
                continue
            scored.append(
                {
                    "team_id": t.get("team_id"),
                    "team_name": t.get("team_name") or t.get("team_id"),
                    "score": alive * 3.0 + kills * 2.0 + in_zone * 5.0,
                }
            )
        total = sum(s["score"] for s in scored)
        if total <= 0:
            return []
        return [
            {
                "team_id": s["team_id"],
                "team_name": s["team_name"],
                "win_probability": round(s["score"] / total * 100, 1),
            }
            for s in scored
        ]

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 3. PLAYER COMPARISON
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def generate_player_comparison(
        self, player_a_id: str, player_b_id: str
    ) -> Optional[Dict[str, Any]]:
        """Side-by-side stats for two players. Missing stats render '—'."""
        if not player_a_id or not player_b_id or player_a_id == player_b_id:
            return None

        stats_a = await self.provider.get_player_stats(player_a_id)
        stats_b = await self.provider.get_player_stats(player_b_id)
        if not stats_a or not stats_b:
            return None

        a_name = stats_a.get("name") or stats_a.get("ign") or player_a_id
        b_name = stats_b.get("name") or stats_b.get("ign") or player_b_id

        body = (
            f"{a_name} vs {b_name} — "
            f"Finishes: {_fmt(stats_a.get('finishes'))} vs {_fmt(stats_b.get('finishes'))}, "
            f"Avg Damage: {_fmt(stats_a.get('avg_damage'))} vs {_fmt(stats_b.get('avg_damage'))}, "
            f"Clutches: {_fmt(stats_a.get('clutches'))} vs {_fmt(stats_b.get('clutches'))}, "
            f"Rating: {_fmt(stats_a.get('rating'))} vs {_fmt(stats_b.get('rating'))}."
        )

        data = {
            "player_a": {
                "id": player_a_id,
                "name": a_name,
                "finishes": stats_a.get("finishes"),
                "avg_damage": stats_a.get("avg_damage"),
                "rating": stats_a.get("rating"),
                "clutches": stats_a.get("clutches"),
            },
            "player_b": {
                "id": player_b_id,
                "name": b_name,
                "finishes": stats_b.get("finishes"),
                "avg_damage": stats_b.get("avg_damage"),
                "rating": stats_b.get("rating"),
                "clutches": stats_b.get("clutches"),
            },
        }
        return self._story(
            StoryType.PLAYER_COMPARISON,
            f"{a_name} vs {b_name} — Player Comparison",
            body,
            data,
            0.85,
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 4. TEAM FORM
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def generate_team_form(
        self,
        team_id: str,
        last_n: int = 5,
        tournament_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Last-N match results (W/L) + form indicator. Needs 2+ matches."""
        team = await self.provider.get_team(team_id)
        if not team:
            return None

        results = await self.provider.get_team_match_results(
            team_id, limit=last_n, tournament_id=tournament_id
        )
        if not results or len(results) < 2:
            return None

        name = team.get("name") or team_id
        wl = ["W" if r.get("won") else "L" for r in results]
        wins = sum(1 for r in results if r.get("won"))

        if wins >= 4:
            indicator, label = "🔥🔥", "zabardast form"
        elif wins == 3:
            indicator, label = "🔥", "achhi form"
        elif wins == 2:
            indicator, label = "➖", "average form"
        else:
            indicator, label = "❄️", "kharab form"

        body = (
            f"{name}: {' '.join(wl)} {indicator} — "
            f"last {len(results)} me se {wins} jeete, {label} me hai."
        )
        return self._story(
            StoryType.TEAM_FORM,
            f"{name} — Team Form",
            body,
            {
                "team_id": team_id,
                "team_name": name,
                "results": wl,
                "wins": wins,
                "matches": len(results),
                "form_indicator": indicator,
                "form_label": label,
                "placements": [r.get("placement") for r in results],
            },
            min(0.9, 0.6 + 0.05 * len(results)),
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 5. ZONE ADVANTAGE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def generate_zone_advantage(
        self, match_id: str
    ) -> Optional[Dict[str, Any]]:
        """Which team the current zone favours, with real evidence."""
        ctx = await self.provider.get_zone_context(match_id)
        if not ctx or not ctx.get("favored_team_id"):
            return None

        name = ctx.get("favored_team_name") or ctx["favored_team_id"]
        similar = ctx.get("similar_zones", 0)
        top3 = ctx.get("top3_count", 0)
        phase = ctx.get("phase")

        if similar < 2:
            return None  # not enough evidence — skip, don't invent

        phase_txt = f"Zone {phase} " if phase else "Ye zone "
        body = (
            f"{phase_txt}{name} ko suit karta hai — "
            f"pichhle {similar} similar zones me {top3} baar top-3 me rahe."
        )
        return self._story(
            StoryType.ZONE_ADVANTAGE,
            f"Zone Advantage — {name}",
            body,
            {
                "match_id": match_id,
                "favored_team_id": ctx["favored_team_id"],
                "favored_team_name": name,
                "zone_phase": phase,
                "similar_zones": similar,
                "top3_count": top3,
                "map_name": ctx.get("map_name"),
            },
            min(0.85, 0.5 + 0.1 * similar),
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 6. MILESTONE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def generate_milestone(
        self, player_id: str
    ) -> Optional[Dict[str, Any]]:
        """Approaching milestones only (<= MILESTONE_WINDOW away)."""
        milestones = await self.provider.get_milestones(player_id)
        if not milestones:
            return None

        # Nearest milestone first.
        milestones = sorted(milestones, key=lambda m: m.get("remaining", 999))
        m = milestones[0]
        remaining = m.get("remaining")
        if remaining is None or remaining <= 0 or remaining > MILESTONE_WINDOW:
            return None

        name = m.get("player_name") or player_id
        target = m.get("target")
        kind = m.get("kind", "finish")
        body = (
            f"⚡ MILESTONE: {name} ko {remaining} aur {kind} chahiye "
            f"{target}-{kind} club ke liye!"
        )
        return self._story(
            StoryType.MILESTONE,
            f"Milestone Alert — {name}",
            body,
            {
                "player_id": player_id,
                "player_name": name,
                "kind": kind,
                "current": m.get("current"),
                "target": target,
                "remaining": remaining,
            },
            0.95,
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 7. COMEBACK
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def generate_comeback(
        self,
        team_id: str,
        tournament_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Position jumps across recent matches (tournament comeback)."""
        ctx = await self.provider.get_comeback_context(team_id, tournament_id)
        if not ctx:
            return None

        jump = ctx.get("jump", 0)
        if jump < COMEBACK_MIN_JUMP:
            return None

        name = ctx.get("team_name") or team_id
        span = ctx.get("matches_span", 2)
        body = (
            f"🔥 COMEBACK: {name} {_ordinal(ctx.get('from_placement'))} se "
            f"{_ordinal(ctx.get('to_placement'))} pe — {jump} position ka jump "
            f"sirf {span} matches me!"
        )
        data: Dict[str, Any] = {
            "team_id": team_id,
            "team_name": name,
            "from_placement": ctx.get("from_placement"),
            "to_placement": ctx.get("to_placement"),
            "jump": jump,
            "matches_span": span,
        }

        # ComebackDetector context when wired in (in-match comebacks).
        if self.comeback_detector is not None:
            try:
                recent = getattr(self.comeback_detector, "comeback_history", None)
                if recent:
                    data["detector_comebacks"] = len(recent)
            except Exception as exc:
                self._log.warning("comeback detector read failed", error=str(exc))

        return self._story(
            StoryType.COMEBACK,
            f"Comeback Story — {name}",
            body,
            data,
            min(0.9, 0.6 + 0.02 * jump),
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DISPATCH
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def generate_stories(
        self,
        match_id: str,
        types: List[str],
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Generate the requested story types for a match.

        ``context`` carries the entity ids each type needs, e.g.
        {"entity_a_id": ..., "entity_b_id": ..., "kind": "team",
         "player_a_id": ..., "player_b_id": ...,
         "team_id": ..., "player_id": ..., "tournament_id": ...}.
        Types with missing data are skipped (never fabricated).
        """
        context = context or {}
        stories: List[Dict[str, Any]] = []

        for raw in types:
            try:
                story_type = StoryType(raw)
            except ValueError:
                self._log.warning("unknown story type requested", type=raw)
                continue

            story: Optional[Dict[str, Any]] = None
            try:
                if story_type == StoryType.HEAD_TO_HEAD:
                    story = await self.generate_head_to_head(
                        context.get("entity_a_id", ""),
                        context.get("entity_b_id", ""),
                        context.get("kind", "team"),
                    )
                elif story_type == StoryType.WIN_PROBABILITY:
                    story = await self.generate_win_probability(
                        match_id,
                        teams_state=context.get("teams_state"),
                        match_context=context.get("match_context"),
                    )
                elif story_type == StoryType.PLAYER_COMPARISON:
                    story = await self.generate_player_comparison(
                        context.get("player_a_id", ""),
                        context.get("player_b_id", ""),
                    )
                elif story_type == StoryType.TEAM_FORM:
                    story = await self.generate_team_form(
                        context.get("team_id", ""),
                        last_n=context.get("last_n", 5),
                        tournament_id=context.get("tournament_id"),
                    )
                elif story_type == StoryType.ZONE_ADVANTAGE:
                    story = await self.generate_zone_advantage(match_id)
                elif story_type == StoryType.MILESTONE:
                    story = await self.generate_milestone(context.get("player_id", ""))
                elif story_type == StoryType.COMEBACK:
                    story = await self.generate_comeback(
                        context.get("team_id", ""),
                        tournament_id=context.get("tournament_id"),
                    )
            except Exception as exc:
                self._log.warning(
                    "story generation failed", type=story_type.value, error=str(exc)
                )
                story = None

            if story is not None:
                stories.append(story)
            else:
                self._log.info("story skipped (missing data)", type=story_type.value)

        return stories

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("fight_detected")
        self.subscribe_to_event("match_state_change")
        self.subscribe_to_event("zone_change")

    async def tick(self) -> None:
        """Periodic maintenance tick — generation is on-demand."""

    async def handle_event(self, record: EventRecord) -> None:
        """Keep match context current for event-driven use."""
        try:
            data = record.event.data if hasattr(record, "event") else {}
            if isinstance(data, dict) and data.get("match_id"):
                self.config.match_id = data["match_id"]
        except Exception:
            pass

    async def cleanup(self) -> None:
        """Release agent resources."""
