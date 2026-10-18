"""Director API router — mounted at /api/v1 by app/main.py.

The director is the single source of truth for what's on the broadcast
output page. Auth model:

  * GET  /director/state               — any authenticated user (viewer incl.)
  * GET  /director/suggestions         — operator+ (pending AI proposals)
  * POST /director/mode                — operator+ (explicit mode flips;
                                         "full_auto" | "semi_auto" | "manual",
                                         legacy "auto" -> "full_auto")
  * POST /director/scene               — operator+, MANUAL mode only
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

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import TokenPayload, get_current_user, require_operator
from app.services.director_service import (
    DirectorModeError,
    SuggestionNotFoundError,
    director_service,
)

from .schemas import (
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
    _user: TokenPayload = Depends(get_current_user),
):
    """Current director state. Any authenticated user, viewers included."""
    return director_service.snapshot()


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

    Accepts "full_auto" | "semi_auto" | "manual"; the legacy value "auto"
    is normalised to "full_auto".
    """
    return await director_service.set_mode(
        payload.mode, updated_by=user.username
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
