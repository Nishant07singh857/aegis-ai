# backend/app/services/observer_pilot/roster.py
"""DB-backed roster provider for the POV trackers (Phase 29, W4).

The POV tracker (W1 ``state.py``) wants a SYNCHRONOUS callable
``slot_number -> Optional[(team_id, [(player_id, name), ...])]`` —
it runs inside the monitor's sampling threads, where no event loop
may be assumed. The DB is async, so this provider keeps a plain dict
cache: :meth:`CachedRosterProvider.refresh` (async) reloads it from
``observer_slots`` + ``players``, and ``__call__`` (sync) only reads
the dict. The pilot service refreshes it at start and every
``refresh_interval_s`` (default 30 s) from its loop; a slot with no
team bound — or a team with no players — is ABSENT from the cache, so
the callable returns None and POV identity stays honestly UNKNOWN.

Roster order: players are ordered by ``created_at`` then ``id`` —
the insertion order in which the roster was entered (the players
table carries no explicit roster-order column). The display name is
the player's IGN when set, else their name: the spectator nameplate
shows the IGN.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger("aegis.observer_pilot.roster")

RosterBinding = Tuple[str, List[Tuple[str, str]]]


def _default_session_factory():
    from app.db.base import AsyncSessionLocal

    return AsyncSessionLocal


class CachedRosterProvider:
    """Sync roster callable backed by an async-refreshed dict cache."""

    def __init__(
        self,
        session_factory: Optional[Callable[[], Any]] = None,
        refresh_interval_s: float = 30.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._session_factory = session_factory
        self.refresh_interval_s = float(refresh_interval_s)
        self._clock = clock
        self._cache: Dict[int, RosterBinding] = {}
        self._refreshed_at: Optional[float] = None
        self.stats = {"refreshes": 0, "refresh_failures": 0}

    # ── the sync callable the trackers consume ───────────────────

    def __call__(self, slot_number: int) -> Optional[RosterBinding]:
        return self._cache.get(int(slot_number))

    # ── async refresh ────────────────────────────────────────────

    async def refresh(self) -> Dict[int, RosterBinding]:
        """Reload the cache from observer_slots + players.

        Failure-isolated: a DB failure keeps the previous cache and
        counts the failure — a stale roster is honest state, a crash
        in the monitor's provider would not be."""
        from sqlalchemy import select

        from app.db.models.observer_slot import ObserverSlot
        from app.db.models.player import Player

        try:
            factory = self._session_factory or _default_session_factory()
            async with factory() as session:
                slots = (
                    await session.execute(select(ObserverSlot))
                ).scalars().all()
                team_ids = [s.team_id for s in slots if s.team_id]
                players_by_team: Dict[str, List[Tuple[str, str]]] = {}
                if team_ids:
                    players = (
                        await session.execute(
                            select(Player)
                            .where(Player.team_id.in_(team_ids))
                            .order_by(Player.created_at, Player.id)
                        )
                    ).scalars().all()
                    for player in players:
                        name = (player.ign or player.name or "").strip()
                        if not name:
                            continue
                        players_by_team.setdefault(str(player.team_id), []).append(
                            (str(player.id), name)
                        )
                cache: Dict[int, RosterBinding] = {}
                for slot in slots:
                    if not slot.team_id:
                        continue
                    roster = players_by_team.get(str(slot.team_id)) or []
                    if not roster:
                        continue  # team bound but no roster: identity unknown
                    cache[int(slot.slot_number)] = (str(slot.team_id), roster)
            self._cache = cache
            self._refreshed_at = self._clock()
            self.stats["refreshes"] += 1
        except Exception as exc:
            self.stats["refresh_failures"] += 1
            logger.warning("roster_refresh_failed", error=str(exc)[:200])
        return self._cache

    def due_for_refresh(self, now: Optional[float] = None) -> bool:
        if self._refreshed_at is None:
            return True
        return (now if now is not None else self._clock()) - self._refreshed_at >= (
            self.refresh_interval_s
        )

    def status(self) -> Dict[str, Any]:
        return {
            "slots_bound": len(self._cache),
            "refreshed_at": self._refreshed_at,
            "refresh_interval_s": self.refresh_interval_s,
            "stats": dict(self.stats),
        }


__all__ = ["CachedRosterProvider", "RosterBinding"]
