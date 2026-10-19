"""Tournament service: DB-backed tournament config with honest-empty reads.

Exactly one tournament may be ``active`` at a time — ``activate`` enforces
this by demoting every other active tournament to ``inactive``.

Reads are cached for 60s through the shared two-tier :class:`ServiceCache`
(Redis + process-local). Every write (including ``activate``) bumps the
cache generation so admin edits reflect promptly, which also keeps the
points calculator's view of the active tournament fresh.

Reads never invent data: no database session means an empty result, an
unreachable database surfaces as HTTP 503, and a missing row is a 404.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.service_cache import ServiceCache
from app.db.models.tournament import Tournament
from app.db.repositories.tournament_repository import STATUS_ACTIVE, TournamentRepository
from .schemas import (
    TournamentCreate,
    TournamentPatch,
    TournamentResponse,
    TournamentUpdate,
)

logger = logging.getLogger("aegis.tournament")


def _serialize(value: object) -> object:
    if isinstance(value, list):
        return {"kind": "list", "items": [t.model_dump(mode="json") for t in value]}
    if value is None:
        return {"kind": "none"}
    return {"kind": "one", "item": value.model_dump(mode="json")}  # type: ignore[union-attr]


def _deserialize(payload: object) -> object:
    kind = payload.get("kind")  # type: ignore[union-attr]
    if kind == "list":
        return [TournamentResponse(**item) for item in payload["items"]]  # type: ignore[index]
    if kind == "one":
        return TournamentResponse(**payload["item"])  # type: ignore[index]
    return None


def _response(t: Tournament) -> TournamentResponse:
    return TournamentResponse(
        id=str(t.id),
        name=t.name,
        slug=t.slug,
        description=t.description,
        status=t.status,
        is_active=(t.status == STATUS_ACTIVE),
        start_date=t.start_date,
        end_date=t.end_date,
        total_matches=t.total_matches or 0,
        points_per_kill=t.points_per_kill if t.points_per_kill is not None else 1,
        placement_points=dict(t.placement_points or {}),
        rules=dict(t.rules or {}),
        prize_pool=t.prize_pool,
        organizer=t.organizer,
        is_public=bool(t.is_public),
    )


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "tournament"


def _db_unavailable(exc: Exception) -> HTTPException:
    logger.warning(f"Database unavailable for tournament write: {exc}")
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Database unavailable — tournament changes require a connected database.",
    )


class TournamentService:
    def __init__(
        self,
        db: Optional[AsyncSession] = None,
        repo: Optional[TournamentRepository] = None,
    ):
        self.db = db
        self.repo = repo if repo is not None else (TournamentRepository(db) if db is not None else None)
        self.cache = ServiceCache("tournament", serialize=_serialize, deserialize=_deserialize)

    # ---------- internal helpers ----------

    def _require_repo(self) -> TournamentRepository:
        if self.repo is None:
            raise _db_unavailable(RuntimeError("no database session"))
        return self.repo

    async def _ensure_slug_unique(self, repo: TournamentRepository, slug: str, exclude_id: Optional[str] = None) -> None:
        try:
            clash = await repo.get_by_slug(slug)
        except Exception as exc:
            raise _db_unavailable(exc)
        if clash is not None and str(clash.id) != exclude_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Tournament with slug '{slug}' already exists.",
            )

    # ---------- reads (cached 60s, honest empty) ----------

    async def list_tournaments(self, skip: int = 0, limit: int = 100) -> List[TournamentResponse]:
        hit, cached = await self.cache.get("list", str(skip), str(limit))
        if hit:
            return cached  # type: ignore[return-value]
        result = await self._fetch_list_tournaments(skip=skip, limit=limit)
        await self.cache.set("list", result, str(skip), str(limit))
        return result

    async def _fetch_list_tournaments(self, skip: int = 0, limit: int = 100) -> List[TournamentResponse]:
        if self.repo is None:
            return []  # no DB configured — honest empty, never fake
        try:
            rows = await self.repo.list_all(skip=skip, limit=limit)
        except Exception as exc:
            logger.warning(f"DB read failed for tournament list: {exc}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database unavailable — cannot list tournaments right now.",
            )
        return [_response(t) for t in rows]

    async def get_tournament(self, tournament_id: str) -> Optional[TournamentResponse]:
        hit, cached = await self.cache.get("get", tournament_id)
        if hit:
            return cached  # type: ignore[return-value]
        result = await self._fetch_tournament(tournament_id)
        await self.cache.set("get", result, tournament_id)
        return result

    async def _fetch_tournament(self, tournament_id: str) -> Optional[TournamentResponse]:
        if self.repo is None:
            return None
        try:
            row = await self.repo.get(tournament_id)
        except Exception as exc:
            logger.warning(f"DB read failed for tournament {tournament_id}: {exc}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database unavailable — cannot fetch tournament right now.",
            )
        return _response(row) if row is not None else None

    async def get_active_tournament(self) -> Optional[TournamentResponse]:
        """The single active tournament, if one exists."""
        hit, cached = await self.cache.get("active")
        if hit:
            return cached  # type: ignore[return-value]
        result = await self._fetch_active_tournament()
        await self.cache.set("active", result)
        return result

    async def _fetch_active_tournament(self) -> Optional[TournamentResponse]:
        if self.repo is None:
            return None
        try:
            row = await self.repo.get_active()
        except Exception as exc:
            logger.warning(f"DB read failed for active tournament: {exc}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database unavailable — cannot fetch active tournament right now.",
            )
        return _response(row) if row is not None else None

    # ---------- writes (DB required; invalidate cache) ----------

    async def create_tournament(self, payload: TournamentCreate) -> TournamentResponse:
        repo = self._require_repo()
        slug = payload.slug.strip() if payload.slug else _slugify(payload.name)
        await self._ensure_slug_unique(repo, slug)
        data = payload.model_dump()
        data["slug"] = slug
        try:
            tournament = await repo.create(**data)
        except IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Tournament with slug '{slug}' already exists.",
            )
        except (SQLAlchemyError, Exception) as exc:
            raise _db_unavailable(exc)
        self.cache.invalidate()
        return _response(tournament)

    async def update_tournament(
        self, tournament_id: str, payload: TournamentUpdate
    ) -> Optional[TournamentResponse]:
        repo = self._require_repo()
        try:
            existing = await repo.get(tournament_id)
        except Exception as exc:
            raise _db_unavailable(exc)
        if existing is None:
            return None
        await self._ensure_slug_unique(repo, payload.slug, exclude_id=tournament_id)
        try:
            updated = await repo.update(tournament_id, **payload.model_dump())
        except IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Tournament with slug '{payload.slug}' already exists.",
            )
        except Exception as exc:
            raise _db_unavailable(exc)
        if updated is None:
            return None
        self.cache.invalidate()
        return _response(updated)

    async def patch_tournament(
        self, tournament_id: str, payload: TournamentPatch
    ) -> Optional[TournamentResponse]:
        repo = self._require_repo()
        changes = {k: v for k, v in payload.model_dump().items() if v is not None}
        try:
            existing = await repo.get(tournament_id)
        except Exception as exc:
            raise _db_unavailable(exc)
        if existing is None:
            return None
        if "slug" in changes:
            await self._ensure_slug_unique(repo, changes["slug"], exclude_id=tournament_id)
        try:
            updated = await repo.update(tournament_id, **changes)
        except IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Tournament slug already exists.",
            )
        except Exception as exc:
            raise _db_unavailable(exc)
        if updated is None:
            return None
        self.cache.invalidate()
        return _response(updated)

    async def delete_tournament(self, tournament_id: str) -> bool:
        repo = self._require_repo()
        try:
            deleted = await repo.delete(tournament_id, soft=True)
        except Exception as exc:
            raise _db_unavailable(exc)
        if deleted:
            self.cache.invalidate()
        return deleted

    async def activate_tournament(self, tournament_id: str) -> Optional[TournamentResponse]:
        """Make this tournament the single active one.

        Sets ``status="active"`` on the target and demotes every other active
        tournament to ``"inactive"``. Returns the activated tournament, or
        None when the id does not exist.
        """
        repo = self._require_repo()
        try:
            existing = await repo.get(tournament_id)
        except Exception as exc:
            raise _db_unavailable(exc)
        if existing is None:
            return None
        try:
            demoted = await repo.deactivate_others(tournament_id)
            updated = await repo.update(tournament_id, status=STATUS_ACTIVE)
        except Exception as exc:
            raise _db_unavailable(exc)
        if updated is None:
            return None
        self.cache.invalidate()
        logger.info(f"Tournament {tournament_id} activated; {demoted} other(s) demoted to inactive")
        return _response(updated)
