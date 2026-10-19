"""Team service: DB-backed master data with honest-empty reads, DB-required writes.

Reads NEVER invent teams: when the database is unreachable they raise 503,
when the teams table is empty they return an empty list, and when no DB
session is configured (tests, local dev) they return honest empties. The
demo dataset (``app/api/v1/teams/demo_data.py``) is NOT imported here — it
exists only as an explicit simulator source.

Writes only touch the database. When it is down they raise 503 with a clear
message instead of silently mutating in-memory state.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_manager
from app.db.models.team import Team
from app.db.repositories.team_repository import TeamRepository
from .schemas import TeamCreate, TeamPatch, TeamResponse, TeamUpdate

logger = logging.getLogger("aegis.teams")

# ── read cache (60s TTL) ──────────────────────────────────────────────
# Two tiers: Redis via cache_manager when available, plus a process-local
# TTL dict so reads stay fast (and tests stay DB/Redis-free) when Redis
# is down. A generation counter in every key makes invalidation
# O(1): writes bump the generation and old Redis entries simply expire.
_TEAMS_CACHE_TTL = 60
_cache_generation = 0
_local_cache: Dict[str, Tuple[float, Any]] = {}  # key -> (expires_at, value)


def _cache_key(kind: str, *parts: str) -> str:
    return f"teams:v{_cache_generation}:{kind}:" + ":".join(parts)


def _serialize_teams(value: Any) -> Any:
    if isinstance(value, list):
        return {"kind": "list", "items": [t.model_dump(mode="json") for t in value]}
    if value is None:
        return {"kind": "none"}
    return {"kind": "one", "item": value.model_dump(mode="json")}


def _deserialize_teams(payload: Any) -> Any:
    kind = payload.get("kind")
    if kind == "list":
        return [TeamResponse(**item) for item in payload["items"]]
    if kind == "one":
        return TeamResponse(**payload["item"])
    return None


async def _cache_get_teams(key: str) -> Tuple[bool, Any]:
    """Return (hit, value). Never raises — a cache failure is a miss."""
    try:
        payload = await cache_manager.get(key)
        if payload is not None:
            return True, _deserialize_teams(payload)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"Teams Redis cache read failed: {exc}")
    entry = _local_cache.get(key)
    if entry is not None:
        expires_at, value = entry
        if expires_at > time.time():
            return True, value
        del _local_cache[key]
    return False, None


async def _cache_set_teams(key: str, value: Any) -> None:
    """Populate both tiers. Never raises."""
    _local_cache[key] = (time.time() + _TEAMS_CACHE_TTL, value)
    try:
        await cache_manager.set(key, _serialize_teams(value), ttl=_TEAMS_CACHE_TTL)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"Teams Redis cache write failed: {exc}")


def _invalidate_teams_cache() -> None:
    """Bump the cache generation and drop the local tier.

    Redis entries under the old generation are left to expire via TTL —
    no key scanning needed, and stale reads are impossible because the
    generation is part of every key.
    """
    global _cache_generation
    _cache_generation += 1
    _local_cache.clear()
    logger.debug(f"Teams cache invalidated (generation {_cache_generation})")


def _db_response(team: Team) -> TeamResponse:
    """Map a DB row. The teams table has no stats columns, so stats are
    honestly reported as None."""
    colors = team.colors or {}
    return TeamResponse(
        id=str(team.id),
        name=team.name,
        tag=team.tag,
        logo=None,
        logo_url=team.logo_url,
        color=colors.get("primary"),
        colors=colors,
        region=team.region,
        is_active=bool(team.is_active),
        rank=None,
        kills=None,
        placement_pts=None,
        total_pts=None,
        wwcd=None,
        alive=None,
        players=None,
        source="db",
    )


def _db_unavailable(exc: Exception) -> HTTPException:
    logger.warning(f"Database unavailable for teams write: {exc}")
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Database unavailable — team changes require a connected database.",
    )


class TeamService:
    def __init__(self, db: Optional[AsyncSession] = None):
        self.db = db
        self.repo = TeamRepository(db) if db is not None else None

    # ---------- internal helpers ----------

    def _require_db(self) -> TeamRepository:
        if self.repo is None:
            raise _db_unavailable(RuntimeError("no database session"))
        return self.repo

    # ---------- reads (cached 60s, honest empty — NEVER fake teams) ----------

    async def list_teams(self, skip: int = 0, limit: int = 100) -> List[TeamResponse]:
        key = _cache_key("list", str(skip), str(limit))
        hit, cached = await _cache_get_teams(key)
        if hit:
            return cached
        result = await self._fetch_list_teams(skip=skip, limit=limit)
        await _cache_set_teams(key, result)
        return result

    async def _fetch_list_teams(self, skip: int = 0, limit: int = 100) -> List[TeamResponse]:
        if self.repo is None:
            return []  # no DB configured — honest empty, never fake
        try:
            rows = await self.repo.get_all(skip=skip, limit=limit, order_by="name")
        except Exception as exc:  # DB down — first execute() surfaces here
            logger.warning(f"DB read failed for teams list: {exc}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database unavailable — cannot list teams right now.",
            )
        return [_db_response(t) for t in rows]

    async def get_team(self, team_id: str) -> Optional[TeamResponse]:
        key = _cache_key("get", team_id)
        hit, cached = await _cache_get_teams(key)
        if hit:
            return cached
        result = await self._fetch_team(team_id)
        await _cache_set_teams(key, result)
        return result

    async def _fetch_team(self, team_id: str) -> Optional[TeamResponse]:
        if self.repo is None:
            return None
        try:
            row = await self.repo.get(team_id)
        except Exception as exc:
            logger.warning(f"DB read failed for team {team_id}: {exc}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database unavailable — cannot fetch team right now.",
            )
        return _db_response(row) if row is not None else None

    # ---------- writes (DB required) ----------

    async def create_team(self, payload: TeamCreate) -> TeamResponse:
        repo = self._require_db()
        try:
            if await repo.get_by_name(payload.name) is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Team with name '{payload.name}' already exists.",
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise _db_unavailable(exc)
        try:
            team = await repo.create(**payload.model_dump())
        except IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Team with name '{payload.name}' already exists.",
            )
        except (SQLAlchemyError, Exception) as exc:
            raise _db_unavailable(exc)
        _invalidate_teams_cache()
        return _db_response(team)

    async def update_team(self, team_id: str, payload: TeamUpdate) -> Optional[TeamResponse]:
        repo = self._require_db()
        try:
            existing = await repo.get(team_id)
        except Exception as exc:
            raise _db_unavailable(exc)
        if existing is None:
            return None
        if payload.name != existing.name:
            try:
                clash = await repo.get_by_name(payload.name)
            except Exception as exc:
                raise _db_unavailable(exc)
            if clash is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Team with name '{payload.name}' already exists.",
                )
        try:
            updated = await repo.update(team_id, **payload.model_dump())
        except IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Team with name '{payload.name}' already exists.",
            )
        except Exception as exc:
            raise _db_unavailable(exc)
        if updated is None:
            return None
        _invalidate_teams_cache()
        return _db_response(updated)

    async def patch_team(self, team_id: str, payload: TeamPatch) -> Optional[TeamResponse]:
        repo = self._require_db()
        changes = {k: v for k, v in payload.model_dump().items() if v is not None}
        try:
            existing = await repo.get(team_id)
        except Exception as exc:
            raise _db_unavailable(exc)
        if existing is None:
            return None
        new_name = changes.get("name")
        if new_name and new_name != existing.name:
            try:
                clash = await repo.get_by_name(new_name)
            except Exception as exc:
                raise _db_unavailable(exc)
            if clash is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Team with name '{new_name}' already exists.",
                )
        try:
            updated = await repo.update(team_id, **changes)
        except IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Team name already exists.",
            )
        except Exception as exc:
            raise _db_unavailable(exc)
        if updated is None:
            return None
        _invalidate_teams_cache()
        return _db_response(updated)

    async def delete_team(self, team_id: str) -> bool:
        repo = self._require_db()
        try:
            deleted = await repo.delete(team_id, soft=True)
        except Exception as exc:
            raise _db_unavailable(exc)
        if deleted:
            _invalidate_teams_cache()
        return deleted
