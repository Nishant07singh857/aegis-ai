"""Player service: DB-backed master data with honest-empty reads.

Reads are cached for 60s through the shared two-tier :class:`ServiceCache`
(Redis + process-local). Every write bumps the cache generation so admin
edits reflect promptly.

Reads never invent data: no database session means an empty result, an
unreachable database surfaces as HTTP 503, and a missing row is a 404.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.service_cache import ServiceCache
from app.db.base import BaseRepository
from app.db.models.player import Player
from app.db.repositories.team_repository import TeamRepository
from .schemas import (
    PlayerCreate,
    PlayerPatch,
    PlayerResponse,
    PlayerUpdate,
)

logger = logging.getLogger("aegis.players")


def _serialize(value: object) -> object:
    if isinstance(value, list):
        return {"kind": "list", "items": [p.model_dump(mode="json") for p in value]}
    if value is None:
        return {"kind": "none"}
    return {"kind": "one", "item": value.model_dump(mode="json")}  # type: ignore[union-attr]


def _deserialize(payload: object) -> object:
    kind = payload.get("kind")  # type: ignore[union-attr]
    if kind == "list":
        return [PlayerResponse(**item) for item in payload["items"]]  # type: ignore[index]
    if kind == "one":
        return PlayerResponse(**payload["item"])  # type: ignore[index]
    return None


def _response(player: Player) -> PlayerResponse:
    return PlayerResponse(
        id=str(player.id),
        team_id=str(player.team_id) if player.team_id else None,
        name=player.name,
        ign=player.ign,
        role=player.role,
        avatar_url=player.avatar_url,
        is_active=bool(player.is_active),
    )


def _db_unavailable(exc: Exception) -> HTTPException:
    logger.warning(f"Database unavailable for players write: {exc}")
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Database unavailable — player changes require a connected database.",
    )


class PlayerService:
    def __init__(
        self,
        db: Optional[AsyncSession] = None,
        repo: Optional[BaseRepository[Player]] = None,
    ):
        self.db = db
        # Explicit repo injection (tests) wins; otherwise build from the session.
        self.repo = repo if repo is not None else (BaseRepository(Player, db) if db is not None else None)
        self.teams = TeamRepository(db) if db is not None else None
        self.cache = ServiceCache("players", serialize=_serialize, deserialize=_deserialize)

    # ---------- internal helpers ----------

    def _require_repo(self) -> BaseRepository[Player]:
        if self.repo is None:
            raise _db_unavailable(RuntimeError("no database session"))
        return self.repo

    @staticmethod
    def _normalize_team_id(team_id: Optional[str]) -> Optional[str]:
        """Empty string means unassigned (admin UI sends "" for no team)."""
        return team_id or None

    async def _check_team(self, team_id: Optional[str]) -> None:
        """Raise 404 if a non-null team_id does not reference a real team."""
        if not team_id:
            return
        try:
            exists = await self.teams.exists(team_id) if self.teams is not None else None
        except Exception as exc:
            raise _db_unavailable(exc)
        if not exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Team '{team_id}' not found — cannot assign player to it.",
            )

    # ---------- reads (cached 60s, honest empty) ----------

    async def list_players(
        self, skip: int = 0, limit: int = 100, team_id: Optional[str] = None
    ) -> List[PlayerResponse]:
        hit, cached = await self.cache.get("list", str(skip), str(limit), team_id or "-")
        if hit:
            return cached  # type: ignore[return-value]
        result = await self._fetch_list_players(skip=skip, limit=limit, team_id=team_id)
        await self.cache.set("list", result, str(skip), str(limit), team_id or "-")
        return result

    async def _fetch_list_players(
        self, skip: int = 0, limit: int = 100, team_id: Optional[str] = None
    ) -> List[PlayerResponse]:
        if self.repo is None:
            return []  # no DB configured — honest empty, never fake
        try:
            filters = {"team_id": team_id} if team_id else None
            rows = await self.repo.get_all(skip=skip, limit=limit, order_by="name", filters=filters)
        except Exception as exc:
            logger.warning(f"DB read failed for players list: {exc}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database unavailable — cannot list players right now.",
            )
        return [_response(p) for p in rows]

    async def get_player(self, player_id: str) -> Optional[PlayerResponse]:
        hit, cached = await self.cache.get("get", player_id)
        if hit:
            return cached  # type: ignore[return-value]
        result = await self._fetch_player(player_id)
        await self.cache.set("get", result, player_id)
        return result

    async def _fetch_player(self, player_id: str) -> Optional[PlayerResponse]:
        if self.repo is None:
            return None
        try:
            row = await self.repo.get(player_id)
        except Exception as exc:
            logger.warning(f"DB read failed for player {player_id}: {exc}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database unavailable — cannot fetch player right now.",
            )
        return _response(row) if row is not None else None

    # ---------- writes (DB required; invalidate cache) ----------

    async def create_player(self, payload: PlayerCreate) -> PlayerResponse:
        repo = self._require_repo()
        data = payload.model_dump()
        data["team_id"] = self._normalize_team_id(data.get("team_id"))
        await self._check_team(data["team_id"])
        try:
            player = await repo.create(**data)
        except (SQLAlchemyError, Exception) as exc:
            raise _db_unavailable(exc)
        self.cache.invalidate()
        return _response(player)

    async def update_player(self, player_id: str, payload: PlayerUpdate) -> Optional[PlayerResponse]:
        repo = self._require_repo()
        data = payload.model_dump()
        data["team_id"] = self._normalize_team_id(data.get("team_id"))
        await self._check_team(data["team_id"])
        try:
            updated = await repo.update(player_id, **data)
        except Exception as exc:
            raise _db_unavailable(exc)
        if updated is None:
            return None
        self.cache.invalidate()
        return _response(updated)

    async def patch_player(self, player_id: str, payload: PlayerPatch) -> Optional[PlayerResponse]:
        repo = self._require_repo()
        changes = {k: v for k, v in payload.model_dump().items() if v is not None}
        if "team_id" in changes:
            changes["team_id"] = self._normalize_team_id(changes["team_id"])
            await self._check_team(changes["team_id"])
        try:
            updated = await repo.update(player_id, **changes)
        except Exception as exc:
            raise _db_unavailable(exc)
        if updated is None:
            return None
        self.cache.invalidate()
        return _response(updated)

    async def delete_player(self, player_id: str) -> bool:
        repo = self._require_repo()
        try:
            deleted = await repo.delete(player_id, soft=True)
        except Exception as exc:
            raise _db_unavailable(exc)
        if deleted:
            self.cache.invalidate()
        return deleted
