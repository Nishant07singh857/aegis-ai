# backend/app/services/observer_pilot/config.py
"""Phase 29 (W3) — Observer Auto-Pilot tap calibration + executor config.

This module owns the ONLY numbers the tap executor may use:

  * WHERE to tap on an observer phone (screen-fraction tap points for
    roster indices 0..3), and
  * HOW LONG verification may poll after a tap
    (``ExecutorConfig.verify_timeout_s`` / ``verify_poll_s``).

Tap points are FRACTIONS of the screen (0..1), never raw pixels in any
default or file: the same calibration must survive a phone swapping
resolution, and pixels are derived at tap time from the actual frame
shape (``TapPoint.to_pixels``).

HONEST LIMIT — the built-in "default" profile is UNCALIBRATED:
  The four default fractions are the centres of the four team-bar
  cards (``TeamStatusReader.DEFAULT_REGION`` in
  ``app.vision.detectors.hud``: x=0.015, y=0.800, w=0.270, h=0.170,
  split into 4 equal cards). Tapping a spectated teammate's card is
  the expected spectator-mode POV-switch gesture, so the card centres
  are the best available ESTIMATE — but no real BGMI spectator
  player-list frame has been measured in this environment. These
  points MUST be set per device in a scrim before production relies
  on them; until then ``TapCalibration.resolve()`` reports
  ``calibrated=False`` for them and the executor stamps that flag on
  every ``TapResult`` so the lie can never travel downstream.

Calibration file (optional):
  Env ``AEGIS_POV_CALIBRATION_PATH`` points at a JSON file::

      {
        "profiles": {
          "default":    [[fx, fy], [fx, fy], [fx, fy], [fx, fy]],
          "1920x1080":  [{"fx": 0.05, "fy": 0.88}, ...]
        },
        "per_serial": {"<adb-serial>": [[fx, fy], ...]},
        "calibrated_profiles": ["1920x1080", "<adb-serial>"]
      }

  ``calibrated_profiles`` lists the profile keys (resolution string,
  "default") or serials whose points were REALLY measured on a rig —
  it is the honesty ledger for ``resolve()``'s calibrated flag, not a
  decorative list. A missing or corrupt file yields the built-in
  defaults plus a warning log; it never crashes the pilot.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

import structlog

logger = structlog.get_logger("aegis.observer_pilot.config")

#: Env var naming the optional tap-calibration JSON file.
CALIBRATION_PATH_ENV = "AEGIS_POV_CALIBRATION_PATH"

#: Key of the fallback profile in ``TapCalibration.profiles``.
DEFAULT_PROFILE_KEY = "default"


@dataclass(frozen=True)
class TapPoint:
    """One tap target as fractions of the screen (0..1 on both axes).

    Fractions, never pixels: pixel coordinates are derived from the
    live frame shape at tap time by :meth:`to_pixels`.
    """

    fx: float
    fy: float

    def __post_init__(self) -> None:
        if not 0.0 <= float(self.fx) <= 1.0:
            raise ValueError(f"TapPoint fx must be in 0..1, got {self.fx}")
        if not 0.0 <= float(self.fy) <= 1.0:
            raise ValueError(f"TapPoint fy must be in 0..1, got {self.fy}")

    def to_pixels(self, width: int, height: int) -> Tuple[int, int]:
        """Pixel coordinates for a ``width`` x ``height`` screen.

        Truncating (not rounding) keeps a fraction's pixel inside the
        card it names; the result is clamped to the screen so an
        fx/fy of exactly 1.0 lands on the last pixel, never outside.
        """
        w, h = int(width), int(height)
        if w <= 0 or h <= 0:
            raise ValueError(f"invalid screen size {width}x{height}")
        x = max(0, min(w - 1, int(float(self.fx) * w)))
        y = max(0, min(h - 1, int(float(self.fy) * h)))
        return x, y


#: UNCALIBRATED best-estimate default tap points for roster 0..3:
#: the centres of the four team-bar cards. TeamStatusReader's bar
#: region is x=0.015, y=0.800, w=0.270, h=0.170 split into 4 equal
#: cards, so card i's centre is
#:   fx = 0.015 + (i + 0.5) * (0.270 / 4),  fy = 0.800 + 0.170 / 2.
#: These are ESTIMATES (see the module docstring) — a scrim must set
#: real per-device points before production trusts a tap.
DEFAULT_TAP_POINTS: Tuple[TapPoint, ...] = (
    TapPoint(0.04875, 0.885),
    TapPoint(0.11625, 0.885),
    TapPoint(0.18375, 0.885),
    TapPoint(0.25125, 0.885),
)


def _parse_points(raw: object) -> Optional[List[TapPoint]]:
    """Parse one profile's point list from calibration-file JSON.

    Accepts ``[fx, fy]`` pairs or ``{"fx": .., "fy": ..}`` dicts.
    Returns None when the value is not a usable non-empty list — the
    caller then keeps the built-in profile instead of a broken one.
    """
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    points: List[TapPoint] = []
    for item in raw:
        try:
            if isinstance(item, dict):
                points.append(
                    TapPoint(fx=float(item["fx"]), fy=float(item["fy"]))
                )
            else:
                fx, fy = item  # type: ignore[misc]
                points.append(TapPoint(fx=float(fx), fy=float(fy)))
        except (KeyError, TypeError, ValueError):
            return None
    return points


def _parse_profile_map(raw: object) -> Dict[str, List[TapPoint]]:
    """Parse a ``{key: [points]}`` map (profiles or per_serial)."""
    out: Dict[str, List[TapPoint]] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        points = _parse_points(value)
        if points:
            out[str(key)] = points
    return out


@dataclass
class TapCalibration:
    """Tap-point calibration: resolution profiles + per-device overrides.

    ``profiles`` is keyed by resolution string ``"WxH"`` plus a
    ``"default"`` profile used for any unlisted resolution.
    ``per_serial`` overrides either, keyed by ADB serial (one phone's
    measured layout beats the generic resolution profile for that
    phone). ``calibrated_profiles`` is the honesty ledger: the set of
    profile keys / serials whose points were really measured on a
    rig. The built-in default profile is NOT in it.
    """

    profiles: Dict[str, List[TapPoint]] = field(
        default_factory=lambda: {DEFAULT_PROFILE_KEY: list(DEFAULT_TAP_POINTS)}
    )
    per_serial: Dict[str, List[TapPoint]] = field(default_factory=dict)
    calibrated_profiles: Set[str] = field(default_factory=set)

    # ── resolution ───────────────────────────────────────────────

    def resolve(
        self, serial: Optional[str], width: int, height: int
    ) -> Tuple[List[TapPoint], bool]:
        """(tap points, calibrated?) for one device + frame size.

        Priority: per-serial override > ``"WxH"`` resolution profile
        > ``"default"`` profile > built-in defaults. ``calibrated``
        is True only when the source that supplied the points is
        named in ``calibrated_profiles`` (a serial, for a per-serial
        override; the profile key otherwise) — using points that
        exist is never, by itself, evidence they were calibrated.
        """
        if serial and serial in self.per_serial and self.per_serial[serial]:
            return list(self.per_serial[serial]), serial in self.calibrated_profiles
        key = f"{int(width)}x{int(height)}"
        if key in self.profiles and self.profiles[key]:
            return list(self.profiles[key]), key in self.calibrated_profiles
        if self.profiles.get(DEFAULT_PROFILE_KEY):
            return (
                list(self.profiles[DEFAULT_PROFILE_KEY]),
                DEFAULT_PROFILE_KEY in self.calibrated_profiles,
            )
        return list(DEFAULT_TAP_POINTS), False

    # ── loading ──────────────────────────────────────────────────

    @classmethod
    def defaults(cls) -> "TapCalibration":
        """Built-in calibration: default profile only, nothing calibrated."""
        return cls()

    @classmethod
    def load(cls, path: Optional[str] = None) -> "TapCalibration":
        """Calibration from the JSON file at ``path`` (or the
        ``AEGIS_POV_CALIBRATION_PATH`` env var when ``path`` is None).

        File entries are merged OVER the built-in defaults (a file
        ``"default"`` profile replaces the built-in one). A missing
        file, unreadable file, invalid JSON, or a top level that is
        not an object all yield the built-in defaults with a warning
        log — never an exception: a broken calibration file must not
        take the pilot down, and the defaults honestly report
        ``calibrated=False``.
        """
        effective = path if path is not None else os.getenv(CALIBRATION_PATH_ENV, "")
        effective = (effective or "").strip()
        if not effective:
            return cls.defaults()
        try:
            with open(effective, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            logger.warning(
                "pov_calibration_load_failed_using_defaults",
                path=effective,
                error=str(exc)[:200],
            )
            return cls.defaults()
        if not isinstance(data, dict):
            logger.warning(
                "pov_calibration_not_an_object_using_defaults", path=effective
            )
            return cls.defaults()

        calibration = cls.defaults()
        file_profiles = _parse_profile_map(data.get("profiles"))
        calibration.profiles.update(file_profiles)
        calibration.per_serial.update(_parse_profile_map(data.get("per_serial")))
        raw_calibrated = data.get("calibrated_profiles")
        if isinstance(raw_calibrated, (list, tuple, set)):
            calibration.calibrated_profiles = {str(k) for k in raw_calibrated}
        logger.info(
            "pov_calibration_loaded",
            path=effective,
            profiles=sorted(calibration.profiles),
            per_serial=sorted(calibration.per_serial),
            calibrated_profiles=sorted(calibration.calibrated_profiles),
        )
        return calibration


def load_tap_calibration(path: Optional[str] = None) -> TapCalibration:
    """Module-level loader (see :meth:`TapCalibration.load`)."""
    return TapCalibration.load(path)


#: Alias for callers that reach for the shorter name.
load_calibration = load_tap_calibration


@dataclass
class ExecutorConfig:
    """The tap executor's tunables, in ONE place (PilotConfig pattern).

    ``verify_timeout_s`` / ``verify_poll_s`` bound the post-tap
    verification window; ``calibration`` carries the tap points. When
    ``calibration`` is omitted it is loaded from
    ``AEGIS_POV_CALIBRATION_PATH`` (built-in defaults when no file is
    configured) — see :class:`TapCalibration`.
    """

    verify_timeout_s: float = 2.0
    verify_poll_s: float = 0.4
    calibration: Optional[TapCalibration] = None

    def __post_init__(self) -> None:
        if float(self.verify_timeout_s) <= 0:
            raise ValueError(
                f"verify_timeout_s must be > 0, got {self.verify_timeout_s}"
            )
        if float(self.verify_poll_s) <= 0:
            raise ValueError(f"verify_poll_s must be > 0, got {self.verify_poll_s}")
        self.verify_timeout_s = float(self.verify_timeout_s)
        self.verify_poll_s = float(self.verify_poll_s)
        if self.calibration is None:
            self.calibration = load_tap_calibration()


__all__ = [
    "CALIBRATION_PATH_ENV",
    "DEFAULT_PROFILE_KEY",
    "DEFAULT_TAP_POINTS",
    "ExecutorConfig",
    "TapCalibration",
    "TapPoint",
    "load_calibration",
    "load_tap_calibration",
]
