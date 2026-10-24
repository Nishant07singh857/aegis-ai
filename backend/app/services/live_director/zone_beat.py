# backend/app/services/live_director/zone_beat.py
"""Zone-reveal on-screen answer (Phase 31, audit item 4).

Pure decisions over a ``zone_pull`` snapshot (winners/losers already
produced by ``vision_intelligence.zone_state`` — this module computes
no geometry). The spec's "Hard Shift / 8-second forced map cut" and
"forced Map 7s" are REJECTED: both are below the Bible's 15–30s map
range and a forced mid-fight cut breaks grammar rule U9. The
grammar-true adapted form, decided here:

  * NO promoted fight active AND NOT final circle -> a 15s ``map_cam``
    beat (inside the Bible 15–30s range; the 30s cap is enforced by
    :func:`clamp_map_beat_duration` and by DirectorService's
    ``enforce_scene_holds``). The beat shows the winners/losers
    graphic alongside the map; it is a scene beat, never a cut to a
    team's feed.
  * A promoted fight IS active -> graphic only (winners/losers
    graphic), NEVER a cut. The fight on program is not interrupted.
  * Final circle -> graphic only as well: a graphic is not a camera
    switch, so it does not break the final-circle no-switching rule,
    but no map beat is taken (no switching means no scene beat).

This module never publishes, suggests, or cuts anything itself. The
coordinator wires the decision into service.py (see the HOOK NOTE in
the module docstring footer): full_auto takes the map beat through
the pending-beat pattern with hot-fight abort, semi_auto through
``propose_suggestion`` (scene ``map_cam``), manual logs it only.

Appendix B finding 6 (audit side-product): winners and losers today
both receive the same +10 zone-pressure in service.py's
``_on_intel_alert`` — the rich ranking flattens before the camera
decision. :func:`zone_pull_terms` differentiates them WITHOUT
changing any live score (this module is pure policy; service.py
applies it only via the hook): winners keep zone_pressure +10
(well-positioned for the new circle), losers move to precombat +10
(they must rotate — that is anticipation, not position). Magnitudes
are unchanged (10 each, reusing the existing ZONE_PRESSURE_PULL
scale), only the term differs, so no team's total can jump. One
overlap case exists by construction: when NO team is inside the new
circle, zone_state lists the single closest team as BOTH the
(relative) winner and a loser. That team gets the winner term only;
the loser term is suppressed so one signal is never double-counted.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

#: The zone-reveal map beat length (Bible 15–30s range). 15s is the
#: range floor and the audit's adapted value — never 7s or 8s.
MAP_BEAT_DURATION_S = 15.0
#: Bible map-cam floor / cap. Any requested duration is clamped into
#: this range by :func:`clamp_map_beat_duration`.
MAP_BEAT_MIN_S = 15.0
MAP_BEAT_MAX_S = 30.0

#: Existing zone-pull weight scale in service.py (ZONE_PRESSURE_PULL).
ZONE_PULL_WINNER_ZONE_PRESSURE = 10.0
#: Loser weight under the differentiated policy — same magnitude as
#: the winner weight, different term (see module docstring).
ZONE_PULL_LOSER_PRECOMBAT = 10.0


def clamp_map_beat_duration(requested: Any) -> float:
    """Clamp a requested map-beat duration into the Bible 15–30s range.

    Non-numeric / missing input -> the 15s default. 7s / 8s spec
    values clamp UP to 15s; anything over 30s clamps DOWN to 30s.
    """
    try:
        value = float(requested)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return MAP_BEAT_DURATION_S
    if value != value:  # NaN
        return MAP_BEAT_DURATION_S
    return min(MAP_BEAT_MAX_S, max(MAP_BEAT_MIN_S, value))


def _team_ids(raw: Any) -> List[str]:
    """Normalize a winners/losers list to team-id strings.

    The VI service emits plain team-id strings; service.py's
    ``_on_intel_alert`` also tolerates ``{"team_id": ...}`` dicts.
    Both shapes are accepted here; anything else is dropped, never
    guessed.
    """
    out: List[str] = []
    for item in raw or []:
        if isinstance(item, dict):
            team_id = item.get("team_id")
        else:
            team_id = item
        if team_id is None:
            continue
        text = str(team_id).strip()
        if text and text != "Unknown" and text not in out:
            out.append(text)
    return out


def decide_zone_beat(
    pull: Optional[Dict[str, Any]],
    *,
    fight_active: bool,
    is_final_circle: bool,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Decide the on-screen answer to one new-circle reveal.

    ``pull`` is a zone_pull snapshot (``winners`` / ``losers`` /
    ``phase`` / ``timestamp``). ``fight_active`` = any promoted fight
    is live; ``is_final_circle`` = the PhaseTracker phase is
    final_circle. Returns a decision dict with ``action`` one of
    ``"map_beat"`` / ``"graphic_only"`` / ``"none"``. ``action`` is
    never a team cut: even ``"map_beat"`` only names the ``map_cam``
    scene — the coordinator resolves the scene publish / suggestion.
    """
    if pull is None:
        return {
            "action": "none",
            "reason": "no_zone_pull",
            "scene": None,
            "duration_s": None,
            "graphic": {"winners": [], "losers": []},
            "cuts_to_team": False,
        }
    winners = _team_ids(pull.get("winners"))
    losers = _team_ids(pull.get("losers"))
    graphic = {"winners": winners, "losers": losers}
    base = {
        "pull_phase": pull.get("phase"),
        "pull_timestamp": pull.get("timestamp"),
        "graphic": graphic,
        "cuts_to_team": False,
    }
    if not winners and not losers:
        return {
            **base,
            "action": "none",
            "reason": "zone_pull_no_teams",
            "scene": None,
            "duration_s": None,
        }
    if fight_active:
        return {
            **base,
            "action": "graphic_only",
            "reason": "fight_active_graphic_only",
            "scene": None,
            "duration_s": None,
        }
    if is_final_circle:
        return {
            **base,
            "action": "graphic_only",
            "reason": "final_circle_graphic_only",
            "scene": None,
            "duration_s": None,
        }
    return {
        **base,
        "action": "map_beat",
        "reason": "no_fight_no_final_circle_map_beat",
        "scene": "map_cam",
        "duration_s": MAP_BEAT_DURATION_S,
    }


def zone_pull_terms(pull: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Differentiated scoring terms for a zone_pull (policy only).

    Winners -> ``zone_pressure`` +10; losers -> ``precombat`` +10;
    a team listed on both sides (the no-team-inside case) gets the
    winner term only. This function changes NO live score by itself:
    service.py's ``_on_intel_alert`` zone_pull branch must be changed
    by the coordinator hook to consume ``terms`` instead of giving
    both sides zone_pressure +10. Until then the output is a
    proposal, and live scoring stays exactly as it is.
    """
    if pull is None:
        return {"terms": [], "winners": [], "losers": [], "overlap_team_ids": []}
    winners = _team_ids(pull.get("winners"))
    losers = _team_ids(pull.get("losers"))
    overlap = [t for t in losers if t in winners]
    terms: List[Dict[str, Any]] = [
        {
            "team_id": team_id,
            "term": "zone_pressure",
            "points": ZONE_PULL_WINNER_ZONE_PRESSURE,
            "role": "winner",
        }
        for team_id in winners
    ]
    terms.extend(
        {
            "team_id": team_id,
            "term": "precombat",
            "points": ZONE_PULL_LOSER_PRECOMBAT,
            "role": "loser",
        }
        for team_id in losers
        if team_id not in winners
    )
    return {
        "terms": terms,
        "winners": winners,
        "losers": losers,
        "overlap_team_ids": overlap,
        "overlap_note": (
            "Team(s) listed as both winner and loser (no team inside "
            "the new circle) keep the winner term only; the loser "
            "term is suppressed so one signal is never double-counted."
            if overlap
            else None
        ),
    }


# ── HOOK NOTE (coordinator: service.py, do NOT edit this module) ──
# H2a — in LiveDirector._on_intel_alert, kind == "zone_pull" branch
#   (after the existing note_zone_pressure calls, or replacing them):
#     from app.services.live_director.zone_beat import decide_zone_beat
#     beat = decide_zone_beat(
#         data, fight_active=bool(self._fights),
#         is_final_circle=(self._phase() == "final_circle"))
#     # data here is the intel_alert payload: winners/losers are the
#     # team-id lists VI emitted, which _team_ids() accepts as-is.
# H2b — map_beat actuation (BEWARE: do not reuse _service_pending_beat
#   completion verbatim — it cuts to pending["slot"] after the beat,
#   which is wrong for a zone beat: nothing follows a zone beat but
#   normal evaluation):
#     * semi_auto: await self._director.propose_suggestion(
#         suggestion_type="scene",
#         payload={"scene": "map_cam"},
#         reason=("New circle: 15s map beat. Winners: "
#                 + ", ".join(beat["graphic"]["winners"])
#                 + "; Losers: "
#                 + ", ".join(beat["graphic"]["losers"]) + "."),
#         confidence=0.8, created_by="live_director")
#       NOTE: the "scene" suggestion payload validator keeps ONLY
#       scene/active_camera/team_id — a "graphic" payload key would
#       be silently stripped, so the winners/losers graphic rides in
#       the reason + the decision-log entry. Rendering a dedicated
#       graphic template is frontend work outside this worker's
#       files; this module supplies the graphic DATA (beat["graphic"])
#       and the log carries it verbatim.
#     * full_auto/clone (not grabbed): register a DISTINCT pending
#       entry, e.g. self._pending_zone_beat = {"scene": "map_cam",
#       "started_at": now, "completes_at": now + 15.0,
#       "graphic": beat["graphic"]}, publish scene="map_cam" via
#       system_publish, and service it at the TOP of _evaluate (same
#       position as the _pending_beat block): abort instantly when
#       any promoted fight appears (hot check over self._fights);
#       on expiry, clear it and FALL THROUGH to normal evaluation
#       (no cut). Manual: log the decision only, no publish/suggest.
# H2c — differentiated terms (Appendix B finding 6, OPTIONAL but
#   recommended; changes the zone_pull branch's scoring, so the
#   coordinator owns it and re-runs the live-director suites):
#     from app.services.live_director.zone_beat import zone_pull_terms
#     for term in zone_pull_terms(data)["terms"]:
#         if term["term"] == "zone_pressure":
#             await note_zone_pressure(term["team_id"],
#                                      term["points"])
#         else:
#             await note_precombat(term["team_id"], term["points"])
#             await note_convergence(term["team_id"])
#   (note_precombat / note_convergence are the branch's existing
#   local helpers. Winners keep +10 zone_pressure; losers move from
#   +10 zone_pressure to +10 precombat — same magnitude, new term.)
