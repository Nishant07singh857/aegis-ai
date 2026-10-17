"""Team service: DB-first reads with demo fallback, DB-required writes.

Reads try the database first and fall back to the in-memory demo dataset
(``app/api/v1/teams/demo_data.py``) whenever the database is unreachable OR
the teams table exists but is empty. The first ``session.execute()`` is the
point where a dead database surfaces — that is where every read catches and
falls back, and where every write converts the failure into a 503.

Writes only touch the database. When it is down they raise 503 with a clear
message instead of silently mutating in-memory state.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.team import Team
from app.db.repositories.team_repository import TeamRepository
from .demo_data import DEMO_TEAMS
from .schemas import TeamCreate, TeamPatch, TeamResponse, TeamUpdate

logger = logging.getLogger("aegis.teams")


def _demo_response(d: dict) -> TeamResponse:
    color = d.get("color")
    return TeamResponse(
        id=d["id"],
        name=d["name"],
        tag=d.get("tag"),
        logo=d.get("logo"),
        logo_url=None,
        color=color,
        colors={"primary": color} if color else {},
        rank=d.get("rank"),
        kills=d.get("kills"),
        placement_pts=d.get("placement_pts"),
        total_pts=d.get("total_pts"),
        wwcd=d.get("wwcd"),
        alive=d.get("alive"),
        players=list(d.get("players", [])),
        source="demo",
    )


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

    # ---------- reads (DB-first, demo fallback) ----------

    async def list_teams(self, skip: int = 0, limit: int = 100) -> List[TeamResponse]:
        if self.repo is not None:
            try:
                rows = await self.repo.get_all(skip=skip, limit=limit, order_by="name")
            except Exception as exc:  # DB down — first execute() surfaces here
                logger.warning(f"DB read failed, falling back to demo teams: {exc}")
                rows = None
            if rows:
                return [_db_response(t) for t in rows]
            if rows is not None:
                logger.info("Teams table is empty, falling back to demo teams")
        return [_demo_response(d) for d in DEMO_TEAMS][skip : skip + limit]

    async def get_team(self, team_id: str) -> Optional[TeamResponse]:
        if self.repo is not None:
            try:
                row = await self.repo.get(team_id)
            except Exception as exc:
                logger.warning(f"DB read failed, falling back to demo teams: {exc}")
                row = None
            if row is not None:
                return _db_response(row)
            # DB up but row missing (or table empty) — check demo data too
        demo = next((d for d in DEMO_TEAMS if d["id"] == team_id), None)
        return _demo_response(demo) if demo else None

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
        return _db_response(updated) if updated else None

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
        return _db_response(updated) if updated else None

    async def delete_team(self, team_id: str) -> bool:
        repo = self._require_db()
        try:
            return await repo.delete(team_id, soft=True)
        except Exception as exc:
            raise _db_unavailable(exc)
