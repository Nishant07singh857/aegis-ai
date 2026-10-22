"""Observer Pilot API router — mounted at /api/v1 by app/main.py.

Phase 29 (W4) surface over the per-feed POV auto-pilot: status,
kill-switch, semi_auto suggestion approve/reject, manual override,
and the durable POV journal.

Auth model (mirrors the director router):
  * GET  /observer-pilot/status     — operator+ (broadcast control roles)
  * GET  /observer-pilot/decisions  — operator+
  * POST /observer-pilot/kill-switch              — operator+
  * POST /observer-pilot/suggestions/{id}/approve — operator+
  * POST /observer-pilot/suggestions/{id}/reject  — operator+
  * POST /observer-pilot/override                 — operator+

Status codes: 401 unauthenticated, 403 insufficient role, 404 unknown/
decided/expired suggestion, 409 when the kill-switch blocks a write
(the request is valid, the pilot is OFF — flip the switch first),
422 invalid values, 503 when the pilot service is not running in
this process (status/decisions then honestly report not-running
instead of fabricating state).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.deps import TokenPayload, require_operator
from app.services.observer_pilot.pov_journal import get_pov_journal
from app.services.observer_pilot.service import get_pilot_service

from .schemas import KillSwitchRequest, OverrideRequest

router = APIRouter(prefix="/observer-pilot", tags=["Observer Pilot"])


def _service(request: Request):
    """The lifespan pilot service: app.state first, then the module
    singleton (unit tests wire the singleton directly)."""
    service = getattr(request.app.state, "observer_pilot", None)
    if service is None:
        service = get_pilot_service()
    return service


def _not_running() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "Observer pilot service is not running in this process — "
            "no tap will be attempted and nothing is fabricated."
        ),
    )


def _kill_switch_conflict(action: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"Pilot kill-switch is OFF: {action} is blocked in every "
            "mode. Re-enable it via POST /api/v1/observer-pilot/"
            "kill-switch {\"enabled\": true} first."
        ),
    )


@router.get("/status")
async def get_pilot_status(
    request: Request,
    _user: TokenPayload = Depends(require_operator),
):
    """Pilot status: per-feed POV + behaviour + pending suggestion +
    last decision/note, kill-switch state, calibration flags.

    When the service is not running, ``running`` is false and feeds
    are honestly empty — nothing is fabricated."""
    service = _service(request)
    if service is None:
        return {
            "running": False,
            "mode": None,
            "clone_grabbed": False,
            "behaviour": None,
            "kill_switch": {"enabled": None, "disabled_slots": [],
                            "control_wired": False},
            "executor_available": False,
            "calibration": {"available": False, "devices": {}},
            "suggestion_ttl_s": None,
            "feeds": [],
            "pending_suggestions": [],
            "stats": {},
            "journal": None,
            "roster": None,
        }
    return service.status()


@router.post("/kill-switch")
async def set_kill_switch(
    payload: KillSwitchRequest,
    request: Request,
    user: TokenPayload = Depends(require_operator),
):
    """Flip the pilot kill-switch. OFF blocks every tap in every
    mode (auto-execute, approved suggestions, manual overrides)."""
    service = _service(request)
    if service is None:
        raise _not_running()
    enabled = service.set_kill_switch(payload.enabled)
    return {
        "enabled": enabled,
        "control_wired": service.control is not None,
        "updated_by": user.username,
    }


@router.post("/suggestions/{suggestion_id}/approve")
async def approve_suggestion(
    suggestion_id: str,
    request: Request,
    user: TokenPayload = Depends(require_operator),
):
    """Approve a pending pilot suggestion: its decision executes now.
    404 unknown/decided/expired; 409 when the kill-switch is OFF."""
    service = _service(request)
    if service is None:
        raise _not_running()
    try:
        result = await service.approve_suggestion(
            suggestion_id, by=user.username
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    if result.get("status") == "blocked_kill_switch":
        raise _kill_switch_conflict("suggestion approval")
    return result


@router.post("/suggestions/{suggestion_id}/reject")
async def reject_suggestion(
    suggestion_id: str,
    request: Request,
    user: TokenPayload = Depends(require_operator),
):
    """Reject a pending pilot suggestion: fate journaled, never
    executed. 404 unknown/decided/expired."""
    service = _service(request)
    if service is None:
        raise _not_running()
    try:
        return await service.reject_suggestion(
            suggestion_id, by=user.username
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )


@router.post("/override")
async def manual_override(
    payload: OverrideRequest,
    request: Request,
    user: TokenPayload = Depends(require_operator),
):
    """Manual POV override: tap one feed straight to a roster index.
    Journaled as ``manual_override``. 409 when the kill-switch is OFF
    (a blocked override is still journaled by the service)."""
    service = _service(request)
    if service is None:
        raise _not_running()
    result = await service.override(
        payload.slot_number, payload.roster_index, by=user.username
    )
    if result.get("status") == "blocked_kill_switch":
        raise _kill_switch_conflict("manual override")
    return result


@router.get("/decisions")
async def list_decisions(
    request: Request,
    _user: TokenPayload = Depends(require_operator),
    limit: int = Query(default=50, ge=1, le=500),
    slot: Optional[int] = Query(default=None, ge=1, le=16),
):
    """Recent POV journal entries (durable), newest first, optional
    slot filter. Empty when nothing has been journaled — or when no
    journal runs in this process (``journal_running`` false)."""
    journal = get_pov_journal()
    service = _service(request)
    if journal is None and service is not None:
        journal = service.journal
    if journal is None:
        return {"journal_running": False, "decisions": []}
    decisions = await journal.recent(limit=limit, slot_number=slot)
    return {"journal_running": True, "decisions": decisions}
