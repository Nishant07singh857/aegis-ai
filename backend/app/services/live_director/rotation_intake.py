# backend/app/services/live_director/rotation_intake.py
"""Phase 31 (Worker A) — rotation / potential-fight intake translation.

WHY THIS MODULE EXISTS
----------------------
The live director already knows what to do with a rotation reading —
``LiveDirector.note_rotation()`` feeds the engagement tracker, which
derives ROTATING, or PRE_COMBAT when the rotation runs into a POI /
compound another team already occupies, and the director's own
evaluation then pre-positions PVW on a PRE_COMBAT team. But nothing in
production ever called ``note_rotation()`` (or ``note_potential_fight()``
for the fight detector's not-yet-promoted PotentialFight readings):
vision intelligence published ``rotation_event`` bus events that no
director subscriber consumed — ``LIVE_DIRECTOR_EVENT_TYPES`` does not
include ``rotation_event`` — and the detector's potential fights never
left the detector at all.

This module is the translation layer between those producers and the
director, WITHOUT editing ``live_director/service.py``:

* :func:`translate_rotation_event` — pure: a ``rotation_event`` bus
  payload -> the exact ``note_rotation()`` call spec, or ``None`` when
  the event names no team (never guessed).
* :func:`translate_potential_fight` — pure: a ``potential_fight`` bus
  payload -> the exact ``note_potential_fight()`` call spec.
* :func:`build_rotation_bridge` — the production caller: an async bus
  callback (subscribed in ``app/main.py`` lifespan) that feeds both
  event types straight into a LiveDirector instance. The bridge never
  raises — a bad event is counted and dropped, never allowed to break
  bus dispatch for other subscribers.

SLAM ANTICIPATION — WHAT THIS DOES AND DOES NOT DO
--------------------------------------------------
On a PRE_COMBAT rotation intake the director's existing evaluation
pre-positions PVW on that team. That is ALL that happens here:

* PVW pre-position ONLY. An intel-only / PRE_COMBAT candidate can never
  blind-cut PGM: the director scores it against the on-air incumbent
  base (``ON_AIR_BASE``) under the normal margins, so a rotation alone
  holds preview while PGM stays put. When the crash lands and the fight
  promotes, the normal hard cut applies (fight promotion path, not this
  module).
* NO free-cam / overhead / drone claim: this rig has no movable camera.
  The pre-positioned feed is an observer phone's follow-spectate feed.
* NO fabricated lead time. A PRE_COMBAT lifetime (TTL) is a lifetime,
  not a "3 seconds before the crash" claim — no such number appears
  anywhere here.
* NO fabricated units. Speeds stay ``px/s`` and distances stay ``px``.
  Metres / km/h appear only with a calibrated
  ``AEGIS_VISION_MINIMAP_PX_PER_METER`` scale, which this module never
  invents — it simply does not carry metre or km/h fields at all.

OCCUPANCY SEMANTICS (honest label)
----------------------------------
``occupied_by`` means: another team's latest known position context
resolves to the SAME named POI area as the rotating team's current
area (normalized map coordinates only — minimap-region pixels can
never name a POI, so occupancy is never derived from them). That is
shared-area convergence, recorded on the event as
``occupancy_basis="shared_current_area_poi"``. It is NOT a measured
destination: the rotation's destination is not observed, only the
team's current area. The engagement tracker treats ``occupied_by`` as
its "rotation into an occupied POI" convergence input; the basis
string travels on the bus event so operators can audit what the
claim actually was.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import structlog

logger = structlog.get_logger("aegis.live_director.rotation_intake")

#: Bus topics the bridge consumes (subscribed in app/main.py).
BRIDGE_EVENT_TYPES = ["rotation_event", "potential_fight"]

#: Subscriber id used for the bridge subscription (main.py lifespan).
BRIDGE_SUBSCRIBER_ID = "phase31_rotation_intake"


def _clean_team(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text != "Unknown" else None


def _clean_poi(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _as_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def translate_rotation_event(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Translate one ``rotation_event`` payload into a director intake.

    Returns ``None`` when the event names no team, or when it is not a
    rotation signal at all. Never invents occupancy, POI, mode, speed
    or bearing: every field is passed through only when the event
    actually carries it.

    Returned spec (the ``note_rotation()`` call arguments plus audit
    context)::

        {
            "team_id": ...,            # required
            "active": bool,            # False only for an ended rotation
            "occupied_by": ...|None,   # never the team itself
            "poi": ...|None,           # rotating team's current area POI
            "rotation_mode": ...|None, # "vehicle" / "on_foot" (inferred)
            "bearing_deg": ...|None,
            "speed_px_s": ...|None,    # px/s ONLY — never km/h
            "precombat_expected": bool,# True iff occupied_by is set
                                        # (engagement's own rule)
            "slam_preposition": {       # PVW-only intent record
                "action": "pvw_preposition_only",
                "never_pgm_cut": True,
                "precombat": bool,
            },
        }
    """
    if not isinstance(event, dict):
        return None
    team_id = _clean_team(event.get("team_id"))
    if team_id is None:
        return None
    # An ended rotation deactivates; anything else is an active start.
    # The vision layer emits starts today; "rotation_ended" / explicit
    # active=False are accepted so a future close event deactivates
    # instead of re-arming.
    if event.get("kind") == "rotation_ended" or event.get("active") is False:
        active = False
    else:
        active = True
    occupied_by = _clean_team(event.get("occupied_by"))
    if occupied_by == team_id:
        occupied_by = None
    poi = _clean_poi(event.get("poi") or event.get("area_poi"))
    mode = event.get("rotation_mode")
    mode = str(mode).strip() if isinstance(mode, str) and mode.strip() else None
    if mode not in ("vehicle", "on_foot"):
        mode = None
    precombat = occupied_by is not None
    return {
        "team_id": team_id,
        "active": active,
        "occupied_by": occupied_by,
        "poi": poi,
        "rotation_mode": mode,
        "bearing_deg": _as_float(event.get("bearing_deg")),
        "speed_px_s": _as_float(event.get("speed_px_s")),
        "occupancy_basis": (
            event.get("occupancy_basis") if precombat else None
        ),
        "precombat_expected": precombat,
        "slam_preposition": {
            "action": "pvw_preposition_only",
            "never_pgm_cut": True,
            "precombat": precombat,
            # No lead-time claim: PRE_COMBAT TTL is a lifetime, not a
            # "seconds before the crash" prediction.
            "lead_time_claim": None,
        },
    }


def translate_potential_fight(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Translate one ``potential_fight`` payload into a director intake.

    Returns ``None`` when no team is named. Confidence is clamped to
    0..1 and passed through untouched otherwise — the director's own
    ``precombat_confidence_enter`` threshold decides whether the
    reading becomes PRE_COMBAT; below-threshold readings are recorded
    as weak signals by the engagement tracker, never dropped here.
    """
    if not isinstance(event, dict):
        return None
    teams = [
        t
        for t in (
            _clean_team(raw)
            for raw in (event.get("teams") or event.get("teams_involved") or [])
        )
        if t is not None
    ]
    if not teams:
        return None
    conf = _as_float(event.get("confidence"))
    if conf is None:
        conf = _as_float(event.get("fight_probability"))
    if conf is None:
        return None
    conf = max(0.0, min(1.0, conf))
    signals = event.get("signals") or event.get("signal_types") or []
    if not isinstance(signals, list):
        signals = [signals]
    return {
        "team_ids": teams,
        "confidence": conf,
        "signals": [str(s) for s in signals if s is not None],
        "fight_id": event.get("fight_id"),
    }


async def handle_rotation_record(
    event_type: str, data: Dict[str, Any], live_director: Any
) -> Optional[Dict[str, Any]]:
    """Feed one bus event into the director. Returns the intake spec
    applied, or ``None`` when the event carried nothing usable. Never
    raises: intake failures are logged and counted by the caller."""
    if event_type == "rotation_event":
        spec = translate_rotation_event(data)
        if spec is None:
            return None
        await live_director.note_rotation(
            spec["team_id"],
            active=spec["active"],
            occupied_by=spec["occupied_by"],
            poi=spec["poi"],
        )
        return spec
    if event_type == "potential_fight":
        spec = translate_potential_fight(data)
        if spec is None:
            return None
        await live_director.note_potential_fight(
            spec["team_ids"],
            spec["confidence"],
            signals=spec["signals"] or None,
        )
        return spec
    return None


def build_rotation_bridge(live_director: Any) -> Callable:
    """Build the bus callback that wires rotation/potential-fight
    events into ``live_director`` (subscribed in ``app/main.py``).

    The returned callback accepts an :class:`EventRecord` (the bus
    callback contract) and exposes a ``stats`` dict
    (``received`` / ``forwarded`` / ``dropped`` / ``errors``) for
    operator visibility. It never raises.
    """
    stats: Dict[str, int] = {
        "received": 0,
        "forwarded": 0,
        "dropped": 0,
        "errors": 0,
    }

    async def _bridge(record: Any) -> None:
        stats["received"] += 1
        try:
            event = getattr(record, "event", None)
            event_type = getattr(event, "event_type", "") if event else ""
            data = getattr(event, "data", None) if event else None
            if not isinstance(data, dict):
                stats["dropped"] += 1
                return
            applied = await handle_rotation_record(
                event_type, data, live_director
            )
            if applied is None:
                stats["dropped"] += 1
            else:
                stats["forwarded"] += 1
                logger.info(
                    "rotation_intake_forwarded",
                    event_type=event_type,
                    team_id=applied.get("team_id")
                    or applied.get("team_ids"),
                    precombat=applied.get("precombat_expected"),
                )
        except Exception as exc:  # never break bus dispatch
            stats["errors"] += 1
            logger.error("rotation_intake_error", error=str(exc)[:200])

    _bridge.stats = stats  # type: ignore[attr-defined]
    return _bridge
