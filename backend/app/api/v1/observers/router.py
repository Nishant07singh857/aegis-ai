"""Observer-team mapping API — mounted at /api/v1 by app/main.py.

A tournament runs 16 observer slots; each slot is assigned to at most
one team. Reads are authenticated (any role); every write requires
operator or above (``require_operator``, same gate as the feed ingest).

Endpoints:
  GET    /observers                  list the 16 slots (seeds rows on first read)
  PUT    /observers/{slot}/assign    assign / re-assign a team (operator+)
  DELETE /observers/{slot}/assign    clear a slot's team (operator+)
  POST   /observers/{slot}/heartbeat record an observer client's status (operator+)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TokenPayload, get_current_user, require_operator
from app.db.models.observer_slot import ObserverSlot
from app.db.session import get_db
from app.services.observer_mapping import ObserverMappingService
from .schemas import (
    AssignSlotRequest,
    HeartbeatRequest,
    ObserverSlotListResponse,
    ObserverSlotResponse,
)

router = APIRouter(prefix="/observers", tags=["Observers"])


def get_observer_service(
    db: AsyncSession = Depends(get_db),
) -> ObserverMappingService:
    return ObserverMappingService(db)


async def _respond(
    service: ObserverMappingService, slot: ObserverSlot
) -> ObserverSlotResponse:
    """Map an ORM row to the API response, resolving the team name."""
    return ObserverSlotResponse(
        slot_number=slot.slot_number,
        team_id=slot.team_id,
        team_name=await service.team_name(slot.team_id),
        status=slot.status,  # type: ignore[arg-type]
        last_heartbeat=slot.last_heartbeat,
    )


@router.get("", response_model=ObserverSlotListResponse)
async def list_observers(
    _user: TokenPayload = Depends(get_current_user),
    service: ObserverMappingService = Depends(get_observer_service),
):
    slots = await service.list_slots()
    return {"data": [await _respond(service, slot) for slot in slots]}


@router.put(
    "/{slot_number}/assign",
    response_model=ObserverSlotResponse,
    dependencies=[Depends(require_operator)],
)
async def assign_slot(
    slot_number: int,
    payload: AssignSlotRequest,
    service: ObserverMappingService = Depends(get_observer_service),
):
    slot = await service.assign_slot(slot_number, payload.team_id)
    return await _respond(service, slot)


@router.delete(
    "/{slot_number}/assign",
    response_model=ObserverSlotResponse,
    dependencies=[Depends(require_operator)],
)
async def unassign_slot(
    slot_number: int,
    service: ObserverMappingService = Depends(get_observer_service),
):
    slot = await service.unassign_slot(slot_number)
    return await _respond(service, slot)


@router.post(
    "/{slot_number}/heartbeat",
    response_model=ObserverSlotResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_operator)],
)
async def slot_heartbeat(
    slot_number: int,
    payload: HeartbeatRequest,
    service: ObserverMappingService = Depends(get_observer_service),
):
    slot = await service.heartbeat(slot_number, payload.status.value)
    return await _respond(service, slot)
