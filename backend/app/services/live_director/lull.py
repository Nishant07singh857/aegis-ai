# backend/app/services/live_director/lull.py
"""Lull loop (Phase 31, audit item 7) — grammar-true caster loop.

When the director would otherwise idle in loot/early phases, this
state machine cycles semi_auto SUGGESTIONS — map -> standings ->
top-team POV — with legs of 15–30s each. The spec's 12–15s legs are
REJECTED: 12s is below the Bible's 15s map-cam floor, so any
configured leg duration is clamped into [15, 30] by
:func:`clamp_leg_duration` (12s becomes 15s, never 12s).

Hard rules, all enforced here (the coordinator only passes inputs):

  * Gates: the loop runs ONLY in PhaseTracker phases ``loot`` and
    ``early``. Any other phase -> no suggestion, loop state reset.
  * Abort: ANY fight signal (a promoted fight) OR any PRE_COMBAT
    candidate aborts the loop immediately — the in-progress leg is
    dropped and the cycle restarts at the map leg next time. A fight
    never waits for a leg to finish.
  * Never an auto-cut: :meth:`LullLoop.tick` returns a SUGGESTION
    dict (``auto_cut: False``, ``mode: "suggestion_only"``). It never
    publishes, never sets a camera, never touches program. The
    coordinator emits it via DirectorService ``propose_suggestion``
    (which itself is a semi_auto-only no-op elsewhere) — in full_auto
    / manual the loop must be reset, not published (see HOOK NOTE).
  * Missing input -> skipped leg, never invented content: the
    top-team POV leg is skipped when no top team is known; the map
    and standings legs need no team at all.
  * Scenes are DirectorService's real scene names (``map_cam``,
    ``standings``, ``main`` + ``active_camera`` for the POV leg) —
    no scene is invented.

The loop is deliberately dumb: it knows nothing about fights, slots
or modes beyond what the caller passes per tick. Slot resolution for
the POV leg (team_id -> slot) stays in service.py, which owns the
attribution maps; a ``None`` slot is carried through honestly and
the coordinator skips the suggestion if the team cannot be resolved
to a slot.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

#: Phases in which the lull loop may run (PhaseTracker vocabulary).
LULL_PHASES = ("loot", "early")
#: Bible map-cam floor / cap, applied to every leg (not just map).
LEG_MIN_S = 15.0
LEG_MAX_S = 30.0
#: Default leg length: mid-range of the Bible window. A design choice,
#: not a measured number — the window, not the default, is the rule.
DEFAULT_LEG_S = 20.0

#: Leg cycle, in order. ``needs_top_team`` legs are skipped when the
#: caller knows no top team.
LEG_CYCLE = ("map", "standings", "top_team_pov")


def clamp_leg_duration(requested: Any) -> float:
    """Clamp a leg duration into the Bible 15–30s range.

    Non-numeric / missing input -> the 20s default. The spec's
    12–15s legs clamp UP: 12s becomes 15s, never 12s. Over 30s clamps
    DOWN to 30s.
    """
    try:
        value = float(requested)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_LEG_S
    if value != value:  # NaN
        return DEFAULT_LEG_S
    return min(LEG_MAX_S, max(LEG_MIN_S, value))


class LullLoop:
    """Cycling lull suggestions for idle loot/early phases."""

    def __init__(self, leg_duration_s: Any = DEFAULT_LEG_S) -> None:
        self.leg_duration_s = clamp_leg_duration(leg_duration_s)
        self._leg_index: Optional[int] = None
        self._leg_started_at: Optional[float] = None
        self._current_leg: Optional[str] = None

    # ── state ────────────────────────────────────────────────────

    def reset(self) -> None:
        """Drop the in-progress leg; the next lull starts at map."""
        self._leg_index = None
        self._leg_started_at = None
        self._current_leg = None

    def state(self) -> Dict[str, Any]:
        return {
            "current_leg": self._current_leg,
            "leg_index": self._leg_index,
            "leg_started_at": self._leg_started_at,
            "leg_duration_s": self.leg_duration_s,
        }

    # ── tick ─────────────────────────────────────────────────────

    def tick(
        self,
        now: float,
        *,
        phase: str,
        fight_active: bool,
        precombat_active: bool,
        top_team_id: Optional[str] = None,
        top_team_slot: Optional[int] = None,
    ) -> Dict[str, Any]:
        """One lull evaluation. Returns exactly one of:

        * ``{"action": "suggest", ...}`` — a new leg starts NOW; the
          caller emits it as a semi_auto suggestion (never a cut).
        * ``{"action": "none", "reason": "leg_in_progress" | ...}`` —
          nothing new to suggest (leg still running, wrong phase,
          or no lull condition).
        * ``{"action": "abort", ...}`` — a fight/precombat signal
          killed the loop; the caller drops any pending lull
          suggestion and returns to normal direction immediately.
        """
        if (phase or "").strip().lower() not in LULL_PHASES:
            self.reset()
            return {
                "action": "none",
                "reason": "phase_not_lull",
                "phase": phase,
            }
        if fight_active or precombat_active:
            aborted_leg = self._current_leg
            self.reset()
            return {
                "action": "abort",
                "reason": (
                    "fight_active" if fight_active else "precombat_active"
                ),
                "aborted_leg": aborted_leg,
            }
        if (
            self._current_leg is not None
            and self._leg_started_at is not None
            and now < self._leg_started_at + self.leg_duration_s
        ):
            return {
                "action": "none",
                "reason": "leg_in_progress",
                "leg": self._current_leg,
                "remaining_s": round(
                    self._leg_started_at + self.leg_duration_s - now, 1
                ),
            }
        # Advance (skipping the POV leg when no top team is known).
        start = 0 if self._leg_index is None else self._leg_index + 1
        chosen: Optional[str] = None
        chosen_index: Optional[int] = None
        for step in range(len(LEG_CYCLE)):
            idx = (start + step) % len(LEG_CYCLE)
            leg = LEG_CYCLE[idx]
            if leg == "top_team_pov" and not top_team_id:
                continue
            chosen, chosen_index = leg, idx
            break
        if chosen is None:  # unreachable (map/standings need no team)
            self.reset()
            return {"action": "none", "reason": "no_suggestable_leg"}
        self._leg_index = chosen_index
        self._current_leg = chosen
        self._leg_started_at = float(now)
        return self._suggestion(chosen, top_team_id, top_team_slot)

    def _suggestion(
        self,
        leg: str,
        top_team_id: Optional[str],
        top_team_slot: Optional[int],
    ) -> Dict[str, Any]:
        base = {
            "action": "suggest",
            "kind": "lull_loop",
            "leg": leg,
            "duration_s": self.leg_duration_s,
            "auto_cut": False,
            "mode": "suggestion_only",
            "suggestion_type": "scene",
        }
        if leg == "map":
            return {
                **base,
                "scene": "map_cam",
                "active_camera": None,
                "team_id": None,
                "reason": (
                    "Lull loop (loot/early, no fight/precombat): map leg "
                    f"for {self.leg_duration_s:g}s."
                ),
            }
        if leg == "standings":
            return {
                **base,
                "scene": "standings",
                "active_camera": None,
                "team_id": None,
                "reason": (
                    "Lull loop (loot/early, no fight/precombat): "
                    f"standings leg for {self.leg_duration_s:g}s."
                ),
            }
        # top_team_pov
        return {
            **base,
            "scene": "main",
            "active_camera": top_team_slot,
            "team_id": top_team_id,
            "slot_unresolved": top_team_slot is None,
            "reason": (
                "Lull loop (loot/early, no fight/precombat): top-team "
                f"POV leg ({top_team_id}) for {self.leg_duration_s:g}s."
            ),
        }


# ── HOOK NOTE (coordinator: service.py, do NOT edit this module) ──
# H3 — in LiveDirector._evaluate, at the idle branch that today reads
#   `if best is None: return None  # idle: no candidate, ...`:
#     * The loop instance lives on the director, e.g. in __init__:
#         self._lull_loop = LullLoop()   # 20s legs, clamped [15, 30]
#     * At the idle branch, BEFORE `return None`:
#         if mode != "semi_auto":
#             self._lull_loop.reset()
#             return None  # full_auto/manual: lull never auto-cuts,
#                          # and propose_suggestion is semi_auto-only
#                          # anyway, so nothing is emitted here.
#         top_team = None; top_slot = None
#         # Top team = rank-1 row of self._story_standings (rebuilt
#         # this tick by _refresh_storyline_context); top_slot from
#         # _team_to_slot. Either may be None -> POV leg is skipped.
#         lull = self._lull_loop.tick(
#             now, phase=phase,
#             fight_active=bool(self._fights),
#             precombat_active=bool(self.engagement.precombat_teams()),
#             top_team_id=top_team, top_team_slot=top_slot)
#         if lull["action"] == "suggest":
#             if lull["leg"] == "top_team_pov" and lull.get(
#                     "active_camera") is None:
#                 return None  # slot unresolvable -> suggest nothing
#             suggestion = await self._director.propose_suggestion(
#                 suggestion_type="scene",
#                 payload={"scene": lull["scene"],
#                          "active_camera": lull["active_camera"],
#                          "team_id": lull["team_id"]},
#                 reason=lull["reason"], confidence=0.6,
#                 created_by="live_director")
#             # suggestion None (duplicate/no-op) -> fall through to
#             # the existing idle return; log the lull tick either way
#             # via _log_decision with outcome "lull_suggestion" /
#             # "lull_hold" so the loop is visible in the decision log.
#         return None  # "none" and "abort" both mean: stay idle here;
#                      # "abort" additionally means any queued lull
#                      # suggestion must NOT be approved blindly —
#                      # propose_suggestion dedup does not retract an
#                      # already-queued one, so ops should treat a
#                      # fight starting as superseding it (the normal
#                      # fight decision path already outranks it).
#   NOTE on abort semantics, stated honestly: tick-level abort stops
#   NEW lull suggestions the moment a fight/precombat signal exists.
#   Retracting an already-queued suggestion is DirectorService's
#   queue behaviour, not this module's — the hook must not claim the
#   loop retracts queue entries it did not create.
