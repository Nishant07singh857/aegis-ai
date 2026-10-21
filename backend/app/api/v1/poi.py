"""POI read API — mounted at /api/v1 by app/main.py (Phase 24, W3).

Auth model: any authenticated user (same read gate as zone state /
vision-intelligence status). The data comes from W4's POI database
(``app/services/poi/poi_database.py::get_poi_database`` — contract:
``all_pois(map_id)``, ``nearest(map_id, x, y, max_radius=0.05)``,
``maps()``). When the database module is unavailable the endpoints
return an honest empty / null — never invented POIs.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import TokenPayload, get_current_user

router = APIRouter(prefix="/poi", tags=["POI"])


def _db():
    try:
        from app.services.poi.poi_database import get_poi_database

        return get_poi_database()
    except Exception:  # pragma: no cover - POI db not landed yet
        return None


@router.get("")
async def list_pois(
    map: Optional[str] = Query(default=None, description="Map id, e.g. 'erangel'."),
    _user: TokenPayload = Depends(get_current_user),
) -> Dict[str, Any]:
    """All POIs for a map (or every known map when omitted)."""
    db = _db()
    if db is None:
        return {"pois": [], "maps": [], "note": "POI database unavailable."}
    maps = []
    try:
        maps = list(db.maps())
    except Exception:  # pragma: no cover - defensive
        maps = []
    if map:
        try:
            pois = db.all_pois(map)
        except Exception:  # pragma: no cover - defensive
            pois = []
        return {"map": map, "pois": pois, "maps": maps}
    pois = []
    for m in maps:
        try:
            pois.extend(db.all_pois(m))
        except Exception:  # pragma: no cover - defensive
            continue
    return {"pois": pois, "maps": maps}


@router.get("/nearest")
async def nearest_poi(
    map: str = Query(..., description="Map id, e.g. 'erangel'."),
    x: float = Query(..., ge=0, le=1, description="Normalized map x (0..1)."),
    y: float = Query(..., ge=0, le=1, description="Normalized map y (0..1)."),
    _user: TokenPayload = Depends(get_current_user),
) -> Dict[str, Any]:
    """Nearest POI to a normalized point, or null when none is close."""
    db = _db()
    if db is None:
        return {"poi": None, "note": "POI database unavailable."}
    try:
        poi = db.nearest(map, x, y)
    except TypeError:
        poi = db.nearest(map, x, y, max_radius=0.05)
    return {"poi": poi}
