# backend/app/services/feed_adapter/fight_schema.py
"""
Canonical FightEvent JSON schema (Phase 22 / W3, M4).

THE one published shape for a fight event. Before Phase 22 the shape
was split three ways — the DB model (``db/models/fight_event.py``),
ad-hoc bus payloads, and serializer dicts — and they did not fully
agree. This module is now the contract:

* :class:`FightEventCanonical` mirrors the DB model field-for-field
  (same names, same optionality). If the DB model gains a field, it is
  added here in the same change.
* :func:`fight_event_to_canonical` converts a DB row (or any object
  with the model's attributes) into the canonical shape.
* :func:`canonical_to_bus_payload` is the ONLY bus serialisation of a
  fight: ``fight.detected`` / ``fight_event`` payloads are built here.

Timestamps are epoch **milliseconds** (``start_timestamp_ms``), matching
the DB model and kill-event convention — not seconds.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class FightEventCanonical(BaseModel):
    """One fight, exactly as stored and exactly as published."""

    id: Optional[str] = Field(default=None, description="fight_events.id (DB uuid).")
    match_id: str
    start_timestamp_ms: int
    end_timestamp_ms: Optional[int] = None

    team_ids: List[str] = Field(default_factory=list)
    player_ids: List[str] = Field(default_factory=list)

    location_x: Optional[float] = None
    location_y: Optional[float] = None
    map_name: Optional[str] = None

    intensity_score: float = 0.0
    winner_team_id: Optional[str] = None
    total_kills: int = 0
    total_knocks: int = 0
    utility_used: Dict[str, Any] = Field(default_factory=dict)

    #: Schema version of THIS contract (bump on breaking change).
    schema_version: str = "1.0"


def fight_event_to_canonical(row: Any) -> FightEventCanonical:
    """Convert a ``FightEvent`` DB row (or attribute twin) to canonical."""
    return FightEventCanonical(
        id=getattr(row, "id", None),
        match_id=row.match_id,
        start_timestamp_ms=int(row.start_timestamp_ms),
        end_timestamp_ms=(
            int(row.end_timestamp_ms) if getattr(row, "end_timestamp_ms", None) is not None else None
        ),
        team_ids=list(getattr(row, "team_ids", None) or []),
        player_ids=list(getattr(row, "player_ids", None) or []),
        location_x=getattr(row, "location_x", None),
        location_y=getattr(row, "location_y", None),
        map_name=getattr(row, "map_name", None),
        intensity_score=float(getattr(row, "intensity_score", 0.0) or 0.0),
        winner_team_id=getattr(row, "winner_team_id", None),
        total_kills=int(getattr(row, "total_kills", 0) or 0),
        total_knocks=int(getattr(row, "total_knocks", 0) or 0),
        utility_used=dict(getattr(row, "utility_used", None) or {}),
    )


def canonical_to_bus_payload(event: FightEventCanonical) -> Dict[str, Any]:
    """The bus payload for a fight event (single serialiser)."""
    return event.model_dump()
