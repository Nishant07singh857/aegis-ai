# backend/app/services/grenade_trajectory/engine.py
"""Endpoint-based grenade arc ESTIMATOR (Phase 23, W4).

This module does NOT track a projectile. A spectator broadcast feed
cannot measure a grenade mid-flight (see docs/GRENADE_TRAJECTORY.md):
the projectile is visible for 1-2 frames at broadcast FPS, is routinely
occluded, and the feed carries no depth. What CAN be known, when a feed
reports them, are two endpoints: where the thrower stood and where the
explosion happened. Given ONLY those endpoints this module draws the
single most conventional arc consistent with them:

  * a symmetric projectile parabola under standard gravity,
  * launched at an ASSUMED angle (default 45 degrees — the angle is an
    assumption, never an observation),
  * sampled into a polyline of {x, y, h} points, where (x, y) is the
    ground track in the endpoints' own coordinate space and ``h`` is
    the modelled height above the ground track at that sample.

Everything this module returns is an estimate derived from endpoints
only. Callers must keep ``estimated: true`` on every record built from
it. Distances are reported in the endpoints' coordinate units unless
the caller supplies a calibrated ``units_per_meter`` scale, in which
case SI values (meters, seconds, m/s) are computed with g = 9.8 m/s^2
— BGMI does not publish its exact gravity constant, so even then the
flight time / launch speed are model outputs, not measurements.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence, Tuple

#: The method label stamped on every record built from this engine.
METHOD = "endpoint_arc_estimate"
#: The confidence basis stamped on every record: endpoints only, no
#: mid-flight observation of any kind.
CONFIDENCE_BASIS = "endpoints_only"

#: Assumed launch angle. 45 degrees is the canonical max-range throw;
#: nothing in a spectator feed reveals the real angle, so it is an
#: explicit, labelled assumption everywhere it surfaces.
DEFAULT_LAUNCH_ANGLE_DEG = 45.0
LAUNCH_ANGLE_BASIS = "assumed_default_45deg"

#: Standard gravity, m/s^2. BGMI/PUBG do not publish their exact
#: projectile gravity; this is the documented assumption.
GRAVITY_MPS2 = 9.8

Point = Tuple[float, float]


def as_point(value: Any) -> Optional[Point]:
    """Coerce a [x, y] / {"x":..,"y":..} value to a float point or None."""
    if value is None:
        return None
    if isinstance(value, dict):
        value = (value.get("x"), value.get("y"))
    try:
        seq = list(value)  # type: ignore[arg-type]
        if len(seq) < 2:
            return None
        return (float(seq[0]), float(seq[1]))
    except (TypeError, ValueError):
        return None


def estimate_arc(
    thrower: Sequence[float],
    explosion: Sequence[float],
    *,
    coordinate_space: str,
    launch_angle_deg: float = DEFAULT_LAUNCH_ANGLE_DEG,
    units_per_meter: Optional[float] = None,
    gravity_mps2: float = GRAVITY_MPS2,
    samples: int = 24,
) -> Optional[Dict[str, Any]]:
    """Estimate the thrower->explosion arc from two endpoints.

    Returns None when the endpoints coincide (zero range — there is no
    arc to draw and no honest way to invent one).

    The returned dict is the geometry/metadata core of a trajectory
    record; the caller adds identity/source fields and MUST keep
    ``estimated: True``.
    """
    start = as_point(thrower)
    end = as_point(explosion)
    if start is None or end is None:
        return None
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    distance = math.hypot(dx, dy)
    if distance <= 0:
        return None
    if samples < 2:
        samples = 2

    theta = math.radians(launch_angle_deg)
    # For a symmetric projectile arc, apex height above the ground track
    # is R * tan(theta) / 4 — pure geometry of the assumed parabola.
    apex = distance * math.tan(theta) / 4.0

    points = []
    for i in range(samples + 1):
        f = i / samples
        points.append(
            {
                "x": round(start[0] + dx * f, 3),
                "y": round(start[1] + dy * f, 3),
                # Parabola height at fraction f: peaks at apex mid-way.
                "h": round(4.0 * apex * f * (1.0 - f), 3),
            }
        )

    result: Dict[str, Any] = {
        "estimated": True,
        "method": METHOD,
        "confidence_basis": CONFIDENCE_BASIS,
        "coordinate_space": coordinate_space,
        "distance": round(distance, 3),
        "distance_units": (
            "meters" if units_per_meter else "coordinate_units"
        ),
        "launch_angle_deg": launch_angle_deg,
        "launch_angle_basis": LAUNCH_ANGLE_BASIS,
        "apex_height": round(apex, 3),
        "apex_units": (
            "meters" if units_per_meter else "coordinate_units"
        ),
        "arc_points": points,
        "flight_time_s": None,
        "launch_speed_mps": None,
    }

    if units_per_meter and units_per_meter > 0:
        range_m = distance / units_per_meter
        sin2 = math.sin(2.0 * theta)
        if sin2 > 0:
            v = math.sqrt(range_m * gravity_mps2 / sin2)
            result["distance"] = round(range_m, 3)
            result["apex_height"] = round(apex / units_per_meter, 3)
            result["launch_speed_mps"] = round(v, 3)
            result["flight_time_s"] = round(
                2.0 * v * math.sin(theta) / gravity_mps2, 3
            )
            result["gravity_mps2"] = gravity_mps2

    return result
