# backend/app/services/director_learning/queries.py
"""Read helpers over the director-learning tables (Phase 27, W1).

Minimal, session-per-call async helpers for the later waves (W2
labeler, W3 learning engine, W4 UI/API). Every function accepts an
injectable ``session_factory`` (default ``app.db.base.AsyncSessionLocal``)
and returns plain dicts — ORM rows never leak to callers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import func, select

from .models import (
    DirectorDecision,
    DirectorDecisionLabel,
    DirectorShadowDivergence,
    DirectorSuggestionFate,
)


def _factory(session_factory: Optional[Callable[[], Any]]):
    if session_factory is not None:
        return session_factory
    from app.db.base import AsyncSessionLocal

    return AsyncSessionLocal


def _clean(row_dict: Dict[str, Any]) -> Dict[str, Any]:
    row_dict.pop("is_deleted", None)
    row_dict.pop("deleted_at", None)
    return row_dict


async def recent_decisions(
    limit: int = 50,
    mode: Optional[str] = None,
    session_factory: Optional[Callable[[], Any]] = None,
) -> List[Dict[str, Any]]:
    """Most recent journal decisions, newest first."""
    async with _factory(session_factory)() as session:
        query = select(DirectorDecision).order_by(DirectorDecision.ts.desc())
        if mode is not None:
            query = query.where(DirectorDecision.mode == mode)
        query = query.limit(max(1, int(limit)))
        rows = (await session.execute(query)).scalars().all()
        return [_clean(r.to_dict()) for r in rows]


async def decision_by_suggestion_id(
    sid: str,
    session_factory: Optional[Callable[[], Any]] = None,
) -> Optional[Dict[str, Any]]:
    """The journal decision that produced suggestion ``sid`` (latest)."""
    async with _factory(session_factory)() as session:
        query = (
            select(DirectorDecision)
            .where(DirectorDecision.suggestion_id == str(sid))
            .order_by(DirectorDecision.ts.desc())
            .limit(1)
        )
        row = (await session.execute(query)).scalar_one_or_none()
        return _clean(row.to_dict()) if row is not None else None


async def decisions_in_window(
    start: datetime,
    end: datetime,
    session_factory: Optional[Callable[[], Any]] = None,
) -> List[Dict[str, Any]]:
    """Decisions with ``start <= ts <= end``, oldest first."""
    async with _factory(session_factory)() as session:
        query = (
            select(DirectorDecision)
            .where(DirectorDecision.ts >= start, DirectorDecision.ts <= end)
            .order_by(DirectorDecision.ts.asc())
        )
        rows = (await session.execute(query)).scalars().all()
        return [_clean(r.to_dict()) for r in rows]


async def unlabeled_decisions(
    before_ts: datetime,
    limit: int = 200,
    session_factory: Optional[Callable[[], Any]] = None,
) -> List[Dict[str, Any]]:
    """Decisions before ``before_ts`` with NO label row yet (W2's queue).

    Left-join against ``director_decision_labels``: a decision counts
    as labeled when ANY label row references it.
    """
    async with _factory(session_factory)() as session:
        query = (
            select(DirectorDecision)
            .outerjoin(
                DirectorDecisionLabel,
                DirectorDecisionLabel.decision_id == DirectorDecision.id,
            )
            .where(
                DirectorDecision.ts < before_ts,
                DirectorDecisionLabel.id.is_(None),
            )
            .order_by(DirectorDecision.ts.asc())
            .limit(max(1, int(limit)))
        )
        rows = (await session.execute(query)).scalars().all()
        return [_clean(r.to_dict()) for r in rows]


async def fate_for_suggestion(
    sid: str,
    session_factory: Optional[Callable[[], Any]] = None,
) -> Optional[Dict[str, Any]]:
    """The LATEST fate row for a suggestion (a ``modified`` row, when
    present, supersedes the earlier rejected/expired row)."""
    async with _factory(session_factory)() as session:
        query = (
            select(DirectorSuggestionFate)
            .where(DirectorSuggestionFate.suggestion_id == str(sid))
            .order_by(DirectorSuggestionFate.ts.desc())
            .limit(1)
        )
        row = (await session.execute(query)).scalar_one_or_none()
        return _clean(row.to_dict()) if row is not None else None


async def fate_contributors(
    session_factory: Optional[Callable[[], Any]] = None,
) -> List[Dict[str, Any]]:
    """Who decided suggestion fates, and how many each (Phase 27, W4).

    Groups ``director_suggestion_fates`` rows by ``decided_by`` (rows
    with no decider — expiries — are excluded: nobody decided those).
    Returns ``[{username, decided_count}]``, most decisions first.
    This is the honest source for the clone profile's contributor
    list: a name appears only when that user actually decided fates.
    """
    async with _factory(session_factory)() as session:
        query = (
            select(DirectorSuggestionFate.decided_by, func.count())
            .where(DirectorSuggestionFate.decided_by.is_not(None))
            .group_by(DirectorSuggestionFate.decided_by)
            .order_by(
                func.count().desc(), DirectorSuggestionFate.decided_by.asc()
            )
        )
        rows = (await session.execute(query)).all()
        return [
            {"username": str(name), "decided_count": int(count)}
            for name, count in rows
            if name
        ]


async def journal_stats(
    session_factory: Optional[Callable[[], Any]] = None,
) -> Dict[str, Any]:
    """Counts by mode / outcome / fate / divergence kind / labels."""
    async with _factory(session_factory)() as session:

        async def _grouped(model, column):
            query = select(column, func.count()).group_by(column)
            rows = (await session.execute(query)).all()
            return {str(k): int(v) for k, v in rows}

        return {
            "decisions_total": int(
                (
                    await session.execute(
                        select(func.count()).select_from(DirectorDecision)
                    )
                ).scalar()
                or 0
            ),
            "decisions_by_mode": await _grouped(
                DirectorDecision, DirectorDecision.mode
            ),
            "decisions_by_outcome": await _grouped(
                DirectorDecision, DirectorDecision.outcome
            ),
            "fates_by_fate": await _grouped(
                DirectorSuggestionFate, DirectorSuggestionFate.fate
            ),
            "divergences_by_kind": await _grouped(
                DirectorShadowDivergence, DirectorShadowDivergence.kind
            ),
            "labels_total": int(
                (
                    await session.execute(
                        select(func.count()).select_from(DirectorDecisionLabel)
                    )
                ).scalar()
                or 0
            ),
        }
