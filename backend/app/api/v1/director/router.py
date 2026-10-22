"""Director API router — mounted at /api/v1 by app/main.py.

The director is the single source of truth for what's on the broadcast
output page. Auth model:

  * GET  /director/state               — any authenticated user (viewer incl.)
  * GET  /director/suggestions         — operator+ (pending AI proposals)
  * POST /director/mode                — operator+ (explicit mode flips;
                                         "full_auto" | "semi_auto" | "manual"
                                         | "clone" (Phase 27 Director-Style),
                                         legacy "auto" -> "full_auto")
  * POST /director/clone/grab          — operator+ (Phase 27: grab manual
                                         control inside CLONE mode; 409
                                         outside clone mode)
  * POST /director/clone/release       — operator+ (Phase 27: release a
                                         clone grab; when nothing is
                                         grabbed this is an honest no-op
                                         returning the unchanged state)
  * POST /director/scene               — operator+, MANUAL mode only
  * POST /director/camera              — operator+, MANUAL mode only (Phase 17 W2:
                                         active observer camera 1..16 for the
                                         broadcast video layer)
  * POST /director/overlays            — operator+, MANUAL mode only
  * POST /director/lower-third         — operator+, MANUAL mode only
  * POST /director/graphics           — operator+; graphics template
                                         selection; MANUAL or SEMI-AUTO
                                         mode only (409 in FULL_AUTO)
  * POST /director/suggestions/{id}/approve — operator+; applies the
                                         payload exactly like a manual
                                         operator write; 404 if unknown/
                                         decided/expired; 409 in FULL_AUTO
  * POST /director/suggestions/{id}/reject  — operator+; marks rejected,
                                         logged, dropped; 404 if unknown/
                                         decided/expired

Status codes: 401 unauthenticated, 403 insufficient role, 422 invalid
values, 404 unknown/decided/expired suggestion, 409 operator write or
approval while the director mode conflicts (valid request, wrong mode —
flip modes first). Graphics template selection is allowed in MANUAL and
SEMI-AUTO; in FULL_AUTO it is rejected with 409 like any other operator
write.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import TokenPayload, get_current_user, require_operator
from app.services.director_service import (
    DirectorModeError,
    SuggestionNotFoundError,
    director_service,
)

from .schemas import (
    CameraRequest,
    DirectorState,
    DirectorSuggestion,
    GraphicsRequest,
    LowerThirdRequest,
    ModeRequest,
    OverlaysRequest,
    SceneRequest,
)

router = APIRouter(prefix="/director", tags=["Director"])


def _mode_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "Director is not in MANUAL mode: operator scene/overlay/"
            "lower-third writes are rejected. In SEMI-AUTO, approve AI "
            "suggestions instead; switch to MANUAL for direct control via "
            'POST /api/v1/director/mode {"mode": "manual"}.'
        ),
    )


def _graphics_mode_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "Director is in FULL_AUTO mode: graphics template selection is "
            "an operator write and is rejected here. Switch to SEMI-AUTO "
            'or MANUAL first via POST /api/v1/director/mode '
            '{"mode": "semi_auto"}.'
        ),
    )


def _suggestion_not_found(exc: SuggestionNotFoundError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=str(exc),
    )


@router.get("/state", response_model=DirectorState)
async def get_director_state(
    request: Request = None,
    _user: TokenPayload = Depends(get_current_user),
):
    """Current director state. Any authenticated user, viewers included."""
    # Phase 18 (W3): enforce the map-cam 30s maximum hold on every read so
    # a poll-driven client (OBS capture page) can never sit on an expired
    # map cam even if it missed the WS broadcast.
    await director_service.enforce_scene_holds()
    state = director_service.snapshot()
    # Phase 29 (W4): per-feed POV summary, _multi_feed_monitor_summary
    # pattern — from the live director's POV provider when wired;
    # empty list when it is not (never fabricated).
    state["pov_feeds"] = _pov_feeds_summary(request)
    return state


def _pov_feeds_summary(request: Request):
    """Phase 29: per-feed POV summary from the live director (or [])."""
    if request is None:
        # TestClient-less unit calls: fall back to the process singleton.
        try:
            from app.services.live_director import get_live_director

            service = get_live_director()
            return service.pov_summary() if service is not None else []
        except Exception:  # pragma: no cover - defensive
            return []
    service = getattr(request.app.state, "live_director", None)
    if service is None:
        try:
            from app.services.live_director import get_live_director

            service = get_live_director()
        except Exception:  # pragma: no cover - defensive
            service = None
    if service is None:
        return []
    try:
        return service.pov_summary()
    except Exception:  # pragma: no cover - defensive
        return []


def _multi_feed_monitor_summary(request: Request):
    """Phase 26: summary of the lifespan multi-feed monitor (or None).

    The director surface shows the fleet-level picture (running, feed
    count, sampling budget, health rollup); full per-feed health lives
    on GET /api/v1/feed/vision/status. None when the monitor is not
    running in this process — reported as null, never fabricated.
    """
    if request is None:
        return None
    monitor = getattr(request.app.state, "multi_feed_monitor", None)
    if monitor is None:
        return None
    full = monitor.status()
    return {
        "running": full["running"],
        "feeds_total": full["feeds_total"],
        "feeds_enabled": full["feeds_enabled"],
        "budget": full["budget"],
        "events_emitted": full["events_emitted"],
        "duplicates_suppressed": full["cross_feed"]["duplicates_suppressed"],
        "health_summary": full["health_summary"],
    }


@router.get("/decision-log")
async def get_director_decision_log(
    request: Request = None,
    _user: TokenPayload = Depends(require_operator),
    limit: int = 50,
):
    """Live director decision log (Phase 25, W1). Operator or admin.

    Read-only ring of every live-director evaluation outcome (switch /
    hold / rejected-challenger / suggestion) with per-candidate score
    breakdowns, the winning margin and the rule that decided it. When
    the live director is not running in this process, ``running`` is
    false and ``decisions`` is honestly empty — nothing is fabricated.
    """
    from app.services.live_director import get_live_director

    monitor_summary = _multi_feed_monitor_summary(request)
    service = get_live_director()
    if service is None:
        return {
            "running": False,
            "decisions": [],
            "status": None,
            "multi_feed_monitor": monitor_summary,
        }
    return {
        "running": service.running,
        "decisions": service.decision_log(limit=limit),
        "status": service.status(),
        "multi_feed_monitor": monitor_summary,
    }


@router.get("/suggestions", response_model=list[DirectorSuggestion])
async def list_director_suggestions(
    _user: TokenPayload = Depends(require_operator),
):
    """Pending AI suggestions (SEMI_AUTO mode). Operator or admin."""
    return director_service.list_suggestions()


@router.post("/suggestions/{suggestion_id}/approve",
             response_model=DirectorSuggestion)
async def approve_director_suggestion(
    suggestion_id: str,
    user: TokenPayload = Depends(require_operator),
):
    """Approve a suggestion: its payload is applied exactly as if the
    operator had issued it manually, then it is marked applied and removed
    from the queue. 404 when the suggestion is unknown, already decided or
    expired; 409 in FULL_AUTO mode."""
    try:
        return await director_service.approve_suggestion(
            suggestion_id, updated_by=user.username
        )
    except SuggestionNotFoundError as exc:
        raise _suggestion_not_found(exc)
    except DirectorModeError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Director is in FULL_AUTO mode: the AI is driving, so "
                "suggestion approvals are rejected. Switch to SEMI-AUTO or "
                "MANUAL first via POST /api/v1/director/mode."
            ),
        )


@router.post("/suggestions/{suggestion_id}/reject",
             response_model=DirectorSuggestion)
async def reject_director_suggestion(
    suggestion_id: str,
    user: TokenPayload = Depends(require_operator),
):
    """Reject a suggestion: marked rejected, logged, dropped from the
    queue. 404 when the suggestion is unknown, already decided or expired."""
    try:
        return await director_service.reject_suggestion(
            suggestion_id, updated_by=user.username
        )
    except SuggestionNotFoundError as exc:
        raise _suggestion_not_found(exc)


@router.post("/mode", response_model=DirectorState)
async def set_director_mode(
    payload: ModeRequest,
    user: TokenPayload = Depends(require_operator),
):
    """Explicit mode transition. Operator or admin.

    Accepts "full_auto" | "semi_auto" | "manual" | "clone"; the legacy
    value "auto" is normalised to "full_auto".
    """
    return await director_service.set_mode(
        payload.mode, updated_by=user.username
    )


@router.post("/clone/grab", response_model=DirectorState)
async def grab_clone_control(
    user: TokenPayload = Depends(require_operator),
):
    """Grab manual control inside CLONE mode (Phase 27). Operator+.

    While grabbed, the clone director behaves exactly like MANUAL: the
    AI never writes, and the human drives via the scene/camera writes.
    409 outside CLONE mode — the service's message is surfaced as-is.
    Idempotent when already grabbed. The actor is the current user;
    the request carries no body.
    """
    try:
        return await director_service.grab_clone_control(
            updated_by=user.username
        )
    except DirectorModeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )


@router.post("/clone/release", response_model=DirectorState)
async def release_clone_control(
    user: TokenPayload = Depends(require_operator),
):
    """Release a clone grab (Phase 27). Operator+.

    After release the clone director resumes firing with the learned
    profile. When nothing is grabbed the service is a no-op, so this
    returns the CURRENT state unchanged (``clone_grabbed`` stays
    false) — it never fabricates a release that did not happen. The
    actor is the current user; the request carries no body.
    """
    return await director_service.release_clone_control(
        updated_by=user.username, reason="explicit"
    )


@router.post("/scene", response_model=DirectorState)
async def set_director_scene(
    payload: SceneRequest,
    user: TokenPayload = Depends(require_operator),
):
    """Operator scene select. Rejected with 409 unless in MANUAL mode."""
    try:
        return await director_service.set_scene(
            payload.scene.value, updated_by=user.username
        )
    except DirectorModeError:
        raise _mode_conflict()


@router.post("/camera", response_model=DirectorState)
async def set_director_camera(
    payload: CameraRequest,
    user: TokenPayload = Depends(require_operator),
):
    """Operator camera select (Phase 17 W2). Rejected with 409 unless in
    MANUAL mode.

    Switches the active observer camera shown on the broadcast output
    video layer (GET /api/v1/video/feed/{slot}). Also keeps the W4
    capture manager's on-air selection in sync so
    GET /api/v1/video/status agrees with the director state.
    """
    try:
        state = await director_service.set_camera(
            payload.slot, updated_by=user.username
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    except DirectorModeError:
        raise _mode_conflict()
    # Keep the video layer's on-air selection consistent (lazy import:
    # the video router pulls DB context; both are loaded by main.py).
    from app.api.v1.video.router import multi_device_manager

    await multi_device_manager.set_on_air(payload.slot, set_by=user.username)
    return state


@router.post("/overlays", response_model=DirectorState)
async def set_director_overlays(
    payload: OverlaysRequest,
    user: TokenPayload = Depends(require_operator),
):
    """Operator overlay toggles (partial update). 409 unless MANUAL mode."""
    try:
        return await director_service.set_overlays(
            scorebug=payload.scorebug,
            killfeed=payload.killfeed,
            updated_by=user.username,
        )
    except DirectorModeError:
        raise _mode_conflict()


@router.post("/lower-third", response_model=DirectorState)
async def set_director_lower_third(
    payload: LowerThirdRequest,
    user: TokenPayload = Depends(require_operator),
):
    """Operator lower-third control. 409 unless MANUAL mode."""
    try:
        return await director_service.set_lower_third(
            visible=payload.visible,
            title=payload.title,
            subtitle=payload.subtitle,
            updated_by=user.username,
        )
    except DirectorModeError:
        raise _mode_conflict()


@router.post("/graphics", response_model=DirectorState)
async def set_director_graphics(
    payload: GraphicsRequest,
    user: TokenPayload = Depends(require_operator),
):
    """Operator graphics template selection (partial update). Allowed in
    MANUAL and SEMI_AUTO mode — template choice is a direct operator
    write in both. 409 in FULL_AUTO mode, where the AI drives the output."""
    try:
        return await director_service.set_graphics(
            scorebug_template_id=payload.scorebug_template_id,
            lowerthird_template_id=payload.lowerthird_template_id,
            standings_template_id=payload.standings_template_id,
            killfeed_template_id=payload.killfeed_template_id,
            updated_by=user.username,
        )
    except DirectorModeError:
        raise _graphics_mode_conflict()
