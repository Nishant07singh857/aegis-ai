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
from typing import Any, Dict, Optional


class ZoneAnalyticsState:
    """Latest zone geometry + per-team in-zone derivation."""

    def __init__(self) -> None:
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
        return changed

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
        snapshot = {
            "markers": len(own),
            "in_zone": in_zone,
            "out_zone": out_zone,
            "enemy_markers": len(enemies),
            "nearest_enemy_px": nearest_enemy_px,
            "centroid": (
                [round(sum(p[0] for p in own) / len(own), 1),
                 round(sum(p[1] for p in own) / len(own), 1)]
                if own else None
            ),
            "updated_at": now,
        }
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
            "updated_at": self.updated_at,
        }
