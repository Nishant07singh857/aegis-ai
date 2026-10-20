# backend/app/intelligence/points_engine.py
"""
DEPRECATED — PointsEngine retired (Phase 22 / W3, M8).

This module used to keep a SECOND standings source of truth: a Redis
hash (``aegis:standings``) counting finishes only, with no placement
points and no tournament configuration, beside the configured
``PointsCalculator``. Two sources meant two different leaderboards.

It is now a thin read-through adapter over the SINGLE source of truth,
``app.analytics.points_calculator.PointsCalculator``:

* Scoring configuration (placement table, points-per-kill) comes only
  from the calculator (tournament config / BMSD defaults).
* The old Redis hash is no longer read or written. Any data left in
  ``aegis:standings`` from older runs is stale and must be ignored —
  delete the key when convenient; nothing in the product reads it.
* ``process_kill_event`` no longer mutates standings on its own: kill
  events flow through match recording into the calculator. It remains
  only as a compatibility shim returning the calculator's current
  leaderboard, and logs a deprecation warning when called.

New code must use ``PointsCalculator`` directly.
"""

from __future__ import annotations

from typing import Any, List, Optional

import structlog

logger = structlog.get_logger("aegis.intelligence.points")


class PointsEngine:
    """Deprecated adapter — delegates to PointsCalculator (see docstring)."""

    def __init__(self, calculator: Optional[Any] = None) -> None:
        if calculator is None:
            from app.analytics.points_calculator import PointsCalculator

            calculator = PointsCalculator()
        self._calculator = calculator
        logger.warning(
            "PointsEngine is deprecated: standings flow through "
            "PointsCalculator only (Phase 22 retirement of the Redis "
            "aegis:standings second source of truth)."
        )

    @property
    def calculator(self) -> Any:
        return self._calculator

    def process_kill_event(self, killer_team: str, victim_team: str) -> List[dict]:
        """Compatibility shim — does NOT score anything itself.

        Standings change only via match recording through
        PointsCalculator.calculate_match_points. Returns the current
        leaderboard so legacy callers keep rendering real standings.
        """
        logger.warning(
            "PointsEngine.process_kill_event is a no-op shim; route kill "
            "events through match recording + PointsCalculator instead.",
            killer_team=killer_team,
            victim_team=victim_team,
        )
        return self.get_leaderboard()

    def get_leaderboard(self) -> List[dict]:
        """Current standings from the single configured calculator."""
        return [
            {
                "rank": t.current_rank,
                "id": t.team_id,
                "name": t.team_name,
                "finishes": t.total_kills,
                "points": t.grand_total,
                "placement_points": t.total_placement_points,
                "wwcd": t.wwcd_count,
            }
            for t in self._calculator.get_rankings()
        ]
