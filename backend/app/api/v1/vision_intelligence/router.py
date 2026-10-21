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


# ── Phase 24 director-derivation intel ───────────────────────────
#
# Every payload below is derived ONLY from spectator-observable
# signals (see services/vision_intelligence/derivations.py). When the
# subscriber never started, the endpoints report running=false with
# empty structures instead of inventing intel.

_NOT_RUNNING_NOTE = (
    "Vision intelligence subscriber was not started "
    "(event bus unavailable at startup); no derivations exist."
)


def _intel_or_empty(method_name: str, empty: dict) -> dict:
    service = get_vision_intelligence_service()
    if service is None:
        return {"running": False, "note": _NOT_RUNNING_NOTE, **empty}
    return {"running": True, **getattr(service, method_name)()}


@router.get("/intel/rotations")
async def vision_intel_rotations(
    _user: TokenPayload = Depends(get_current_user),
) -> dict:
    """Per-team rotation history: path, bearing, speed, rotation
    start classification + urgency, vehicle/on-foot mode, team shape."""
    return _intel_or_empty(
        "intel_rotations", {"teams": {}, "rotation_events": []}
    )


@router.get("/intel/fights")
async def vision_intel_fights(
    _user: TokenPayload = Depends(get_current_user),
) -> dict:
    """Fight heat (decayed, per area) + active third-party/gatekeep
    intel alerts + the fight detector's active fights."""
    return _intel_or_empty(
        "intel_fights", {"heat": [], "alerts": [], "active_fights": []}
    )


@router.get("/intel/airdrops")
async def vision_intel_airdrops(
    _user: TokenPayload = Depends(get_current_user),
) -> dict:
    """Airdrop log: crates spotted on a team's feed (loot detector or
    spotter tag), contested flag from same-slot combat within 60 s."""
    return _intel_or_empty("intel_airdrops", {"airdrops": []})


@router.get("/intel/knocks")
async def vision_intel_knocks(
    _user: TokenPayload = Depends(get_current_user),
) -> dict:
    """Active knock sessions (duration + bleed-out urgency) and
    recently completed sessions (revived / bled out / finished)."""
    return _intel_or_empty("intel_knocks", {"active": [], "completed": []})


@router.get("/intel/zones")
async def vision_intel_zones(
    _user: TokenPayload = Depends(get_current_user),
) -> dict:
    """Last zone-pull winners/losers snapshot (taken on each new zone
    reveal) plus the snapshot history."""
    return _intel_or_empty("intel_zones", {"last": None, "history": []})
