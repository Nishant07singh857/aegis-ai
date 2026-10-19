"""Live prediction API router — mounted at /api/v1 by app/main.py.

Auth model:

  * GET  /predictions/win-probability  — director, admin, operator
    (broadcast display data; same read plane as the stories API)

Status codes: 401 unauthenticated, 403 insufficient role,
404 no usable live team state for the match (frontend renders "—"
instead of inventing percentages), 422 missing match_id.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TokenPayload, require_role
from app.db.session import get_db

from .schemas import WinProbabilityResponse
from .service import PredictionService

router = APIRouter(prefix="/predictions", tags=["Predictions"])

#: Read access: director + admin + operator (same as the stories API).
require_prediction_reader = require_role("director", "admin", "operator")


def get_prediction_service(db: AsyncSession = Depends(get_db)) -> PredictionService:
    return PredictionService(db)


@router.get("/win-probability", response_model=WinProbabilityResponse)
async def get_win_probability(
    match_id: str = Query(..., description="Match id to score"),
    service: PredictionService = Depends(get_prediction_service),
    user: TokenPayload = Depends(require_prediction_reader),
):
    """Per-team WWCD% for the live match, refreshed every call.

    The frontend polls this endpoint every 30s and glides the cards
    to the new values. ``delta`` is the change vs the previous
    snapshot for the same match (null on first sighting).
    """
    result = await service.win_probability(match_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No usable live team state for this match — no WWCD% "
                "can be computed. The cards should render '—'."
            ),
        )
    return result
