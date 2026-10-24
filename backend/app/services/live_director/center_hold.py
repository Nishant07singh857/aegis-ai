# backend/app/services/live_director/center_hold.py
"""Center-hold derivation (Phase 31, audit item 8).

Derives a ``center_hold`` intel alert from EXISTING primitives only —
nothing new is measured, no metres, no km/h, minimap pixels only:

  * zone-centre distance (px) — ``ZoneAnalyticsState`` /
    ``team_zone_distances`` (``distance_to_centre_px``,
    ``distance_to_edge_px``, ``inside_zone``) in minimap-region pixels;
  * favoured team — ``ZoneAnalyticsState.favoured_team_id`` (most
    markers inside); reported as context, NOT a gate (a team can hold
    the centre compound while another team has more markers inside
    elsewhere — gating on favoured would suppress real holds);
  * POI ``type="compound"`` — 26 of the 127 POIs in the POI database
    (Erangel 15, Miramar 2, Sanhok 3, Rondo 2, Livik 4; counts verified
    against ``services/poi/data/*.json`` at build time);
  * stationarity — ``RotationTracker`` team state: no active rotation
    and no significant centroid movement for the dwell window.

A hold requires ALL FOUR gates: inside the safe zone + inside the
centre band + stationary dwell + standing on a compound POI. Any
missing leg -> None (missing input -> no output, never a guess).

COORDINATE-SPACE HONESTY (read before wiring): the POI database
contract accepts ONLY normalized full-map coordinates. Minimap-region
pixels must NEVER be fed to it — that would name a wrong place. The
caller resolves the POI first (via the existing
``DirectorDerivations.poi_name_for`` / ``poi_database().nearest``
path over a team-labelled normalized position, or via
``area_label_for``) and passes the resolved POI dict in. This module
only checks ``poi["type"] == "compound"``; it performs no lookup and
no coordinate conversion.

Output: a ``center_hold`` alert dict carrying a zone-pressure weight
(10.0, reusing the existing zone-pressure scale — same magnitude as
zone-pull / gatekeep — NOT a new measured number) and a
semi_auto-only suggestion payload for that team's feed. The module
never publishes or suggests anything itself; the coordinator wires
it (see HOOK NOTE).

NOT BUILT — choke-point variant: the audit's sibling derivation
(bridge POIs) is a DATA BACKLOG, not a derivation. Bridge POIs total
2, both on Erangel, zero on every other map — a "choke_point" alert
built on that would fire on one map only and mislead everywhere
else. Fill the POI data first, then derive. No bridge logic exists
in this module, deliberately.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

#: Centre band: centroid within this fraction of the safe radius from
#: the centre counts as "holding the centre". Heuristic design choice
#: (half the radius), labelled as such in the alert — not measured.
CENTER_BAND_FRACTION = 0.5
#: Minimum stationary dwell to count as a hold rather than passing
#: through (a team looting through a compound does not sit 60s).
#: Heuristic design choice, not a measured number. For scale:
#: RotationTracker closes a rotation after 10s of quiet
#: (ROTATION_QUIET_S), so 60s is well past "just stopped moving".
CENTER_HOLD_DWELL_MIN_S = 60.0
#: Suggested zone-pressure weight, reusing the existing scale
#: (zone-pull / gatekeep are 10.0 in service.py).
CENTER_HOLD_ZONE_PRESSURE = 10.0


def stationary_dwell_from_tracker(
    team_state: Optional[Dict[str, Any]], now: float
) -> Optional[float]:
    """Stationary dwell (seconds) from a RotationTracker team state.

    Returns None when the team is in an active rotation (moving by
    definition), when no movement history exists, or when the state
    is missing. Otherwise ``now - last_move_ts``; when no move was
    ever recorded, ``now - anchor_ts`` (settled since the anchor).
    The caller passes ``RotationTracker.teams[team_id]`` verbatim —
    this function only reads its documented keys (``active_rotation``,
    ``last_move_ts``, ``anchor``).
    """
    if not team_state:
        return None
    if team_state.get("active_rotation") is not None:
        return None
    last_move = team_state.get("last_move_ts")
    if last_move is not None:
        try:
            return max(0.0, float(now) - float(last_move))
        except (TypeError, ValueError):
            return None
    anchor = team_state.get("anchor")
    if anchor:
        try:
            return max(0.0, float(now) - float(anchor[2]))
        except (TypeError, ValueError, IndexError):
            return None
    return None


def evaluate_center_hold(
    *,
    team_id: str,
    distance_to_centre_px: Optional[float],
    distance_to_edge_px: Optional[float],
    inside_zone: Optional[bool],
    safe_radius_px: Optional[float],
    dwell_s: Optional[float],
    poi: Optional[Dict[str, Any]],
    favoured_team_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """The four-gate center-hold derivation. Returns the alert, or None.

    Gates (ALL required): ``inside_zone`` is True; ``safe_radius_px``
    is positive and ``distance_to_centre_px`` <= 0.5 * radius (centre
    band); ``dwell_s`` >= 60s (stationary); ``poi`` resolves to
    ``type == "compound"``. A team id that is empty, or any gate input
    missing / non-numeric, yields None — never a partial hold.
    """
    team = str(team_id or "").strip()
    if not team or team == "Unknown":
        return None
    if inside_zone is not True:
        return None
    if safe_radius_px is None or distance_to_centre_px is None:
        return None
    try:
        radius = float(safe_radius_px)
        dist_centre = float(distance_to_centre_px)
        dwell = float(dwell_s) if dwell_s is not None else None
    except (TypeError, ValueError):
        return None
    if radius <= 0 or dist_centre < 0:
        return None
    centre_band_px = CENTER_BAND_FRACTION * radius
    if dist_centre > centre_band_px:
        return None
    if dwell is None or dwell < CENTER_HOLD_DWELL_MIN_S:
        return None
    if not isinstance(poi, dict) or poi.get("type") != "compound":
        return None
    return {
        "kind": "center_hold",
        "team_id": team,
        "poi_name": poi.get("name"),
        "poi_type": "compound",
        "distance_to_centre_px": round(dist_centre, 1),
        "distance_to_edge_px": (
            round(float(distance_to_edge_px), 1)
            if distance_to_edge_px is not None
            else None
        ),
        "safe_radius_px": round(radius, 1),
        "centre_band_px": round(centre_band_px, 1),
        "centre_band_fraction": CENTER_BAND_FRACTION,
        "inside_zone": True,
        "stationary_dwell_s": round(dwell, 1),
        "dwell_min_s": CENTER_HOLD_DWELL_MIN_S,
        "favoured_team_id": favoured_team_id,
        "is_favoured": (
            (favoured_team_id == team) if favoured_team_id else None
        ),
        "inferred": True,
        "source": "zone_geometry+compound_poi+rotation_stationarity",
        "units": "minimap_pixels",
        "suggested_weight": {
            "term": "zone_pressure",
            "points": CENTER_HOLD_ZONE_PRESSURE,
        },
        "suggestion": {
            "suggestion_type": "scene",
            "scene": "main",
            "team_id": team,
            "auto_cut": False,
            "reason": (
                f"Center hold: {team} stationary "
                f"{round(dwell, 1):g}s on compound "
                f"{poi.get('name') or 'POI'} inside the centre band "
                f"({round(dist_centre, 1):g}px of centre, band "
                f"{round(centre_band_px, 1):g}px)."
            ),
        },
    }


def evaluate_center_hold_from_zone_entry(
    *,
    team_id: str,
    zone_entry: Optional[Dict[str, Any]],
    dwell_s: Optional[float],
    poi: Optional[Dict[str, Any]],
    favoured_team_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Convenience wrapper over a ``team_zone_distances`` entry dict.

    Reads ``distance_to_centre_px`` / ``distance_to_edge_px`` /
    ``inside_zone`` / ``safe_radius`` from the entry. Note the key
    name: zone_state's snapshot carries ``safe_radius`` (not
    ``safe_radius_px``) — both spellings are accepted here.
    """
    if not isinstance(zone_entry, dict):
        return None
    radius = zone_entry.get("safe_radius_px", zone_entry.get("safe_radius"))
    return evaluate_center_hold(
        team_id=team_id,
        distance_to_centre_px=zone_entry.get("distance_to_centre_px"),
        distance_to_edge_px=zone_entry.get("distance_to_edge_px"),
        inside_zone=zone_entry.get("inside_zone"),
        safe_radius_px=radius,
        dwell_s=dwell_s,
        poi=poi,
        favoured_team_id=favoured_team_id,
    )


# ── HOOK NOTE (coordinator: service.py + VI service, not this module) ──
# H4 — production wiring (two halves; neither lives in this module):
#   (a) PRODUCER (VI service, where zone_pull alerts are emitted):
#       for each team with a labelled normalized position, resolve
#       its POI via derivations.poi_name_for / poi_database nearest
#       (normalized space ONLY), take its zone entry from
#       zone_state.team_zone_distances(team_id), and its dwell from
#       stationary_dwell_from_tracker(
#           derivations.rotations.teams.get(team_id), now).
#       If evaluate_center_hold(...) returns an alert, emit it as an
#       intel_alert {"kind": "center_hold", ...alert} on the bus.
#       Missing POI / missing zone entry / missing tracker state ->
#       no alert (the function returns None; do not substitute).
#   (b) CONSUMER (LiveDirector._on_intel_alert, new kind branch):
#       elif kind == "center_hold":
#           resolved = await self._resolve_team(data.get("team_id"))
#           if resolved is None:
#               self.stats["dropped_unknown_team"] += 1
#           else:
#               await note_zone_pressure(resolved[0],
#                                        CENTER_HOLD_ZONE_PRESSURE)
#               # propose_suggestion is semi_auto-only by contract
#               # (silent no-op in full_auto/manual), so calling it
#               # here can never auto-cut; it is anticipation only.
#               await self._director.propose_suggestion(
#                   suggestion_type="scene",
#                   payload={"scene": "main",
#                            "active_camera": resolved[1],
#                            "team_id": resolved[0]},
#                   reason=data["suggestion"]["reason"],
#                   confidence=0.6, created_by="live_director")
#       _resolve_team returns (team_id, slot), so no separate slot
#       lookup is needed; an unresolvable team suggests nothing.
#       Zone-pressure weight 10.0 matches the
#       existing zone-pull/gatekeep scale and sits under the
#       ZONE_PRESSURE_CAP of 20.0, so it cannot dominate a fight term.
#   CHOKE-POINT: do NOT add a bridge/choke branch until POI data
#   exists beyond Erangel's 2 bridge POIs (data backlog, see module
#   docstring). Any choke alert built now would be Erangel-only.
