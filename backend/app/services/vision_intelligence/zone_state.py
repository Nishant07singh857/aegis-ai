# backend/app/services/vision_intelligence/zone_state.py
"""Analytics zone state built from vision ``zone_update`` events (Phase 22).

Before Phase 22 nothing consumed the vision zone stream: the zone
geometry (safe circle center/radius from the minimap) and the per-team
marker positions never met. This tracker keeps the latest REAL zone
state and, for every attributed ``position_update``, derives which of
the observed team's markers sit inside the safe zone — all in the
minimap's own pixel coordinate space, so in/out is exact geometry, not
an estimate. Distances stay in minimap pixels unless a caller supplies
a calibrated scale; nothing here invents meters.

It also detects zone-advantage swings: when the team whose markers are
best positioned for the current circle changes, the narrative
auto-flagger gets a real trigger to evaluate.
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional

from .geometry import zone_distances


class ZoneAnalyticsState:
    """Latest zone geometry + per-team in-zone derivation."""

    def __init__(self, px_per_meter: Optional[float] = None) -> None:
        # Calibrated minimap scale; None = distances stay in pixels.
        self.px_per_meter = px_per_meter
        self.phase: Optional[int] = None
        self.phase_source: Optional[str] = None
        self.safe_center: Optional[tuple] = None  # minimap px (x, y)
        self.safe_radius: Optional[float] = None  # minimap px
        self.blue_center: Optional[tuple] = None
        self.is_shrinking: Optional[bool] = None
        self.zone_damage: Optional[float] = None
        self.updated_at: Optional[float] = None
        # team_id -> {"markers": int, "in_zone": int, "out_zone": int,
        #             "nearest_enemy_px": float|None, "updated_at": float}
        self.teams: Dict[str, Dict[str, Any]] = {}
        # Team with the most markers inside the safe zone (the swing signal).
        self.favoured_team_id: Optional[str] = None
        # Phase 24: zone-pull snapshots taken on each NEW zone reveal
        # (safe-circle geometry change), newest last, capped.
        self.zone_pull_history: List[Dict[str, Any]] = []
        self.last_zone_pull: Optional[Dict[str, Any]] = None
        #: team_id -> team_name for labelled-position teams (the label
        #: came from the source stream itself, never from a guess).
        self.team_names: Dict[str, str] = {}

    # ── zone geometry ────────────────────────────────────────────

    def update_zone(self, payload: Dict[str, Any], ts: Optional[float] = None) -> bool:
        """Apply one zone_update payload. Returns True when geometry changed."""
        changed = False
        phase = payload.get("zone_phase")
        if phase is not None:
            try:
                phase_int = int(phase)
            except (TypeError, ValueError):
                phase_int = None
            if phase_int is not None and phase_int != self.phase:
                self.phase = phase_int
                changed = True
        center = payload.get("safe_center")
        radius = payload.get("safe_radius")
        geometry_changed = False
        if center and radius:
            try:
                new_center = (float(center[0]), float(center[1]))
                new_radius = float(radius)
            except (TypeError, ValueError, IndexError):
                new_center, new_radius = None, None
            if new_center is not None and (
                new_center != self.safe_center or new_radius != self.safe_radius
            ):
                self.safe_center = new_center
                self.safe_radius = new_radius
                changed = True
                geometry_changed = True
        blue = payload.get("blue_center")
        if blue:
            try:
                self.blue_center = (float(blue[0]), float(blue[1]))
            except (TypeError, ValueError, IndexError):
                pass
        if payload.get("is_shrinking") is not None:
            self.is_shrinking = bool(payload["is_shrinking"])
        if payload.get("zone_damage") is not None:
            try:
                self.zone_damage = float(payload["zone_damage"])
            except (TypeError, ValueError):
                pass
        if payload.get("phase_source"):
            self.phase_source = str(payload["phase_source"])
        self.updated_at = time.time() if ts is None else float(ts)
        if geometry_changed:
            # NEW zone reveal: snapshot every team with real positions
            # against the NEW circle (Phase 24, gap rows 21/22).
            self._snapshot_zone_pull(self.updated_at)
        return changed

    # ── zone-pull snapshots (Phase 24) ───────────────────────────

    def _team_zone_entry(self, team_id: str, snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """One team's distances against the CURRENT safe circle."""
        centroid = snap.get("centroid")
        if not centroid or self.safe_center is None or not self.safe_radius:
            return None
        dists = zone_distances(
            (float(centroid[0]), float(centroid[1])),
            self.safe_center,
            self.safe_radius,
            px_per_meter=self.px_per_meter,
        )
        if dists is None:
            return None
        return {
            "team_id": team_id,
            "team_name": self.team_names.get(team_id) or snap.get("team_name"),
            "source": snap.get("source", "observed_minimap"),
            "centroid": centroid,
            "position_updated_at": snap.get("updated_at"),
            **dists,
        }

    def _snapshot_zone_pull(self, ts: float) -> Optional[Dict[str, Any]]:
        """Rank all positioned teams against the just-revealed zone.

        Winners = inside the new zone, shortest distance-to-centre
        first. Losers = outside it, farthest outside first. When no
        team is inside, the single closest team is the (relative)
        winner; when every team is inside, the farthest from centre is
        listed last among losers = []. No positioned teams -> no
        snapshot at all (missing input -> no output).
        """
        entries = []
        for team_id, snap in self.teams.items():
            entry = self._team_zone_entry(team_id, snap)
            if entry is not None:
                entries.append(entry)
        if not entries:
            return None
        entries.sort(key=lambda e: e["distance_to_centre_px"])
        inside = [e for e in entries if e["inside_zone"]]
        outside = [e for e in entries if not e["inside_zone"]]
        outside.sort(key=lambda e: e["distance_to_edge_px"])  # farthest first
        winners = inside if inside else entries[:1]
        losers = outside
        pull = {
            "timestamp": ts,
            "phase": self.phase,
            "safe_center": list(self.safe_center) if self.safe_center else None,
            "safe_radius": self.safe_radius,
            "coordinate_space": "minimap_region_pixels",
            "teams": entries,
            "winners": [e["team_id"] for e in winners],
            "losers": [e["team_id"] for e in losers],
            "inferred": False,
            "source": "zone_geometry+team_positions",
        }
        self.last_zone_pull = pull
        self.zone_pull_history.append(pull)
        if len(self.zone_pull_history) > 32:
            self.zone_pull_history = self.zone_pull_history[-32:]
        return pull

    def team_zone_distances(self, team_id: str) -> Optional[Dict[str, Any]]:
        """Current distance-to-edge/centre for one positioned team."""
        snap = self.teams.get(team_id)
        if snap is None:
            return None
        return self._team_zone_entry(team_id, snap)

    def zone_pull_snapshot(self) -> Dict[str, Any]:
        return {
            "last": self.last_zone_pull,
            "history": list(self.zone_pull_history),
        }

    # ── per-team derivation ──────────────────────────────────────

    def update_team_positions(
        self,
        team_id: str,
        positions: Dict[str, Any],
        ts: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Derive in/out-of-zone counts for one attributed team.

        ``positions`` is the minimap marker map (``teammate_*`` /
        ``self`` markers belong to the observed team; ``enemy_*``
        markers are opponents whose team is unknown — they only feed
        the nearest-enemy distance, never a team count).

        Returns the per-team snapshot, including ``nearest_enemy_px``
        (distance in minimap pixels — callers convert only with a
        calibrated scale) and ``zone_swing`` True when the favoured
        team changed because of this update.
        """
        now = time.time() if ts is None else float(ts)
        own = []
        enemies = []
        for key, pos in (positions or {}).items():
            try:
                point = (float(pos[0]), float(pos[1]))
            except (TypeError, ValueError, IndexError):
                continue
            if str(key).startswith("enemy"):
                enemies.append(point)
            else:
                own.append(point)

        in_zone = out_zone = 0
        if self.safe_center is not None and self.safe_radius:
            cx, cy = self.safe_center
            for x, y in own:
                if math.hypot(x - cx, y - cy) <= self.safe_radius:
                    in_zone += 1
                else:
                    out_zone += 1

        nearest_enemy_px: Optional[float] = None
        if own and enemies:
            ox = sum(p[0] for p in own) / len(own)
            oy = sum(p[1] for p in own) / len(own)
            nearest_enemy_px = min(
                math.hypot(ex - ox, ey - oy) for ex, ey in enemies
            )

        previous_favoured = self.favoured_team_id
        centroid_point = (
            (sum(p[0] for p in own) / len(own), sum(p[1] for p in own) / len(own))
            if own else None
        )
        snapshot = {
            "markers": len(own),
            "in_zone": in_zone,
            "out_zone": out_zone,
            "enemy_markers": len(enemies),
            "nearest_enemy_px": nearest_enemy_px,
            "centroid": (
                [round(centroid_point[0], 1), round(centroid_point[1], 1)]
                if centroid_point else None
            ),
            "source": "observed_minimap",
            "updated_at": now,
        }
        # Phase 24: per-team distance-to-edge (signed) + to-centre for
        # the observed team's centroid, when zone geometry is known.
        if centroid_point is not None:
            dists = zone_distances(
                centroid_point, self.safe_center, self.safe_radius,
                px_per_meter=self.px_per_meter,
            )
            if dists is not None:
                snapshot.update(dists)
        self.teams[team_id] = snapshot

        # Favoured team = most own markers inside the safe zone (ties
        # keep the incumbent, so the signal only fires on a real swing).
        if self.safe_center is not None and self.safe_radius:
            best_team, best_in = None, -1
            for tid, snap in self.teams.items():
                if snap["in_zone"] > best_in:
                    best_team, best_in = tid, snap["in_zone"]
            if best_team is not None and best_in > 0:
                self.favoured_team_id = best_team
        snapshot["zone_swing"] = (
            previous_favoured is not None
            and self.favoured_team_id is not None
            and self.favoured_team_id != previous_favoured
        )
        snapshot["favoured_team_id"] = self.favoured_team_id
        return snapshot

    def update_labelled_position(
        self,
        team_id: str,
        x: float,
        y: float,
        ts: Optional[float] = None,
        team_name: Optional[str] = None,
        coordinate_space: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register one TEAM-LABELLED position (feed ingest / agent data).

        The label comes from the source stream itself — this method is
        only for streams that name the team. Anonymous minimap markers
        must NEVER be routed here. Distances are computed in whatever
        coordinate space the caller declares; zone distances are only
        attached when the space matches the zone geometry's space
        (minimap pixels by default in this state object).
        """
        now = time.time() if ts is None else float(ts)
        if team_name:
            self.team_names[team_id] = team_name
        snapshot: Dict[str, Any] = {
            "markers": 1,
            "in_zone": 0,
            "out_zone": 0,
            "enemy_markers": 0,
            "nearest_enemy_px": None,
            "centroid": [round(float(x), 1), round(float(y), 1)],
            "source": "team_labelled",
            "coordinate_space": coordinate_space,
            "team_name": team_name or self.team_names.get(team_id),
            "updated_at": now,
        }
        if coordinate_space in (None, "minimap_region_pixels"):
            dists = zone_distances(
                (float(x), float(y)), self.safe_center, self.safe_radius,
                px_per_meter=self.px_per_meter,
            )
            if dists is not None:
                snapshot.update(dists)
                snapshot["in_zone"] = 1 if dists["inside_zone"] else 0
                snapshot["out_zone"] = 0 if dists["inside_zone"] else 1
        self.teams[team_id] = snapshot
        return snapshot

    def snapshot(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "phase_source": self.phase_source,
            "safe_center": list(self.safe_center) if self.safe_center else None,
            "safe_radius": self.safe_radius,
            "blue_center": list(self.blue_center) if self.blue_center else None,
            "is_shrinking": self.is_shrinking,
            "zone_damage": self.zone_damage,
            "coordinate_space": "minimap_region_pixels",
            "favoured_team_id": self.favoured_team_id,
            "teams": self.teams,
            "last_zone_pull": self.last_zone_pull,
            "updated_at": self.updated_at,
        }
