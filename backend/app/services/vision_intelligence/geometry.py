# backend/app/services/vision_intelligence/geometry.py
"""Pure geometry + documented thresholds for director derivations (Phase 24).

Everything here is a pure function over REAL spectator-observable
inputs: minimap marker coordinates (minimap-region pixels), zone circle
geometry (same pixel space) and — only when a source explicitly
supplies them — team-labelled positions. No function here invents a
team, a position or a unit conversion:

  * Distances are minimap pixels unless the caller supplies a
    calibrated ``px_per_meter`` (env ``AEGIS_VISION_MINIMAP_PX_PER_METER``);
    metre fields are only produced alongside pixel fields, never
    instead of them.
  * Bearings are compass degrees in screen space: 0 = up (north on the
    minimap), 90 = right (east), clockwise. Screen y grows downwards,
    so north is -y.

Thresholds (all documented, all heuristic where labelled):

  * ``SPLIT_THRESHOLD_PX = 80`` — teammate spread above this (max
    pairwise distance between the observed team's own markers) is a
    "split" shape. The minimap region is ~500x300 px at 1080p, so
    80 px is roughly a compound-to-compound separation on the local
    minimap — wide enough that the squad cannot instantly trade.
  * ``ISOLATED_MIN_DISTANCE_PX = 60`` and ``ISOLATED_FACTOR = 2.0`` —
    one marker is "isolated" when its distance to the centroid of the
    other markers exceeds BOTH 60 px AND 2x the other markers' own
    internal spread (so a uniformly spread squad flags nobody).
  * ``ROTATION_START_DISPLACEMENT_PX = 25`` — a tracked team's centroid
    must move at least this far from its settled anchor before a
    rotation is declared started (below that is loot-shuffling /
    compound micro-movement, not a rotation).
  * ``ROTATION_QUIET_S = 10`` — no significant centroid movement for
    this long ends a rotation.
  * ``VEHICLE_SPEED_PX_PER_S = 12`` — sustained centroid speed above
    this, with vehicle markers visible, is classified as a vehicle
    rotation. On-foot sprint on the local minimap is slower than this
    in practice; the value is a heuristic and the output is labelled
    ``inferred: true``.
  * ``GATEKEEP_EDGE_BAND_PX = 15`` — observed team inside the zone but
    within this many pixels of the edge = gatekeep position.
  * Rotation urgency bands (``urgency_band``): based on the margin
    between the zone-close countdown and the team's estimated time to
    reach the zone edge at its current speed. CRITICAL margin < 0 s
    (cannot make it at current speed) or, when not moving, <= 45 s to
    close; HIGH margin < 30 s (or not moving and <= 120 s); MEDIUM
    margin < 90 s (or not moving with any finite countdown); LOW when
    inside the zone or margin >= 90 s. Heuristic — labelled as such.
  * Bleed-out urgency base thresholds 20 s / 45 s of continuous knock
    duration (``BLEED_WARN_S`` / ``BLEED_CRITICAL_S``), scaled down for
    repeat knocks (each prior knock multiplies thresholds by 0.75,
    floor 0.4x) because BGMI bleed-out accelerates per knock in a
    match. Heuristic — labelled as such.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence, Tuple

Point = Tuple[float, float]

SPLIT_THRESHOLD_PX = 80.0
ISOLATED_MIN_DISTANCE_PX = 60.0
ISOLATED_FACTOR = 2.0
ROTATION_START_DISPLACEMENT_PX = 25.0
ROTATION_PENDING_DISPLACEMENT_PX = 5.0
ROTATION_QUIET_S = 10.0
VEHICLE_SPEED_PX_PER_S = 12.0
VEHICLE_MIN_SAMPLES = 3
VEHICLE_WINDOW_S = 10.0
GATEKEEP_EDGE_BAND_PX = 15.0
BLEED_WARN_S = 20.0
BLEED_CRITICAL_S = 45.0
BLEED_REPEAT_SCALE = 0.75
BLEED_MIN_SCALE = 0.4

#: Coordinate spaces in which (x, y) are normalized 0..1 full-map
#: coordinates — the ONLY spaces the POI database contract accepts.
#: Minimap-region pixels are a local zoomed view and are deliberately
#: NOT in this set: naming a POI from them would be a guess.
NORMALIZED_MAP_SPACES = frozenset(
    {"map_normalized", "normalized_map", "map_image_normalized", "normalized"}
)


def is_normalized_map_space(coordinate_space: Optional[str]) -> bool:
    return bool(coordinate_space) and coordinate_space in NORMALIZED_MAP_SPACES


def centroid(points: Sequence[Point]) -> Optional[Point]:
    if not points:
        return None
    n = len(points)
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


def distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def bearing_degrees(a: Point, b: Point) -> Optional[float]:
    """Compass bearing from point ``a`` to point ``b`` (screen space).

    0 = up/north, 90 = right/east, clockwise, range [0, 360).
    None when the points coincide (no direction exists).
    """
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    if dx == 0 and dy == 0:
        return None
    # Screen y grows downward: north is -y, east is +x.
    return round(math.degrees(math.atan2(dx, -dy)) % 360.0, 1)


def zone_distances(
    point: Point,
    safe_center: Optional[Point],
    safe_radius: Optional[float],
    px_per_meter: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Distance-to-centre and signed distance-to-edge for one point.

    Signed edge distance: positive = that many units INSIDE the safe
    circle (margin to the edge), negative = that far OUTSIDE it.
    Returns None when zone geometry is unknown — never estimated.
    """
    if safe_center is None or not safe_radius:
        return None
    d_centre = distance(point, safe_center)
    d_edge = float(safe_radius) - d_centre
    out: Dict[str, Any] = {
        "distance_to_centre_px": round(d_centre, 1),
        "distance_to_edge_px": round(d_edge, 1),
        "inside_zone": d_edge >= 0,
        "units": "minimap_pixels",
    }
    if px_per_meter:
        out["distance_to_centre_m"] = round(d_centre / px_per_meter, 1)
        out["distance_to_edge_m"] = round(d_edge / px_per_meter, 1)
        out["units"] = "minimap_pixels+meters_calibrated"
    return out


def max_pairwise_distance(points: Sequence[Point]) -> Optional[float]:
    """Teammate spread: the largest distance between any two markers."""
    if len(points) < 2:
        return None
    best = 0.0
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            best = max(best, distance(points[i], points[j]))
    return best


def isolated_marker(
    markers: Dict[str, Point],
) -> Optional[Dict[str, Any]]:
    """Find one marker far from the rest of the squad, if any.

    A marker qualifies when its distance to the centroid of the OTHER
    markers exceeds both ``ISOLATED_MIN_DISTANCE_PX`` and
    ``ISOLATED_FACTOR`` x the others' internal spread. Returns
    ``{"marker": key, "distance_to_squad_px": float}`` or None.
    Needs at least 3 markers (a pair cannot have an isolated member).
    """
    if len(markers) < 3:
        return None
    keys = list(markers.keys())
    best: Optional[Dict[str, Any]] = None
    for key in keys:
        others = [markers[k] for k in keys if k != key]
        c = centroid(others)
        if c is None:
            continue
        d = distance(markers[key], c)
        others_spread = max_pairwise_distance(others) or 0.0
        if d > ISOLATED_MIN_DISTANCE_PX and d > ISOLATED_FACTOR * others_spread:
            if best is None or d > best["distance_to_squad_px"]:
                best = {"marker": key, "distance_to_squad_px": round(d, 1)}
    return best


def urgency_band(
    distance_to_edge_px: Optional[float],
    time_to_close_s: Optional[float],
    eta_s: Optional[float],
) -> Optional[str]:
    """Rotation urgency: LOW / MED / HIGH / CRITICAL (heuristic).

    Inputs: signed distance-to-edge (negative = outside by that many
    px), seconds until the zone closes, and the team's estimated
    seconds to reach the edge at its current speed (None when the team
    is not measurably moving toward the zone). Returns None when the
    countdown is unknown — urgency is never invented.
    """
    if distance_to_edge_px is None or time_to_close_s is None:
        return None
    if distance_to_edge_px >= 0:
        return "LOW"  # already inside the zone
    if eta_s is None:
        # Outside and not measurably moving toward the zone.
        if time_to_close_s <= 45:
            return "CRITICAL"
        if time_to_close_s <= 120:
            return "HIGH"
        return "MED"
    margin = time_to_close_s - eta_s
    if margin < 0:
        return "CRITICAL"
    if margin < 30:
        return "HIGH"
    if margin < 90:
        return "MED"
    return "LOW"


def bleed_thresholds(prior_knocks: int) -> Tuple[float, float]:
    """(warn_s, critical_s) bleed-out thresholds, scaled for repeats."""
    scale = max(
        BLEED_MIN_SCALE, BLEED_REPEAT_SCALE ** max(0, int(prior_knocks))
    )
    return (round(BLEED_WARN_S * scale, 1), round(BLEED_CRITICAL_S * scale, 1))


def bleed_urgency_level(
    duration_s: float, prior_knocks: int = 0
) -> Optional[str]:
    """'warn' / 'critical' once a knock duration crosses a threshold."""
    warn, critical = bleed_thresholds(prior_knocks)
    if duration_s >= critical:
        return "critical"
    if duration_s >= warn:
        return "warn"
    return None


def classify_rotation_start(
    start_ts: float,
    phase_start_ts: Optional[float],
    phase_duration_s: Optional[float],
) -> Optional[str]:
    """'early' / 'late' vs the zone phase midpoint (heuristic).

    Early = rotation began before the midpoint of the zone phase it
    started in; late = after. None when phase timing is unknown.
    """
    if phase_start_ts is None or not phase_duration_s or phase_duration_s <= 0:
        return None
    if start_ts < phase_start_ts or start_ts > phase_start_ts + phase_duration_s:
        return None
    midpoint = phase_start_ts + phase_duration_s / 2.0
    return "early" if start_ts < midpoint else "late"
