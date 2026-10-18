"""AEGIS AI — Match Point / Smash Rule indicator service (Phase 10, Worker W2).

Configurable rules engine that decides which teams are "on match point".

Configuration (never hardcoded in the logic — every value comes from config):

    MATCH_POINT_ENABLED        bool  — master switch (default: true)
    MATCH_POINT_MODE           str   — "points_threshold" | "final_day_chicken"
    MATCH_POINT_THRESHOLD      int   — points needed for match point (default: 40)
    MATCH_POINT_FINAL_DAY      int   — tournament day the smash rule activates
                                       (default: 3)
    MATCH_POINT_CURRENT_DAY    int   — tournament day currently being played
                                       (default: 1)

Modes
-----
* ``points_threshold``: any team with ``total_points >= threshold`` is on
  match point.
* ``final_day_chicken``: like ``points_threshold``, but match point only
  activates when the played day reaches ``final_day_number``. On that day a
  team on match point wins the championship with a WWCD — hence the reason
  strings.

Input standings are supplied by the caller (``POST /match-point/evaluate``).
The match service exposes no tournament-wide standings endpoint, so the
honest design is: whoever drives the broadcast (operator UI / analytics
pipeline) posts the standings it is showing. Nothing is read from thin air.

The service never imports ``app.main`` (that would be circular). Every
config change and every evaluation publishes::

    {"type": "matchpoint.state", "data": {<full match-point state snapshot>}}

``app/main.py`` registers ``ConnectionManager.broadcast`` as a listener at
startup, which fans the messages out over ``/ws/live`` — the exact pattern
used by ``DirectorService``.
"""

from __future__ import annotations

import asyncio
import copy
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

import structlog

logger = structlog.get_logger("aegis.match_point")

#: WS message type pushed to /ws/live on config changes and evaluations.
WS_MESSAGE_TYPE = "matchpoint.state"

MODES: tuple[str, ...] = ("points_threshold", "final_day_chicken")


class InvalidMatchPointConfigError(ValueError):
    """Raised when a match-point config update is invalid."""


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MatchPointService:
    """In-memory single source of truth for match-point state."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._listeners: List[Callable[[Dict[str, Any]], Awaitable[None]]] = []
        self._config: Dict[str, Any] = {
            "enabled": _env_bool("MATCH_POINT_ENABLED", True),
            "mode": _env_str("MATCH_POINT_MODE", "points_threshold"),
            "points_threshold": _env_int("MATCH_POINT_THRESHOLD", 40),
            "final_day_number": _env_int("MATCH_POINT_FINAL_DAY", 3),
            "current_day": _env_int("MATCH_POINT_CURRENT_DAY", 1),
        }
        if self._config["mode"] not in MODES:
            logger.warning(
                "invalid MATCH_POINT_MODE %r, falling back to 'points_threshold'",
                self._config["mode"],
            )
            self._config["mode"] = "points_threshold"
        self._last_evaluation: Optional[Dict[str, Any]] = None
        self._updated_at: str = _utcnow_iso()

    # ── listeners ────────────────────────────────────────────────

    def add_listener(
        self, callback: Callable[[Dict[str, Any]], Awaitable[None]]
    ) -> None:
        """Register an async ``callback(message)`` invoked on state changes."""
        self._listeners.append(callback)

    async def _notify(self, state: Dict[str, Any]) -> None:
        message = {"type": WS_MESSAGE_TYPE, "data": state}
        for listener in list(self._listeners):
            await listener(message)

    # ── reads ────────────────────────────────────────────────────

    def get_config(self) -> Dict[str, Any]:
        return copy.deepcopy(self._config)

    def snapshot(self) -> Dict[str, Any]:
        """Full match-point state (safe to send to any viewer)."""
        return {
            "config": self.get_config(),
            "last_evaluation": copy.deepcopy(self._last_evaluation),
            "updated_at": self._updated_at,
        }

    # ── writes ───────────────────────────────────────────────────

    async def set_config(
        self, updated_by: str = "operator", **updates: Any
    ) -> Dict[str, Any]:
        """Update match-point configuration. Accepted keys: enabled, mode,
        points_threshold, final_day_number, current_day. Unknown keys are
        rejected; invalid values raise :class:`InvalidMatchPointConfigError`."""
        allowed = {
            "enabled",
            "mode",
            "points_threshold",
            "final_day_number",
            "current_day",
        }
        unknown = set(updates) - allowed
        if unknown:
            raise InvalidMatchPointConfigError(
                f"Unknown match-point config keys: {sorted(unknown)}."
            )
        if "mode" in updates and updates["mode"] not in MODES:
            raise InvalidMatchPointConfigError(
                f"Invalid mode {updates['mode']!r}: must be one of {list(MODES)}."
            )
        for key in ("points_threshold", "final_day_number", "current_day"):
            if key in updates:
                value = updates[key]
                if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                    raise InvalidMatchPointConfigError(
                        f"{key} must be a positive integer, got {value!r}."
                    )
        if "enabled" in updates and not isinstance(updates["enabled"], bool):
            raise InvalidMatchPointConfigError(
                f"enabled must be a boolean, got {updates['enabled']!r}."
            )
        async with self._lock:
            self._config.update(updates)
            self._updated_at = _utcnow_iso()
            state = copy.deepcopy(self.snapshot())
        logger.info("match-point config updated by %s: %s", updated_by, updates)
        await self._notify(state)
        return state

    async def evaluate(
        self,
        standings: List[Dict[str, Any]],
        day: Optional[int] = None,
        updated_by: str = "operator",
    ) -> Dict[str, Any]:
        """Evaluate which teams are on match point.

        ``standings``: list of ``{"team_id": str, "total_points": number,
        "team_name": str (optional)}``. ``day`` overrides the configured
        ``current_day`` for this evaluation only.

        Returns the stored evaluation result (also retrievable via
        ``GET /match-point/status``).
        """
        async with self._lock:
            config = copy.deepcopy(self._config)
            result = self._run_evaluation(config, standings, day)
            self._last_evaluation = copy.deepcopy(result)
            self._updated_at = _utcnow_iso()
            state = copy.deepcopy(self.snapshot())
        logger.info(
            "match-point evaluated by %s: %d team(s) on match point",
            updated_by,
            len(result["on_match_point"]),
        )
        await self._notify(state)
        return state

    # ── engine ───────────────────────────────────────────────────

    @staticmethod
    def _run_evaluation(
        config: Dict[str, Any],
        standings: List[Dict[str, Any]],
        day: Optional[int],
    ) -> Dict[str, Any]:
        evaluated_at = _utcnow_iso()
        if not config["enabled"]:
            return {
                "evaluated_at": evaluated_at,
                "day": day if day is not None else config["current_day"],
                "on_match_point": [],
                "note": "Match-point indicator is disabled.",
            }
        eff_day = day if day is not None else config["current_day"]
        threshold = config["points_threshold"]

        if config["mode"] == "final_day_chicken" and eff_day < config[
            "final_day_number"
        ]:
            return {
                "evaluated_at": evaluated_at,
                "day": eff_day,
                "on_match_point": [],
                "note": (
                    f"Match point activates on day {config['final_day_number']} "
                    f"(currently day {eff_day})."
                ),
            }

        on_match_point: List[Dict[str, Any]] = []
        for entry in standings:
            try:
                total = float(entry.get("total_points", 0))
            except (TypeError, ValueError):
                total = 0.0
            if total >= threshold:
                if config["mode"] == "final_day_chicken":
                    reason = (
                        f"On match point — a WWCD on finals day {eff_day} wins "
                        "the championship."
                    )
                else:
                    reason = (
                        f"Reached the {threshold:g}-point match-point threshold."
                    )
                on_match_point.append(
                    {
                        "team_id": entry.get("team_id"),
                        "team_name": entry.get("team_name"),
                        "total_points": total,
                        "reason": reason,
                    }
                )
        on_match_point.sort(key=lambda t: t["total_points"], reverse=True)
        return {
            "evaluated_at": evaluated_at,
            "day": eff_day,
            "on_match_point": on_match_point,
            "note": (
                f"{len(on_match_point)} team(s) on match point."
                if on_match_point
                else "No team is on match point yet."
            ),
        }


#: Process-wide singleton used by the API router and app/main.py.
match_point_service = MatchPointService()
