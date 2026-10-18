"""AEGIS AI — analysis services (Phase 11).

Rotation / drop / zone tracking over the existing ``rotation_events``
table plus the new ``drop_events`` / ``zone_phases`` tables.

All stat computations are module-level pure functions over plain data
structures — they are unit-tested in ``test_analysis_smoke.py`` with
in-memory fixtures, no database required. The service classes only do
async SQLAlchemy reads/writes and then delegate the math to those
functions, so the numbers the API returns are exactly the numbers the
tests pin down.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import HTTPException, status
from sqlalchemy import distinct, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.rotation_event import RotationEvent

from .models import DropEvent, ZonePhase
from .schemas import (
    DropIngestRequest,
    DropResponse,
    DropTeamStats,
    RotationIngestRequest,
    RotationResponse,
    ZoneIngestRequest,
    ZonePhaseResponse,
    ZoneTendency,
)

logger = logging.getLogger("aegis.analysis")


# ══════════════════════════════════════════════════════════════════════
# Pure stat computations (unit-testable, no DB)
# ══════════════════════════════════════════════════════════════════════


def _safe_xy(point: Any) -> Optional[Tuple[float, float]]:
    """Coerce a waypoint candidate to (x, y) floats; None when malformed."""
    try:
        x, y = point
        return (float(x), float(y))
    except (TypeError, ValueError):
        return None


def polyline_distance_m(waypoints: Sequence[Any]) -> float:
    """Total length in metres of a [[x, y], ...] polyline.

    Malformed points are skipped; fewer than two valid points → 0.0.
    """
    pts = [_safe_xy(p) for p in (waypoints or [])]
    pts = [p for p in pts if p is not None]
    if len(pts) < 2:
        return 0.0
    total = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        total += math.hypot(x2 - x1, y2 - y1)
    return total


def survival_summary(survived_flags: Sequence[Optional[bool]]) -> Dict[str, Any]:
    """Drop survival rate over *known* outcomes only.

    Unknown outcomes (None) shrink the denominator instead of being
    guessed — when nothing is known, ``rate`` is None, not 0.
    """
    flags = list(survived_flags)
    known = [f for f in flags if f is not None]
    survived = sum(1 for f in known if f)
    return {
        "total_drops": len(flags),
        "known_outcomes": len(known),
        "survived": survived,
        "survival_rate": (survived / len(known)) if known else None,
    }


def top_drop_locations(drops: Sequence[Dict[str, Any]], n: int = 5) -> List[Dict[str, Any]]:
    """Top-N drop spots by (named_location, map_name): drops + contested count.

    Drops without a named location are grouped under "unknown".
    """
    buckets: Dict[Tuple[str, Optional[str]], Dict[str, int]] = {}
    order: List[Tuple[str, Optional[str]]] = []
    for d in drops:
        key = (d.get("named_location") or "unknown", d.get("map_name"))
        if key not in buckets:
            buckets[key] = {"drops": 0, "contested": 0}
            order.append(key)
        buckets[key]["drops"] += 1
        if d.get("contested_by"):
            buckets[key]["contested"] += 1
    ranked = sorted(order, key=lambda k: buckets[k]["drops"], reverse=True)
    return [
        {
            "named_location": k[0],
            "map_name": k[1],
            "drops": buckets[k]["drops"],
            "contested": buckets[k]["contested"],
        }
        for k in ranked[:n]
    ]


def latest_position_at_or_before(
    positions: Sequence[Dict[str, Any]], ts_ms: int
) -> Optional[Dict[str, Any]]:
    """Latest team position with timestamp <= ts_ms (positions sorted by ts)."""
    best: Optional[Dict[str, Any]] = None
    for p in positions:
        if p["ts"] <= ts_ms:
            best = p
        else:
            break
    return best


def normalized_edge_ratios(
    positions: Sequence[Dict[str, Any]],
    phases: Sequence[Dict[str, Any]],
) -> List[float]:
    """Per-phase distance-from-centre / radius for the team's last known
    position at or before each phase's timestamp.

    Phases with no known position are skipped (not zero-filled).
    """
    ratios: List[float] = []
    ordered = sorted(positions, key=lambda p: p["ts"])
    for ph in sorted(phases, key=lambda p: p["phase_number"]):
        pos = latest_position_at_or_before(ordered, ph["timestamp_ms"])
        radius = ph.get("radius")
        if pos is None or not radius or radius <= 0:
            continue
        dist = math.hypot(pos["x"] - ph["center_x"], pos["y"] - ph["center_y"])
        ratios.append(dist / radius)
    return ratios


def classify_tendency(avg_ratio: Optional[float]) -> str:
    """Classify edge-vs-centre tendency from a mean normalised distance."""
    if avg_ratio is None:
        return "unknown"
    if avg_ratio < 0.5:
        return "center"
    if avg_ratio < 0.9:
        return "mid-zone"
    if avg_ratio <= 1.15:
        return "edge"
    return "outside"


def zone_pull_notes(
    phases: Sequence[Dict[str, Any]],
    positions: Sequence[Dict[str, Any]],
) -> List[str]:
    """Honest, data-derived notes on how the team adapted to zone pulls.

    Notes only describe phases where the team has a known position; phases
    without position data get an explicit 'no position data' note rather
    than an invented one.
    """
    notes: List[str] = []
    ordered = sorted(phases, key=lambda p: p["phase_number"])
    pos_ordered = sorted(positions, key=lambda p: p["ts"])
    prev: Optional[Dict[str, Any]] = None
    prev_pos: Optional[Dict[str, Any]] = None
    for ph in ordered:
        pos = latest_position_at_or_before(pos_ordered, ph["timestamp_ms"])
        if prev is not None:
            shift = math.hypot(
                ph["center_x"] - prev["center_x"],
                ph["center_y"] - prev["center_y"],
            )
            if pos is not None and prev_pos is not None and pos is not prev_pos:
                moved = math.hypot(pos["x"] - prev_pos["x"], pos["y"] - prev_pos["y"])
                ratio = pos and (moved / shift if shift > 0 else 0.0)
                notes.append(
                    f"Phase {prev['phase_number']}→{ph['phase_number']}: zone centre "
                    f"shifted {shift:.0f}m; team moved {moved:.0f}m in the same "
                    f"window ({'kept pace' if ratio and ratio >= 0.8 else 'lagged the pull'})."
                )
            else:
                notes.append(
                    f"Phase {prev['phase_number']}→{ph['phase_number']}: zone centre "
                    f"shifted {shift:.0f}m; no team position data for this window."
                )
        prev, prev_pos = ph, pos
    return notes


def average(values: Sequence[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return (sum(vals) / len(vals)) if vals else None


# ══════════════════════════════════════════════════════════════════════
# Services (async SQLAlchemy; math delegated to the pure functions)
# ══════════════════════════════════════════════════════════════════════


def _db_unavailable(exc: Exception) -> HTTPException:
    logger.warning(f"Database unavailable for analysis write: {exc}")
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Database unavailable — analysis writes require a connected database.",
    )


def _bad_reference(exc: Exception) -> HTTPException:
    logger.warning(f"Analysis ingest rejected (bad reference): {exc}")
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="Unknown match_id (or conflicting row) — ingest requires an existing match.",
    )


class RotationService:
    """Record and query rotation events into ``rotation_events.meta``."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── writes ──

    async def record_rotation(self, payload: RotationIngestRequest) -> RotationResponse:
        computed = polyline_distance_m(payload.waypoints)
        distance = payload.distance_m if payload.distance_m is not None else computed
        row = RotationEvent(
            match_id=payload.match_id,
            team_id=payload.team_id,
            timestamp_ms=payload.timestamp_ms,
            meta={
                "from_zone": payload.from_zone,
                "to_zone": payload.to_zone,
                "game_time_ms": payload.game_time_ms,
                "waypoints": payload.waypoints,
                "vehicle_used": payload.vehicle_used,
                "fights_encountered": payload.fights_encountered,
                "rotation_order": payload.rotation_order,
                "distance_m": distance,
                "computed_distance_m": computed,
                "source": payload.source,
            },
        )
        self.db.add(row)
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise _bad_reference(exc)
        except SQLAlchemyError as exc:
            await self.db.rollback()
            raise _db_unavailable(exc)
        await self.db.refresh(row)
        return self._to_response(row)

    # ── reads ──

    async def list_rotations(
        self, team_id: Optional[str] = None, match_id: Optional[str] = None
    ) -> List[RotationResponse]:
        stmt = select(RotationEvent).order_by(RotationEvent.timestamp_ms.asc())
        if team_id:
            stmt = stmt.where(RotationEvent.team_id == team_id)
        if match_id:
            stmt = stmt.where(RotationEvent.match_id == match_id)
        rows = (await self.db.execute(stmt)).scalars().all()
        return [self._to_response(r) for r in rows]

    async def team_positions(
        self, team_id: str, match_id: str
    ) -> List[Dict[str, Any]]:
        """Last-waypoint positions per rotation, for zone-tendency math."""
        rows = await self.list_rotations(team_id=team_id, match_id=match_id)
        positions: List[Dict[str, Any]] = []
        for r in rows:
            pts = [_safe_xy(p) for p in (r.waypoints or [])]
            pts = [p for p in pts if p is not None]
            if not pts:
                continue
            ts = r.game_time_ms if r.game_time_ms is not None else r.timestamp_ms
            positions.append({"ts": ts, "x": pts[-1][0], "y": pts[-1][1]})
        return sorted(positions, key=lambda p: p["ts"])

    # ── mapping ──

    @staticmethod
    def _to_response(row: RotationEvent) -> RotationResponse:
        meta = row.meta or {}
        waypoints = meta.get("waypoints") or []
        return RotationResponse(
            id=str(row.id),
            match_id=str(row.match_id),
            team_id=str(row.team_id),
            timestamp_ms=row.timestamp_ms,
            from_zone=meta.get("from_zone"),
            to_zone=meta.get("to_zone"),
            game_time_ms=meta.get("game_time_ms"),
            waypoints=waypoints,
            vehicle_used=bool(meta.get("vehicle_used", False)),
            fights_encountered=list(meta.get("fights_encountered") or []),
            rotation_order=meta.get("rotation_order"),
            distance_m=meta.get("distance_m"),
            waypoint_count=len(waypoints),
            computed_distance_m=float(meta.get("computed_distance_m")
                                      or polyline_distance_m(waypoints)),
            source=str(meta.get("source") or "unknown"),
        )


class DropService:
    """Record and query drop events; per-team drop statistics."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── writes ──

    async def record_drop(self, payload: DropIngestRequest) -> DropResponse:
        row = DropEvent(
            match_id=payload.match_id,
            team_id=payload.team_id,
            map_name=payload.map_name,
            location_x=payload.location_x,
            location_y=payload.location_y,
            named_location=payload.named_location,
            timestamp_ms=payload.timestamp_ms,
            contested_by=list(payload.contested_by),
            survived_drop=payload.survived_drop,
            meta={"source": payload.source},
        )
        self.db.add(row)
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise _bad_reference(exc)
        except SQLAlchemyError as exc:
            await self.db.rollback()
            raise _db_unavailable(exc)
        await self.db.refresh(row)
        return self._to_response(row)

    # ── reads ──

    async def list_drops(
        self, team_id: Optional[str] = None, match_id: Optional[str] = None
    ) -> List[DropResponse]:
        stmt = select(DropEvent).order_by(DropEvent.timestamp_ms.asc())
        if team_id:
            stmt = stmt.where(DropEvent.team_id == team_id)
        if match_id:
            stmt = stmt.where(DropEvent.match_id == match_id)
        rows = (await self.db.execute(stmt)).scalars().all()
        return [self._to_response(r) for r in rows]

    async def team_drop_stats(
        self, team_id: str, match_id: Optional[str] = None
    ) -> DropTeamStats:
        """Per-team drop stats: totals, contested count, survival rate over
        known outcomes only, top-5 favourite locations."""
        stmt = select(DropEvent).where(DropEvent.team_id == team_id)
        if match_id:
            stmt = stmt.where(DropEvent.match_id == match_id)
        rows = (await self.db.execute(stmt)).scalars().all()

        summary = survival_summary([r.survived_drop for r in rows])
        contested = sum(1 for r in rows if r.contested_by)
        locations = top_drop_locations(
            [
                {
                    "named_location": r.named_location,
                    "map_name": r.map_name,
                    "contested_by": r.contested_by,
                }
                for r in rows
            ],
            n=5,
        )
        return DropTeamStats(
            team_id=team_id,
            total_drops=summary["total_drops"],
            contested_drops=contested,
            known_outcomes=summary["known_outcomes"],
            survived=summary["survived"],
            survival_rate=summary["survival_rate"],
            favorite_locations=locations,
        )

    async def distinct_teams(
        self, match_id: Optional[str] = None
    ) -> List[str]:
        stmt = select(distinct(DropEvent.team_id))
        if match_id:
            stmt = stmt.where(DropEvent.match_id == match_id)
        rows = (await self.db.execute(stmt)).scalars().all()
        return [str(t) for t in rows]

    # ── mapping ──

    @staticmethod
    def _to_response(row: DropEvent) -> DropResponse:
        contested_by = list(row.contested_by or [])
        return DropResponse(
            id=str(row.id),
            match_id=str(row.match_id),
            team_id=str(row.team_id),
            map_name=row.map_name,
            location_x=row.location_x,
            location_y=row.location_y,
            named_location=row.named_location,
            timestamp_ms=row.timestamp_ms,
            contested=bool(contested_by),
            contested_by=contested_by,
            survived_drop=row.survived_drop,
            source=str((row.meta or {}).get("source") or "unknown"),
        )


class ZoneService:
    """Record zone phases; per-team per-match edge-vs-centre tendency."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.rotations = RotationService(db)

    # ── writes ──

    async def record_zone_phase(self, payload: ZoneIngestRequest) -> ZonePhaseResponse:
        row = ZonePhase(
            match_id=payload.match_id,
            phase_number=payload.phase_number,
            center_x=payload.center_x,
            center_y=payload.center_y,
            radius=payload.radius,
            timestamp_ms=payload.timestamp_ms,
            meta={"source": payload.source, **(payload.extra or {})},
        )
        self.db.add(row)
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise _bad_reference(exc)
        except SQLAlchemyError as exc:
            await self.db.rollback()
            raise _db_unavailable(exc)
        await self.db.refresh(row)
        return self._to_response(row)

    # ── reads ──

    async def list_zone_phases(
        self, team_id: Optional[str] = None, match_id: Optional[str] = None
    ) -> List[ZonePhaseResponse]:
        # zone phases are per-match, not per-team; team_id only scopes the
        # tendency summary, not the phase list itself.
        stmt = select(ZonePhase).order_by(ZonePhase.phase_number.asc())
        if match_id:
            stmt = stmt.where(ZonePhase.match_id == match_id)
        rows = (await self.db.execute(stmt)).scalars().all()
        return [self._to_response(r) for r in rows]

    async def tendency(
        self, team_id: Optional[str] = None, match_id: Optional[str] = None
    ) -> List[ZoneTendency]:
        """Per-team per-match edge-vs-centre tendency.

        Without any filter this is empty (a tendency needs a team and a
        match); the router states that honestly in the coverage text.
        """
        pairs: List[Tuple[str, str]] = []
        if team_id and match_id:
            pairs = [(team_id, match_id)]
        elif team_id:
            stmt = (
                select(distinct(RotationEvent.match_id))
                .where(RotationEvent.team_id == team_id)
                .order_by(RotationEvent.match_id)
            )
            pairs = [(team_id, str(m)) for m in (await self.db.execute(stmt)).scalars().all()]
        elif match_id:
            stmt = (
                select(distinct(RotationEvent.team_id))
                .where(RotationEvent.match_id == match_id)
                .order_by(RotationEvent.team_id)
            )
            pairs = [(str(t), match_id) for t in (await self.db.execute(stmt)).scalars().all()]

        out: List[ZoneTendency] = []
        for tid, mid in pairs:
            out.append(await self._team_match_tendency(tid, mid))
        return out

    async def _team_match_tendency(self, team_id: str, match_id: str) -> ZoneTendency:
        phases = await self.list_zone_phases(match_id=match_id)
        phase_dicts = [
            {
                "phase_number": p.phase_number,
                "center_x": p.center_x,
                "center_y": p.center_y,
                "radius": p.radius,
                "timestamp_ms": p.timestamp_ms,
            }
            for p in phases
        ]
        positions = await self.rotations.team_positions(team_id, match_id)
        rotations = await self.rotations.list_rotations(team_id=team_id, match_id=match_id)

        ratios = normalized_edge_ratios(positions, phase_dicts)
        avg_ratio = average(ratios)
        distances = [
            r.distance_m if r.distance_m is not None else r.computed_distance_m
            for r in rotations
        ]
        notes = zone_pull_notes(phase_dicts, positions)
        if not phases:
            notes = [
                "No zone phases recorded for this match — "
                "ingest them via POST /api/v1/analysis/ingest/zone."
            ]
        elif not positions:
            notes.append(
                "No rotation waypoints recorded for this team — tendency is "
                "unknown until rotations are ingested."
            )

        return ZoneTendency(
            team_id=team_id,
            match_id=match_id,
            phases_recorded=len(phases),
            phases_with_position=len(ratios),
            avg_normalized_distance=avg_ratio,
            tendency=classify_tendency(avg_ratio),
            avg_distance_traveled_per_phase_m=average(distances),
            notes=notes,
        )

    # ── mapping ──

    @staticmethod
    def _to_response(row: ZonePhase) -> ZonePhaseResponse:
        return ZonePhaseResponse(
            id=str(row.id),
            match_id=str(row.match_id),
            phase_number=row.phase_number,
            center_x=row.center_x,
            center_y=row.center_y,
            radius=row.radius,
            timestamp_ms=row.timestamp_ms,
            source=str((row.meta or {}).get("source") or "unknown"),
        )
