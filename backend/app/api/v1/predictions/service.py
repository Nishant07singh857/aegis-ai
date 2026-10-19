# backend/app/api/v1/predictions/service.py
"""Live prediction service (Phase 20, W1 — Global Predictive / G1).

Recomputes per-team WWCD% from real match state on every call. The
frontend polls the router every 30s, which is the update cadence.

Data sources (all real, all optional except the live team state):
  * live teams  — DBStoryDataProvider.get_live_teams (team_position feed)
  * form        — last-5 match results per alive team
  * head-to-head — pairwise fight records among the top-6 contenders
    only (caps DB work; lower teams get a neutral bonus)
  * zone phase  — DBStoryDataProvider.get_zone_context

Deltas: the service keeps an in-memory snapshot per match_id of the
previous call's probabilities; delta = current - previous (None for a
team seen for the first time).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.prediction_agents.win_probability import (
    attach_deltas,
    score_live_win_probability,
    snapshot_map,
)

logger = structlog.get_logger("aegis.api.predictions")

#: Pairwise H2H is only evaluated among the top-N contenders by base
#: score — full pairwise over 16 teams would be 240 DB queries per
#: 30s poll for a bonus that barely moves.
H2H_TOP_N = 6


class PredictionService:
    def __init__(self, db: Optional[AsyncSession] = None, provider: Any = None):
        self.db = db
        if provider is not None:
            self.provider = provider
        elif db is not None:
            from app.api.v1.stories.service import DBStoryDataProvider

            self.provider = DBStoryDataProvider(db)
        else:
            self.provider = None
        #: match_id -> {team_id: win_probability} of the previous snapshot
        self._previous: Dict[str, Dict[str, float]] = {}

    async def win_probability(
        self, match_id: str
    ) -> Optional[Dict[str, Any]]:
        """Live WWCD% for every alive team, ordered descending.

        Returns None when there is no usable live team state — the
        router turns that into a 404 and the cards render "—".
        """
        if self.provider is None:
            return None

        teams = await self.provider.get_live_teams(match_id)
        if not teams:
            return None

        form_by_team: Dict[str, List[Dict[str, Any]]] = {}
        for t in teams:
            tid = t.get("team_id")
            if not tid:
                continue
            try:
                results = await self.provider.get_team_match_results(
                    tid, limit=5
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("form lookup failed", team_id=tid, error=str(exc))
                results = None
            if results:
                form_by_team[tid] = results

        # H2H among the top-H2H_TOP_N contenders by base score.
        h2h: Dict[Tuple[str, str], Dict[str, Any]] = {}
        contenders = sorted(
            [t for t in teams if (t.get("alive_count") or 0) > 0],
            key=lambda t: (t.get("alive_count") or 0) * 3.0
            + (t.get("kills") or 0) * 2.0,
            reverse=True,
        )[:H2H_TOP_N]
        contender_ids = [str(t.get("team_id")) for t in contenders]
        for i, a in enumerate(contender_ids):
            for b in contender_ids[i + 1 :]:
                try:
                    rec = await self.provider.get_head_to_head(a, b, kind="team")
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning(
                        "h2h lookup failed", a=a, b=b, error=str(exc)
                    )
                    rec = None
                if rec:
                    h2h[(a, b)] = rec

        zone_phase: Optional[int] = None
        try:
            zone = await self.provider.get_zone_context(match_id)
            if zone and zone.get("phase") is not None:
                zone_phase = int(zone["phase"])
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("zone context lookup failed", error=str(exc))

        scored = score_live_win_probability(
            teams,
            form_by_team=form_by_team,
            h2h=h2h,
            zone_phase=zone_phase,
        )
        if not scored:
            return None

        attach_deltas(scored, self._previous.get(match_id))
        self._previous[match_id] = snapshot_map(scored)

        return {
            "match_id": match_id,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "teams_alive": len(scored),
            "zone_phase": zone_phase,
            "teams": [
                {
                    "team_id": p["team_id"],
                    "team_name": p["team_name"],
                    "win_probability": p["win_probability"],
                    "delta": p["delta"],
                    "alive_count": p["alive_count"],
                    "kills": p["kills"],
                    "in_zone": p["in_zone"],
                }
                for p in scored
            ],
        }
