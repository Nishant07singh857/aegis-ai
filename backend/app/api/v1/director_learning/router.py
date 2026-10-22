"""Director Learning API router — mounted at /api/v1 by app/main.py.

Phase 27 (W4) surface over the director learning loop: the decision
journal's stats, the rule-tuning engine's proposals/adjustments, and
the learning mode. This is transparent rule-tuning over counted
outcomes — NOT machine learning (see docs/DIRECTOR_LEARNING.md).

Auth model (mirrors the director router's role dependencies, tightened
per the Phase 27 brief — learning evidence includes who decided what,
so even reads are broadcast-staff only):

  * GET  /director-learning/status        — operator / director / admin
  * GET  /director-learning/proposals     — operator / director / admin
  * GET  /director-learning/adjustments   — operator / director / admin
  * GET  /director-learning/mode          — operator / director / admin
  * POST /director-learning/proposals/{id}/approve  — director / admin
  * POST /director-learning/proposals/{id}/reject   — director / admin
  * POST /director-learning/adjustments/{id}/rollback — director / admin
  * PUT  /director-learning/mode          — director / admin
  * POST /director-learning/analyze       — director / admin

viewer / spotter / observer get 403 on EVERY route here; unauthenticated
callers get 401. Status codes: 404 unknown adjustment id, 409 the row
is already decided / not in a state the action allows (the engine's
message is surfaced), 422 invalid learning mode or query values.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import TokenPayload, require_role
from app.services.director_learning import queries
from app.services.director_learning.engine import LearningEngine, get_engine
from app.services.director_learning.journal import get_journal
from app.services.director_learning.knobs import (
    KNOB_REGISTRY,
    get_provider,
)

from .schemas import (
    AdjustmentResponse,
    KnobState,
    LearningModeResponse,
    LearningModeUpdate,
    LearningStatusResponse,
)

router = APIRouter(prefix="/director-learning", tags=["Director Learning"])

#: Reads: broadcast staff who run the show (operator included).
require_learning_reader = require_role("operator", "director", "admin")
#: Writes: the people accountable for the director's behaviour.
require_learning_writer = require_role("director", "admin")

#: Fates that are a human decision (an expiry is nobody's decision).
_DECIDED_FATES = ("approved", "rejected", "modified")


def _engine() -> LearningEngine:
    """The lifespan-registered engine, or a default-constructed one.

    Production always registers the engine in the lifespan builder;
    the fallback keeps the API functional (default session factory +
    the registered knob provider) if that wiring is absent, instead
    of failing reads that the tables could answer.
    """
    engine = get_engine()
    if engine is not None:
        return engine
    return LearningEngine(provider=get_provider())


def _session_factory():
    """Session factory of the wired learning stack (engine, else journal).

    journal_stats / fate_contributors accept an injectable factory;
    using the SAME factory the engine/journal were built with keeps
    every number on this surface read from the same database they
    write to. None => the queries' default (app.db AsyncSessionLocal).
    """
    for source in (get_engine(), get_journal()):
        factory = getattr(source, "_session_factory", None)
        if factory is not None:
            return factory
    return None


def _not_found(exc: KeyError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
    )


def _conflict(exc: ValueError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail=str(exc)
    )


@router.get("/status", response_model=LearningStatusResponse)
async def get_learning_status(
    _user: TokenPayload = Depends(require_learning_reader),
):
    """Learning status: journal stats, mode, profile, effective knobs.

    Every number is a real query result: journal_stats + fate
    contributors over the learning tables, the provider's refreshed
    learned/factory effective values, and the in-process journal's
    own status (drop counters) — null when no journal runs here.
    """
    engine = _engine()
    factory = _session_factory()
    learning_mode = await engine.get_learning_mode()
    stats = await queries.journal_stats(session_factory=factory)
    contributors = await queries.fate_contributors(session_factory=factory)

    provider = get_provider()
    if provider is not None:
        await provider.refresh()
        effective_learned = provider.effective_all(learned=True)
        effective_factory = provider.effective_all(learned=False)
        snapshot = provider.learned_snapshot()
        profile_exists = bool(snapshot["has_profile"])
        applied_count = int(snapshot["applied_count"])
    else:
        from app.services.director_learning.knobs import KNOB_DEFAULTS

        effective_learned = dict(KNOB_DEFAULTS)
        effective_factory = dict(KNOB_DEFAULTS)
        profile_exists = await engine.profile_exists()
        applied_count = len(
            await engine.list_adjustments(status="applied", limit=10000)
        )

    journal = get_journal()
    decided_fates = sum(
        int(stats["fates_by_fate"].get(fate, 0)) for fate in _DECIDED_FATES
    )
    return {
        "learning_mode": learning_mode,
        "profile": {
            "exists": profile_exists,
            "applied_count": applied_count,
            "contributors": contributors,
            "learned_from": {
                "labeled_decisions": int(stats["labels_total"]),
                "decided_fates": decided_fates,
                "divergences": sum(
                    int(v) for v in stats["divergences_by_kind"].values()
                ),
            },
        },
        "journal_stats": stats,
        "journal": journal.status() if journal is not None else None,
        "knobs": [
            KnobState(
                name=spec.name,
                default=spec.default,
                effective_learned=effective_learned[spec.name],
                effective_factory=effective_factory[spec.name],
                min_value=spec.min_value,
                max_value=spec.max_value,
                step=spec.step,
                unit=spec.unit,
                why=spec.why,
            )
            for spec in KNOB_REGISTRY.values()
        ],
    }


@router.get("/proposals", response_model=List[AdjustmentResponse])
async def list_proposals(
    limit: int = Query(default=100, ge=1, le=500),
    _user: TokenPayload = Depends(require_learning_reader),
):
    """Pending ('proposed') adjustments awaiting a human decision."""
    return await _engine().list_proposals(limit=limit)


@router.post(
    "/proposals/{id}/approve", response_model=AdjustmentResponse
)
async def approve_proposal(
    id: str,
    user: TokenPayload = Depends(require_learning_writer),
):
    """Apply a proposal: proposed -> applied; the provider refreshes,
    so clone mode picks the new value up. 404 unknown id; 409 when the
    row is already decided."""
    try:
        return await _engine().apply_adjustment(
            id, applied_by=user.username
        )
    except KeyError as exc:
        raise _not_found(exc)
    except ValueError as exc:
        raise _conflict(exc)


@router.post("/proposals/{id}/reject", response_model=AdjustmentResponse)
async def reject_proposal(
    id: str,
    user: TokenPayload = Depends(require_learning_writer),
):
    """Reject a proposal: proposed -> rejected (actor recorded).
    404 unknown id; 409 when the row is already decided."""
    try:
        return await _engine().reject_adjustment(
            id, decided_by=user.username
        )
    except KeyError as exc:
        raise _not_found(exc)
    except ValueError as exc:
        raise _conflict(exc)


@router.get("/adjustments", response_model=List[AdjustmentResponse])
async def list_adjustments(
    status_filter: Optional[str] = Query(
        default=None,
        alias="status",
        description='Filter: "proposed" | "applied" | "rejected" | '
        '"rolled_back".',
    ),
    knob: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _user: TokenPayload = Depends(require_learning_reader),
):
    """Adjustments newest-first, optionally filtered by status/knob."""
    return await _engine().list_adjustments(
        status=status_filter, knob=knob, limit=limit
    )


@router.post(
    "/adjustments/{id}/rollback", response_model=AdjustmentResponse
)
async def rollback_adjustment(
    id: str,
    cascade: bool = False,
    user: TokenPayload = Depends(require_learning_writer),
):
    """Roll back an applied adjustment exactly.

    Returns the COMPENSATING row (status 'applied', parent_id = the
    rolled-back row, after_value = its exact pre-adjustment value).
    404 unknown id; 409 when the row is not currently applied, or
    when a newer applied row exists for the same knob and
    ``?cascade=true`` was not passed (cascade unwinds the target
    and every newer applied row for that knob together).
    """
    try:
        return await _engine().rollback_adjustment(
            id, by=user.username, cascade=cascade
        )
    except KeyError as exc:
        raise _not_found(exc)
    except ValueError as exc:
        raise _conflict(exc)


@router.get("/mode", response_model=LearningModeResponse)
async def get_mode(
    _user: TokenPayload = Depends(require_learning_reader),
):
    """Current learning mode: "off" | "suggest" | "auto"."""
    return {"mode": await _engine().get_learning_mode()}


@router.put("/mode", response_model=LearningModeResponse)
async def set_mode(
    payload: LearningModeUpdate,
    user: TokenPayload = Depends(require_learning_writer),
):
    """Set the learning mode. Invalid values are 422 (schema)."""
    try:
        mode = await _engine().set_learning_mode(
            payload.mode, updated_by=user.username
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    return {"mode": mode}


@router.post("/analyze", response_model=List[AdjustmentResponse])
async def analyze_now(
    user: TokenPayload = Depends(require_learning_writer),
):
    """Run one learning analysis pass on demand.

    Returns the adjustment rows this pass created (proposals in
    SUGGEST mode, already-applied rows in AUTO mode, empty in OFF
    mode or when no rule has enough samples — never fabricated).
    """
    return await _engine().run_analysis()
