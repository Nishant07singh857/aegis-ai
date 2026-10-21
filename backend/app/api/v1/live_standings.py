# backend/app/api/v1/live_standings.py
"""Live standings API — mounted at /api/v1 by app/main.py (Phase 24, W6).

Why a NEW router: the pre-existing standings reads cannot carry live
data — ``GET /api/v1/analytics/standings`` is DEMO_MODE-gated and serves
the hardcoded demo dataset, and the ``/overlays/standings`` page reads
demo teams off the /ws/live ``standings`` frame. Neither is a live read
path. This router serves the table accumulated by
``app.services.live_stats.LiveStatsService`` from the real bus event
stream (kills / knocks / revives / team wipes), scored with the
tournament-configured PointsCalculator.

Auth model (matching the Phase 24 flight reads):

  * GET /standings/live — any authenticated user (``get_current_user``)

Honesty contract: every payload is labelled ``live: true`` +
``provisional: true``; mid-match placement is ``null`` (placement points
0) until the event stream truly determines the full elimination order
at match end; per-player damage is ``null`` (no dealt-damage producer
exists from a spectator feed). Post-match manual results supersede the
live table through the normal persisted read paths.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import TokenPayload, get_current_user
from app.services.live_stats import get_live_stats_service

router = APIRouter(prefix="/standings", tags=["Standings"])


@router.get("/live")
async def get_live_standings(
    match_id: Optional[str] = Query(
        default=None,
        description="Match id; defaults to the currently tracked live match.",
    ),
    _user: TokenPayload = Depends(get_current_user),
) -> Dict[str, Any]:
    """Provisional live standings from the real event stream.

    Returns an empty ``standings`` list (still labelled live/provisional)
    when no match events have been observed yet — never demo data.
    """
    service = get_live_stats_service()
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Live stats service is not running (event bus unavailable "
                "at startup); no live standings can be served."
            ),
        )
    return service.live_standings(match_id)
