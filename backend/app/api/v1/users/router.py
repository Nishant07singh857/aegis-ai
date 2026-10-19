"""Users API router — mounted at /api/v1 by app/main.py (Phase 16).

Every endpoint here requires admin auth. Plaintext passwords appear ONLY in
the create response (when auto-generated) and the bulk-observers credential
sheet — they are never stored and never returned again.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TokenPayload, require_admin
from app.db.session import get_db
from .schemas import (
    BulkObserversRequest,
    BulkObserversResponse,
    DeactivateTournamentResponse,
    UserCreate,
    UserCreateResponse,
    UserListResponse,
    UserPatch,
    UserResponse,
)
from .service import UserService

router = APIRouter(prefix="/users", tags=["Users"])


def get_user_service(db: AsyncSession = Depends(get_db)) -> UserService:
    return UserService(db)


@router.post(
    "",
    response_model=UserCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_user(
    payload: UserCreate,
    admin: TokenPayload = Depends(require_admin),
    service: UserService = Depends(get_user_service),
):
    """Create one account. Auto-generates a secure password (returned ONCE)
    when omitted for any non-admin role."""
    response, _plaintext = await service.create_user(
        username=payload.username,
        password=payload.password,
        role=payload.role,
        tournament_id=payload.tournament_id,
        team_id=payload.team_id,
        email=payload.email,
        full_name=payload.full_name,
        created_by=admin.username,
    )
    return response


@router.get("", response_model=UserListResponse, dependencies=[Depends(require_admin)])
async def list_users(
    role: Optional[str] = Query(None, description="Filter by role"),
    tournament_id: Optional[str] = Query(None, description="Filter by tournament"),
    is_active: Optional[bool] = Query(None, description="Filter by active flag"),
    service: UserService = Depends(get_user_service),
):
    users, total = await service.list_user_responses(
        role=role, tournament_id=tournament_id, is_active=is_active
    )
    return {"data": users, "total": total}


@router.post(
    "/bulk-observers",
    response_model=BulkObserversResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def bulk_create_observers(
    payload: BulkObserversRequest,
    admin: TokenPayload = Depends(require_admin),
    service: UserService = Depends(get_user_service),
):
    """Generate N observer accounts (obs01..obsN) for a tournament, assign
    active teams round-robin, and wire the matching observer slots.

    Returns the credential sheet — the ONLY time these passwords are visible.
    """
    return await service.bulk_create_observers(
        tournament_id=payload.tournament_id,
        count=payload.count,
        created_by=admin.username,
    )


@router.post(
    "/tournament/{tournament_id}/deactivate",
    response_model=DeactivateTournamentResponse,
    dependencies=[Depends(require_admin)],
)
async def deactivate_tournament_users(
    tournament_id: str,
    service: UserService = Depends(get_user_service),
):
    """Kill switch: deactivate every user bound to a tournament."""
    count = await service.deactivate_tournament(tournament_id)
    return {"tournament_id": tournament_id, "deactivated": count}


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    dependencies=[Depends(require_admin)],
)
async def patch_user(
    user_id: str,
    payload: UserPatch,
    service: UserService = Depends(get_user_service),
):
    """Reset a password and/or (de)activate an account."""
    user = await service.patch_user(user_id, password=payload.password, is_active=payload.is_active)
    return await service.user_response(user)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def delete_user(
    user_id: str,
    service: UserService = Depends(get_user_service),
):
    """Hard-delete an account. Refused (409) when the account has ever
    logged in — deactivate those instead."""
    await service.delete_user(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
