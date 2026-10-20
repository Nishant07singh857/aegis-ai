"""Zone-efficiency analytics API (Phase 22 / W3, M7).

Mounted at /api/v1 by app/main.py:

  POST /analytics/zone-efficiency — compute + publish zone efficiency
  for the teams whose analyzer observations are submitted.

The observations are the rotation/positioning analyzers' own fields
(``in_zone``, ``distance_to_edge``, ``danger_level``) — the metric's
defined formula lives in ``app.analytics.zone_efficiency``. Teams with
no observations come back with ``score: null`` and a reason; nothing
is estimated server-side.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.analytics.zone_efficiency import zone_efficiency_report
from app.api.deps import TokenPayload, get_current_user

router = APIRouter(prefix="/analytics", tags=["Analytics"])


class ZoneObservation(BaseModel):
    in_zone: bool
    distance_to_edge: Optional[float] = None
    distance_to_zone_edge: Optional[float] = None
    danger_level: Optional[float] = None


class ZoneEfficiencyRequest(BaseModel):
    teams: Dict[str, List[ZoneObservation]] = Field(default_factory=dict)


@router.post("/zone-efficiency")
async def publish_zone_efficiency(
    payload: ZoneEfficiencyRequest,
    _user: TokenPayload = Depends(get_current_user),
) -> Dict[str, Any]:
    """Compute zone efficiency from submitted analyzer observations."""
    observations = {
        team_id: [o.model_dump() for o in obs]
        for team_id, obs in payload.teams.items()
    }
    return {"data": zone_efficiency_report(observations)}
