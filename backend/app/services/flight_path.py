# backend/app/services/flight_path.py
"""Flight-path service (Phase 24, gap row 10).

The flight path is **director-set**: at match start a human director
(who can see the pre-match plane line on the observer feed) records
``map_id + start POI + end POI``. AEGIS never detects or invents it —
there is no vision source for the plane line (see
docs/SPECTATOR_COVERAGE.md row 10). Every surface that shows the path
labels it "director-set".

State is in-memory (like the director / zone services) and a
``flight_path_set`` bus event is emitted when a bus publisher is wired.

Predicted drops — corridor math
--------------------------------
Given the set path as a segment A->B in normalized map-image space
(0..1 on both axes, the POI database's coordinate space), a recorded
drop at point P is "on the corridor" when the perpendicular distance
from P to the segment A-B is <= ``corridor_half_width`` (default
0.12 — roughly a 12%-of-map-width band either side of the line, wide
enough to cover parachute glide from the line) AND the projection of P
onto A-B falls within the segment (parameter t in [0, 1], with a small
0.05 overshoot allowance at both ends for drops just past an endpoint).
Drop points come from recorded ``drop_events`` history: the event's own
``location_x/location_y`` when present, otherwise the coordinates of its
``named_location`` resolved through the POI database. Drops with no
resolvable point are skipped — never placed by guesswork. No history ->
an honest empty list.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Callable, Dict, List, Optional

import structlog

logger = structlog.get_logger("aegis.flight_path")

#: Default corridor half-width in normalized map units.
DEFAULT_CORRIDOR_HALF_WIDTH = 0.12
#: Projection overshoot allowance past either endpoint (normalized t).
ENDPOINT_OVERSHOOT = 0.05


def _poi_lookup():
    """Return the POI database singleton, or None when W4's module is
    not present yet. The flight service must keep working (names stored,
    coordinates honestly null) rather than crash the whole backend."""
    try:
        from app.services.poi.poi_database import get_poi_database

        return get_poi_database()
    except Exception:  # pragma: no cover - POI db not landed yet
        return None


def point_to_segment_distance(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    """Perpendicular distance from P to segment A-B (normalized units)."""
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    closest_x, closest_y = ax + t * dx, ay + t * dy
    return math.hypot(px - closest_x, py - closest_y)


def projection_t(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    """Projection parameter of P onto line A-B (0 at A, 1 at B)."""
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return 0.0
    return ((px - ax) * dx + (py - ay) * dy) / length_sq


class FlightPathService:
    """Director-set flight path + predicted-drop corridor queries."""

    def __init__(
        self,
        poi_db: Any = None,
        publish: Optional[Callable] = None,
    ) -> None:
        self._poi_db = poi_db  # None -> lazily resolved singleton
        self._publish = publish
        self._path: Optional[Dict[str, Any]] = None
        # Phase 25 (P1-6): drop-clash alerts already emitted, keyed by
        # (poi, team-set, basis) so repeat checks never spam the bus,
        # plus the alert log itself for read surfaces.
        self._drop_clash_emitted: set = set()
        self.drop_clash_alerts: List[Dict[str, Any]] = []

    # ── POI db access ───────────────────────────────────────────

    @property
    def poi_db(self) -> Any:
        if self._poi_db is not None:
            return self._poi_db
        return _poi_lookup()

    def set_poi_db(self, poi_db: Any) -> None:
        self._poi_db = poi_db

    def set_publisher(self, publish: Callable) -> None:
        self._publish = publish

    def _resolve_poi(self, map_id: str, name: str) -> Optional[Dict[str, Any]]:
        db = self.poi_db
        if db is None or not name:
            return None
        try:
            return db.by_name(map_id, name)
        except Exception:  # pragma: no cover - defensive
            return None

    # ── writes ──────────────────────────────────────────────────

    async def set_path(
        self,
        *,
        map_id: str,
        start_poi: str,
        end_poi: str,
        updated_by: Optional[str] = None,
        match_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record the director-set flight path. Raises ValueError on
        empty map / POI names. Unknown POI names are stored as named
        references with null coordinates (honest — the director typed
        them; AEGIS does not invent coordinates for them)."""
        map_id = (map_id or "").strip()
        start_poi = (start_poi or "").strip()
        end_poi = (end_poi or "").strip()
        if not map_id:
            raise ValueError("map_id must be a non-empty string.")
        if not start_poi or not end_poi:
            raise ValueError("start_poi and end_poi must be non-empty strings.")
        start = self._resolve_poi(map_id, start_poi)
        end = self._resolve_poi(map_id, end_poi)
        self._path = {
            "is_set": True,
            "source": "director-set",
            "map_id": map_id,
            "match_id": match_id,
            "start_poi": start_poi,
            "end_poi": end_poi,
            "start": (
                {"name": start["name"], "x": start["x"], "y": start["y"]}
                if start
                else {"name": start_poi, "x": None, "y": None}
            ),
            "end": (
                {"name": end["name"], "x": end["x"], "y": end["y"]}
                if end
                else {"name": end_poi, "x": None, "y": None}
            ),
            "updated_by": updated_by,
        }
        if self._publish is not None:
            try:
                await self._publish("flight_path_set", dict(self._path))
            except Exception:  # pragma: no cover - defensive
                pass
        logger.info(
            "flight_path_set", map_id=map_id, start=start_poi, end=end_poi
        )
        return self.snapshot()

    def clear(self) -> None:
        self._path = None
        # A new match's path must re-arm clash detection honestly.
        self._drop_clash_emitted = set()
        self.drop_clash_alerts = []

    # ── reads ───────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        if self._path is None:
            return {
                "is_set": False,
                "source": None,
                "map_id": None,
                "match_id": None,
                "start_poi": None,
                "end_poi": None,
                "start": None,
                "end": None,
                "updated_by": None,
            }
        return dict(self._path)

    # ── predicted drops ─────────────────────────────────────────

    def _drop_point(self, row: Dict[str, Any], map_id: str) -> Optional[tuple]:
        x, y = row.get("location_x"), row.get("location_y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            return float(x), float(y)
        named = row.get("named_location")
        if named:
            poi = self._resolve_poi(map_id, str(named))
            if poi is not None:
                return float(poi["x"]), float(poi["y"])
        return None

    def predicted_drops(
        self,
        drop_rows: List[Dict[str, Any]],
        corridor_half_width: float = DEFAULT_CORRIDOR_HALF_WIDTH,
    ) -> List[Dict[str, Any]]:
        """Teams whose recorded drops lie on the flight-path corridor.

        ``drop_rows``: dicts with ``team_id`` (+ optional ``team_name``),
        ``named_location``, ``location_x`` / ``location_y``,
        ``contested_by``. Returns one entry per team:
        ``{team_id, team_name, usual_poi, drop_count, contested_drops,
        contested}`` sorted by drop_count desc. Empty when no path is
        set, the path endpoints have no coordinates, or no recorded
        drop falls on the corridor — never fabricated.
        """
        if self._path is None:
            return []
        start, end = self._path["start"], self._path["end"]
        if (
            start is None or end is None
            or start.get("x") is None or end.get("x") is None
        ):
            return []
        ax, ay = float(start["x"]), float(start["y"])
        bx, by = float(end["x"]), float(end["y"])
        map_id = self._path["map_id"]

        per_team: Dict[str, Dict[str, Any]] = {}
        poi_counts: Dict[str, Counter] = defaultdict(Counter)
        for row in drop_rows or []:
            point = self._drop_point(row, map_id)
            if point is None:
                continue
            px, py = point
            t = projection_t(px, py, ax, ay, bx, by)
            if t < -ENDPOINT_OVERSHOOT or t > 1.0 + ENDPOINT_OVERSHOOT:
                continue
            if point_to_segment_distance(px, py, ax, ay, bx, by) > corridor_half_width:
                continue
            team_id = str(row.get("team_id") or "")
            if not team_id:
                continue
            entry = per_team.setdefault(
                team_id,
                {
                    "team_id": team_id,
                    "team_name": row.get("team_name"),
                    "usual_poi": None,
                    "drop_count": 0,
                    "contested_drops": 0,
                    "contested": False,
                },
            )
            entry["drop_count"] += 1
            if row.get("team_name") and not entry["team_name"]:
                entry["team_name"] = row["team_name"]
            named = row.get("named_location")
            if named:
                poi_counts[team_id][str(named)] += 1
            contested_by = row.get("contested_by") or []
            if contested_by:
                entry["contested_drops"] += 1
                entry["contested"] = True
        for team_id, entry in per_team.items():
            if poi_counts[team_id]:
                entry["usual_poi"] = poi_counts[team_id].most_common(1)[0][0]
        return sorted(
            per_team.values(), key=lambda e: (-e["drop_count"], e["team_id"])
        )

    # ── drop-clash alerts (Phase 25, P1-6) ──────────────────────

    def detect_drop_clashes(
        self,
        drop_rows: List[Dict[str, Any]],
        corridor_half_width: float = DEFAULT_CORRIDOR_HALF_WIDTH,
        history_rows: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Drop clashes in recorded drop data — pure, no emission.

        Two bases, both from REAL recorded rows only:

        * ``predicted`` — 2+ teams whose corridor predicted drops
          (``predicted_drops``) share the same usual POI. Only exists
          pre-drop, from the flight corridor + drop history.
        * ``observed`` — 2+ distinct teams with recorded drops at the
          same named POI in ``drop_rows`` itself.

        Phase 31: ``history_rows`` (default: ``drop_rows``) feeds the
        predicted basis, while ``drop_rows`` feeds the observed basis.
        Splitting them matters in production: history spans past
        matches (the right input for "where do teams usually drop"),
        but observed clashes must come from THIS match's drops only —
        feeding all history as observed rows would alert on old
        matches' contests. Omitting ``history_rows`` preserves the
        exact pre-Phase-31 behaviour (both bases over ``drop_rows``).

        Distances are corridor / normalized map units only — no metre
        figure is ever stated (no calibrated scale exists here).
        No flight path / no history / no shared POI -> empty list:
        a clash is never synthesized. The flight path gate applies to
        BOTH bases: the drop-clash surface is a plane-phase product of
        the director-set path, so without one no alert exists at all.
        """
        if self._path is None:
            return []
        alerts: List[Dict[str, Any]] = []
        seen: set = set()

        def _add(poi: str, teams: List[Dict[str, Any]], basis: str,
                 source: str) -> None:
            team_ids = sorted({str(t["team_id"]) for t in teams if t.get("team_id")})
            if len(team_ids) < 2:
                return
            key = (poi, tuple(team_ids), basis)
            if key in seen:
                return
            seen.add(key)
            names = {
                str(t["team_id"]): t.get("team_name")
                for t in teams
                if t.get("team_id")
            }
            alerts.append(
                {
                    "kind": "drop_clash",
                    "poi": poi,
                    "teams": team_ids,
                    "team_names": [names.get(t) for t in team_ids],
                    "basis": basis,
                    "source": source,
                    # Predicted clashes are inference over recorded
                    # history; observed clashes are the records
                    # themselves.
                    "inferred": basis == "predicted",
                    "units": "normalized_map_coordinates",
                    "corridor_half_width": corridor_half_width,
                    "map_id": self._path.get("map_id") if self._path else None,
                    "match_id": self._path.get("match_id") if self._path else None,
                }
            )

        # Predicted basis (needs a set path; predicted_drops enforces).
        # Phase 31: predicted over history_rows (past matches' usual
        # drops), observed over this match's drop_rows.
        by_poi: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for entry in self.predicted_drops(
            history_rows if history_rows is not None else drop_rows,
            corridor_half_width=corridor_half_width,
        ):
            if entry.get("usual_poi"):
                by_poi[str(entry["usual_poi"])].append(entry)
        for poi, teams in by_poi.items():
            _add(poi, teams, "predicted", "predicted_drops_corridor")

        # Observed basis: the recorded rows themselves.
        observed: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in drop_rows or []:
            named = row.get("named_location")
            team_id = str(row.get("team_id") or "")
            if named and team_id:
                observed[str(named)].append(
                    {"team_id": team_id, "team_name": row.get("team_name")}
                )
        for poi, teams in observed.items():
            _add(poi, teams, "observed", "recorded_drop_events")

        return alerts

    async def check_drop_clashes(
        self,
        drop_rows: List[Dict[str, Any]],
        corridor_half_width: float = DEFAULT_CORRIDOR_HALF_WIDTH,
        history_rows: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Detect clashes and emit NEW ones as bus ``intel_alert``s.

        Emission follows the Phase 24 alert conventions: the payload
        is an ``intel_alert`` whose ``kind`` is ``drop_clash`` (the
        live director is its consumer). Each (poi, teams, basis)
        signature is emitted at most once per set flight path.
        Returns only the alerts emitted by THIS call. With no
        publisher wired the alerts are still recorded and returned —
        detection never depends on the bus.

        Phase 31: ``history_rows`` is forwarded to
        :meth:`detect_drop_clashes` (predicted basis over history,
        observed basis over ``drop_rows``).
        """
        fresh: List[Dict[str, Any]] = []
        for alert in self.detect_drop_clashes(
            drop_rows,
            corridor_half_width=corridor_half_width,
            history_rows=history_rows,
        ):
            signature = (
                alert["poi"],
                tuple(alert["teams"]),
                alert["basis"],
            )
            if signature in self._drop_clash_emitted:
                continue
            self._drop_clash_emitted.add(signature)
            self.drop_clash_alerts.append(alert)
            fresh.append(alert)
            if self._publish is not None:
                try:
                    await self._publish("intel_alert", dict(alert))
                except Exception:  # pragma: no cover - defensive
                    pass
            logger.info(
                "drop_clash_detected",
                poi=alert["poi"],
                teams=alert["teams"],
                basis=alert["basis"],
            )
        return fresh

    def drop_clash_snapshot(self) -> List[Dict[str, Any]]:
        """Every drop-clash alert emitted for the current path."""
        return list(self.drop_clash_alerts)


async def fetch_drop_rows(
    db: Any,
    *,
    match_id: Optional[str] = None,
    map_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Phase 31: read recorded ``drop_events`` rows for clash checks.

    Same row shape and scoping as the flight API's predicted-drops
    read (``GET /api/v1/flight/predicted-drops``): team names joined
    from the Team table, rows scoped to ``map_id`` when both sides
    name one, and to ``match_id`` when given. Any DB failure yields
    an honest empty list — the caller then checks nothing, rather
    than alerting on partial data.
    """
    rows: List[Dict[str, Any]] = []
    try:
        from sqlalchemy import select

        from app.analysis.models import DropEvent
        from app.db.models.team import Team

        stmt = select(DropEvent)
        if match_id:
            stmt = stmt.where(DropEvent.match_id == match_id)
        rows_db = (await db.execute(stmt)).scalars().all()
        team_names: Dict[str, str] = {}
        team_ids = {str(r.team_id) for r in rows_db}
        if team_ids:
            teams = (
                await db.execute(select(Team).where(Team.id.in_(team_ids)))
            ).scalars().all()
            team_names = {str(t.id): t.name for t in teams}
        for r in rows_db:
            if r.map_name and map_id:
                if str(r.map_name).strip().lower() != str(map_id).strip().lower():
                    continue
            rows.append(
                {
                    "team_id": str(r.team_id),
                    "team_name": team_names.get(str(r.team_id)),
                    "named_location": r.named_location,
                    "location_x": r.location_x,
                    "location_y": r.location_y,
                    "contested_by": list(r.contested_by or []),
                }
            )
    except Exception:
        rows = []
    return rows


#: Process-wide singleton (wired in app/main.py lifespan).
flight_path_service = FlightPathService()


def get_flight_path_service() -> FlightPathService:
    return flight_path_service
