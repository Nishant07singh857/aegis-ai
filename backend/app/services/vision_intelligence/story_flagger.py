# backend/app/services/vision_intelligence/story_flagger.py
"""Narrative auto-flagging (Phase 22, M9).

The 7 story generators in ``storyteller.py`` are real, but before
Phase 22 they only fired when a director manually requested them —
nothing in the live loop ever noticed a storyline moment.

This flagger evaluates *candidate conditions* on live events and asks
the real generators to verify them:

  * head-to-head moment   — a fight is promoted between exactly 2 teams
  * zone advantage swing  — the zone-analytics favoured team changes,
                            or a new zone geometry lands
  * milestone reached     — a kill event carries a resolvable player id
  * comeback threshold    — a team reduced to its last player is in a
                            newly promoted fight and still standing
  * late-game win prob.   — the match alive count crosses into the
                            endgame (<= 16, then <= 8 players)

The generator is the truth gate: it returns nothing when the data for
the story does not exist, and then NO flag is raised — a storyline is
never fabricated to fill the slot. (player_comparison and team_form
have no truthful live trigger — one needs two specific players in a
shared moment, the other is a post-match pattern — so they stay
manual-only, deliberately.)

Mode behaviour mirrors the director's own rules:
  * full_auto  -> the story is generated AND triggered to air
                  (the system acts directly, like system_publish).
  * semi_auto  -> the story is generated and left pending: it appears
                  in the stories approval flow (GET /stories/available)
                  for a director/admin to trigger or dismiss — the
                  same approval queue directors already use, with the
                  story.trigger endpoint's director/admin gate intact.
  * manual     -> same as semi_auto: a pending suggestion, never air.

Rate limiting: a global minimum interval between auto-flags, a
per-story-type cooldown, and a longer per-(type, entity) cooldown so
the same storyline cannot re-fire all match. Attempts that produce no
story (missing data) still consume the per-type cooldown — the flagger
must not hammer the DB providers every event.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

import structlog

logger = structlog.get_logger("aegis.vision_intelligence.story_flagger")

#: async (match_id, types, context) -> [story dicts with id/type/...]
StoryGeneratorFn = Callable[
    [str, List[str], Dict[str, Any]], Awaitable[List[Dict[str, Any]]]
]
#: async (story_id, username) -> truthy when the story went to air
StoryTriggerFn = Callable[[str, str], Awaitable[Any]]
#: () -> current director mode ("full_auto" | "semi_auto" | "manual")
ModeProviderFn = Callable[[], str]
#: async (event_type, data) -> None (event-bus publish, best-effort)
PublishFn = Callable[[str, Dict[str, Any]], Awaitable[None]]

AUTO_FLAGGER_USERNAME = "story_auto_flagger"

#: Bus topic emitted for every auto-flag (closes the M12
#: ``insight.generated`` gap for narrative insights).
INSIGHT_EVENT_TYPE = "insight.generated"


class StoryAutoFlagger:
    """Evaluates live-event candidates against the real story generators."""

    def __init__(
        self,
        generator: StoryGeneratorFn,
        trigger: StoryTriggerFn,
        mode_provider: ModeProviderFn,
        publish: Optional[PublishFn] = None,
        clock: Callable[[], float] = time.time,
        min_interval_s: float = 20.0,
        per_type_cooldown_s: float = 120.0,
        per_entity_cooldown_s: float = 300.0,
    ):
        self._generator = generator
        self._trigger = trigger
        self._mode_provider = mode_provider
        self._publish = publish
        self._clock = clock
        self.min_interval_s = float(min_interval_s)
        self.per_type_cooldown_s = float(per_type_cooldown_s)
        self.per_entity_cooldown_s = float(per_entity_cooldown_s)

        self._last_flag_at = 0.0
        self._last_type_attempt: Dict[str, float] = {}
        self._last_entity_flag: Dict[str, float] = {}
        self._late_game_stage = 0  # 0 = none, 1 = <=16 seen, 2 = <=8 seen

        self.stats = {
            "candidates": 0,
            "attempts": 0,
            "flags_triggered": 0,
            "flags_suggested": 0,
            "skipped_missing_data": 0,
            "skipped_cooldown": 0,
        }

    # ── candidate entry points (called by the subscriber) ────────

    async def on_fight_promoted(
        self,
        match_id: Optional[str],
        teams_involved: List[str],
        team_alive: Optional[Dict[str, int]] = None,
    ) -> Optional[Dict[str, Any]]:
        """A new fight was promoted: H2H moment (+ comeback candidate)."""
        if not match_id or len(teams_involved) < 2:
            return None
        teams = sorted(str(t) for t in teams_involved)
        result = await self._evaluate(
            "head_to_head",
            match_id,
            {"entity_a_id": teams[0], "entity_b_id": teams[1], "kind": "team"},
            entity_key=f"h2h:{teams[0]}:{teams[1]}",
        )
        # Comeback candidate: a team on its last player is in this fight.
        if team_alive:
            for team_id in teams:
                if team_alive.get(team_id) == 1:
                    comeback = await self._evaluate(
                        "comeback",
                        match_id,
                        {"team_id": team_id},
                        entity_key=f"comeback:{team_id}",
                    )
                    if result is None:
                        result = comeback
        return result

    async def on_zone_swing(
        self, match_id: Optional[str], favoured_team_id: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """Zone advantage swung (or fresh geometry): evaluate zone story
        and refresh win probability for the new circle."""
        if not match_id:
            return None
        result = await self._evaluate(
            "zone_advantage", match_id, {}, entity_key=f"zone:{favoured_team_id}"
        )
        if result is None:
            result = await self._evaluate(
                "win_probability", match_id, {}, entity_key="winprob:zone"
            )
        return result

    async def on_kill(
        self, match_id: Optional[str], killer_player_id: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """Milestone candidate — only with a resolvable player id.

        Vision kill-feed lines carry names, not DB player ids; when the
        id is absent this returns None (missing data -> no flag).
        """
        if not match_id or not killer_player_id:
            return None
        return await self._evaluate(
            "milestone",
            match_id,
            {"player_id": killer_player_id},
            entity_key=f"milestone:{killer_player_id}",
        )

    async def on_alive_count(
        self, match_id: Optional[str], players_alive: Optional[int]
    ) -> Optional[Dict[str, Any]]:
        """Endgame crossings (<=16, <=8) refresh the win-probability story."""
        if not match_id or players_alive is None:
            return None
        try:
            alive = int(players_alive)
        except (TypeError, ValueError):
            return None
        stage = 2 if alive <= 8 else (1 if alive <= 16 else 0)
        if stage == 0 or stage <= self._late_game_stage:
            return None
        self._late_game_stage = stage
        return await self._evaluate(
            "win_probability",
            match_id,
            {},
            entity_key=f"winprob:endgame{stage}",
        )

    # ── core evaluation ──────────────────────────────────────────

    async def _evaluate(
        self,
        story_type: str,
        match_id: str,
        context: Dict[str, Any],
        entity_key: str,
    ) -> Optional[Dict[str, Any]]:
        self.stats["candidates"] += 1
        now = self._clock()

        # Cooldowns: global min interval applies to successful flags;
        # per-type applies to attempts (anti-hammer); per-entity to flags.
        if now - self._last_flag_at < self.min_interval_s:
            self.stats["skipped_cooldown"] += 1
            return None
        if now - self._last_type_attempt.get(story_type, 0.0) < self.per_type_cooldown_s:
            self.stats["skipped_cooldown"] += 1
            return None
        if now - self._last_entity_flag.get(entity_key, 0.0) < self.per_entity_cooldown_s:
            self.stats["skipped_cooldown"] += 1
            return None
        self._last_type_attempt[story_type] = now
        self.stats["attempts"] += 1

        try:
            stories = await self._generator(match_id, [story_type], context)
        except Exception as exc:
            logger.warning(
                "auto_flag_generation_failed",
                story_type=story_type,
                error=str(exc)[:200],
            )
            return None
        if not stories:
            # The generator found no real data for this story. No flag.
            self.stats["skipped_missing_data"] += 1
            logger.info(
                "auto_flag_skipped_missing_data",
                story_type=story_type,
                match_id=match_id,
            )
            return None

        story = stories[0]
        self._last_flag_at = now
        self._last_entity_flag[entity_key] = now

        mode = (self._mode_provider() or "manual").strip().lower()
        action = "suggested"
        if mode == "full_auto":
            try:
                triggered = await self._trigger(
                    str(story.get("id")), AUTO_FLAGGER_USERNAME
                )
            except Exception as exc:
                logger.warning(
                    "auto_flag_trigger_failed",
                    story_type=story_type,
                    error=str(exc)[:200],
                )
                triggered = None
            if triggered:
                action = "triggered"
                self.stats["flags_triggered"] += 1
            else:
                # Full-auto trigger failed: fall back to a pending
                # suggestion rather than losing the story or forcing air.
                self.stats["flags_suggested"] += 1
        else:
            self.stats["flags_suggested"] += 1

        flag = {
            "story_id": story.get("id"),
            "story_type": story.get("type") or story_type,
            "title": story.get("title"),
            "match_id": match_id,
            "action": action,
            "director_mode": mode,
            "flagged_at": now,
        }
        if self._publish is not None:
            try:
                await self._publish(INSIGHT_EVENT_TYPE, dict(flag))
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("insight_publish_failed", error=str(exc)[:200])
        logger.info(
            "story_auto_flagged",
            story_type=flag["story_type"],
            action=action,
            mode=mode,
            match_id=match_id,
        )
        return flag

    def snapshot(self) -> Dict[str, Any]:
        return {
            **self.stats,
            "min_interval_s": self.min_interval_s,
            "per_type_cooldown_s": self.per_type_cooldown_s,
            "per_entity_cooldown_s": self.per_entity_cooldown_s,
        }
