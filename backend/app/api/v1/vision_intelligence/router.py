"""Vision-intelligence status router — mounted at /api/v1 by app/main.py.

  * GET /vision-intelligence/status — any authenticated user.

Reports the Phase 22 subscriber's honest state: subscription, event
counts, attribution drops (unmapped / slotless), active fights from
vision signals, zone analytics, story auto-flagger stats and the
anti-cheat checks registry summary. When the service was never built
(e.g. the bus failed to connect at startup) it reports running=false
instead of inventing a healthy state.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import TokenPayload, get_current_user
from app.services.vision_intelligence.anti_cheat import checks_registry
from app.services.vision_intelligence.service import (
    get_vision_intelligence_service,
)

router = APIRouter(prefix="/vision-intelligence", tags=["Vision Intelligence"])


@router.get("/status")
async def vision_intelligence_status(
    _user: TokenPayload = Depends(get_current_user),
) -> dict:
    service = get_vision_intelligence_service()
    if service is None:
        return {
            "running": False,
            "note": (
                "Vision intelligence subscriber was not started "
                "(event bus unavailable at startup). Vision events are "
                "published but not consumed."
            ),
            "checks": checks_registry(),
        }
    snapshot = service.snapshot()
    snapshot["checks"] = checks_registry()
    return snapshot
