# backend/app/services/director_learning/shadow.py
"""Manual-mode shadow reconciliation (Phase 27, W1).

In MANUAL mode the LiveDirector still evaluates every event and logs a
``hold`` / ``manual_mode_no_write`` decision — that logged entry IS the
AI's shadow decision. This module compares those shadow entries against
the human director's REAL output writes (observed via DirectorService
write sinks — see ``journal.py``) and derives divergences:

* ``human_switched_ai_held`` — the human cut to target X, but the
  latest shadow evaluation before the cut would NOT have cut to X:
  either its challenger was a different slot, or the challenger's
  margin would not have cleared the margin full_auto requires.
* ``ai_wanted_human_stayed`` — a shadow challenger WOULD have won
  under full_auto rules, and no human switch followed within 15 s.

Human vs system writes (VERIFIED in ``director_service.py``)
------------------------------------------------------------
The distinguisher is the WRITE PATH, not the ``updated_by`` string
(operator usernames are arbitrary):

* HUMAN: ``set_camera`` / ``set_scene`` — both gated by
  ``_require_manual()`` (they raise outside MANUAL mode), plus
  ``approve_suggestion`` (a human decision applying a payload).
* SYSTEM: ``system_publish`` — the AI path; it is a no-op outside
  FULL_AUTO and carries ``updated_by="live_director"`` in production.

DirectorService tags every write-sink event with its ``source`` method
name; :func:`classify_write` maps that to human/system. Preview
updates, mode flips, graphics and overlay writes never reach the sink
(the sink fires only on real scene/camera output changes).

Full-auto margin recomputation
------------------------------
Manual shadow entries carry ``margin_required=None`` (rule
``manual_mode_no_write``), so the margin full_auto WOULD have required
is recomputed from the entry's own evidence, mirroring the ladder in
``LiveDirector._evaluate`` in its priority order, using only facts the
logged entry actually contains:

* challenger candidate breakdown ``clutch > 0``  -> 5
  (``margin_clutch_5``; the breakdown's clutch term is CLUTCH_BONUS
  exactly when the challenger's fight ``is_clutch``);
* incumbent breakdown ``staleness_penalty < 0``  -> 10
  (``margin_stale_10``; the penalty is negative exactly when the
  incumbent was stale — ``age > stale_after`` in ``_incumbent_score``);
* otherwise -> 15 (``margin_default_15``).

Two grammar facts are NOT recoverable from a logged entry (the fight
-hold window needs the scene age; block rules need live fight state),
so the recomputation is deliberately CONSERVATIVE — it can only
under-claim AI intent, never over-claim: in ``final_circle``, when the
incumbent candidate has an active fight the challenger is not part of,
full_auto would have blocked the cut outright
(``final_circle_lock_until_fight_resolves``), so the required margin
is infinity and no ``ai_wanted`` divergence is raised. The detail
payload records ``hold_window_recomputable: False`` so W2/W3 know the
fight-hold step of the ladder could not be replayed from the entry.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Tuple

#: Write-sink ``source`` values that are human output writes.
HUMAN_WRITE_SOURCES = ("set_camera", "set_scene", "approve_suggestion")
#: Write-sink ``source`` values that are system/AI output writes.
SYSTEM_WRITE_SOURCES = ("system_publish",)

#: Seconds after a winning shadow entry during which a human switch
#: still counts as "the human followed" (no ai_wanted divergence).
AI_WANTED_WINDOW_S = 15.0

# The full_auto margin ladder (mirrors live_director.service constants:
# MARGIN_CLUTCH_CHALLENGER / MARGIN_STALE_INCUMBENT / MARGIN_DEFAULT).
# Phase 27 (W3): the knob provider is now the source for these values,
# resolved with learned=False — full_auto is the FACTORY profile
# forever, so the shadow comparison always uses registry defaults
# (identical numbers), even when a learned profile exists for clone.
MARGIN_CLUTCH = 5.0
MARGIN_STALE = 10.0
MARGIN_DEFAULT = 15.0


def _ladder() -> Tuple[float, float, float]:
    """(clutch, stale, default) from the provider's factory profile."""
    try:
        from .knobs import get_provider

        provider = get_provider()
        if provider is not None:
            return (
                provider.effective("margin_clutch", learned=False),
                provider.effective("margin_stale_incumbent", learned=False),
                provider.effective("margin_default", learned=False),
            )
    except Exception:  # pragma: no cover - defensive
        pass
    return MARGIN_CLUTCH, MARGIN_STALE, MARGIN_DEFAULT


def classify_write(event: Dict[str, Any]) -> str:
    """``"human"`` | ``"system"`` | ``"other"`` for a write-sink event."""
    source = event.get("source")
    if source in HUMAN_WRITE_SOURCES:
        return "human"
    if source in SYSTEM_WRITE_SOURCES:
        return "system"
    return "other"


def _candidate_for(entry: Dict[str, Any], team_id: Optional[str]) -> Dict[str, Any]:
    for cand in entry.get("candidates") or []:
        if cand.get("team_id") == team_id:
            return cand
    return {}


def full_auto_margin_required(entry: Dict[str, Any]) -> Tuple[float, str]:
    """The margin full_auto would have required for this shadow entry.

    Returns ``(margin_required, rule)`` recomputed ONLY from facts in
    the logged entry (see module docstring). ``math.inf`` + the block
    rule when full_auto would have blocked the cut outright.
    """
    challenger = entry.get("challenger") or {}
    incumbent = entry.get("incumbent") or {}
    phase = entry.get("phase")

    # Conservative block replay: final-circle lock. The incumbent's
    # candidate row carries its fight_id; the challenger's fight is in
    # the challenger dict. Different (or no) challenger fight while the
    # incumbent is fighting in the final circle -> blocked in full_auto.
    if phase == "final_circle":
        inc_cand = _candidate_for(entry, incumbent.get("team_id"))
        inc_fight = inc_cand.get("fight_id")
        if inc_fight and challenger.get("fight_id") != inc_fight:
            return math.inf, "final_circle_lock_until_fight_resolves"

    margin_clutch, margin_stale, margin_default = _ladder()
    chal_cand = _candidate_for(entry, challenger.get("team_id"))
    breakdown = chal_cand.get("breakdown") or {}
    try:
        clutch = float(breakdown.get("clutch") or 0.0)
    except (TypeError, ValueError):
        clutch = 0.0
    if clutch > 0:
        return margin_clutch, "margin_clutch_5"

    inc_breakdown = incumbent.get("breakdown") or {}
    try:
        staleness = float(inc_breakdown.get("staleness_penalty") or 0.0)
    except (TypeError, ValueError):
        staleness = 0.0
    if staleness < 0:
        return margin_stale, "margin_stale_10"

    return margin_default, "margin_default_15"


def shadow_would_switch(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Would full_auto have cut to this entry's challenger?

    Returns {would_switch, margin_required, margin_actual, rule} — the
    margin facts are the entry's own ``margin_actual`` vs the
    recomputed requirement. A missing margin means NO (never invented).
    """
    required, rule = full_auto_margin_required(entry)
    margin_actual = entry.get("margin_actual")
    would = (
        margin_actual is not None
        and required != math.inf
        and float(margin_actual) >= required
    )
    return {
        "would_switch": bool(would),
        "margin_required": None if required == math.inf else required,
        "margin_actual": margin_actual,
        "rule": rule,
    }


class ShadowReconciler:
    """Stateful divergence detector over journal rings.

    Pure computation + dedupe state; persistence is injected as an
    async callable ``persist(divergence_dict)`` by the journal, which
    owns the DB. Ring records are the journal's dicts:
    ``{"decision_id", "ts_epoch", "entry"}``; human writes are the
    journal's observed write records ``{"ts_epoch", "event"}``.
    """

    def __init__(self, persist: Callable[[Dict[str, Any]], Any]) -> None:
        self._persist = persist
        #: Write-event seqs already examined (one divergence per switch).
        self._seen_write_seqs: set = set()
        #: Shadow decision ids already checked for ai_wanted.
        self._checked_shadow_ids: set = set()

    # ── human_switched_ai_held ───────────────────────────────────

    def divergence_for_human_write(
        self,
        write: Dict[str, Any],
        ring: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Divergence dict for one human write, or None.

        Compares against the LATEST manual-mode shadow entry logged
        before the write. No shadow entry -> None (nothing to compare;
        never fabricated). AI would have cut to the same slot with a
        clearing margin -> None (agreement, not divergence).
        """
        seq = write.get("seq")
        if seq is not None:
            if seq in self._seen_write_seqs:
                return None
            self._seen_write_seqs.add(seq)
        event = write.get("event") or {}
        write_ts = float(write.get("ts_epoch") or 0.0)

        shadow = None
        for record in ring:
            entry = record.get("entry") or {}
            if entry.get("mode") != "manual":
                continue
            if float(record.get("ts_epoch") or 0.0) <= write_ts:
                if shadow is None or record["ts_epoch"] >= shadow["ts_epoch"]:
                    shadow = record
        if shadow is None:
            return None

        entry = shadow["entry"]
        challenger = entry.get("challenger") or {}
        verdict = shadow_would_switch(entry)
        target_slot = event.get("active_camera")
        ai_would_go_there = bool(
            verdict["would_switch"]
            and target_slot is not None
            and challenger.get("slot") == target_slot
        )
        if ai_would_go_there:
            return None
        return {
            "kind": "human_switched_ai_held",
            "phase": entry.get("phase"),
            "human_action": {
                "source": event.get("source"),
                "scene": event.get("scene"),
                "active_camera": event.get("active_camera"),
                "updated_by": event.get("updated_by"),
            },
            "ai_candidate": {
                "team_id": challenger.get("team_id"),
                "slot": challenger.get("slot"),
                "score": challenger.get("score"),
                "fight_id": challenger.get("fight_id"),
            },
            "shadow_decision_id": shadow.get("decision_id"),
            "detail": {
                "shadow_outcome": entry.get("outcome"),
                "shadow_rule": entry.get("rule"),
                "would_switch": verdict["would_switch"],
                "margin_required_recomputed": verdict["margin_required"],
                "margin_actual": verdict["margin_actual"],
                "margin_rule_recomputed": verdict["rule"],
                "hold_window_recomputable": False,
                "shadow_age_s": round(write_ts - float(shadow["ts_epoch"]), 3),
            },
        }

    # ── ai_wanted_human_stayed ───────────────────────────────────

    def pending_ai_wanted(
        self,
        ring: List[Dict[str, Any]],
        human_writes: List[Dict[str, Any]],
        now: float,
    ) -> List[Dict[str, Any]]:
        """New ai_wanted divergences whose 15 s window has fully elapsed.

        Each shadow entry is checked exactly once (dedupe by decision
        id). A human switch — to ANY target — inside the window means
        the human acted, so no divergence (a switch to a different
        target is captured by the human_switched path instead).
        """
        out: List[Dict[str, Any]] = []
        for record in ring:
            decision_id = record.get("decision_id")
            if decision_id is None or decision_id in self._checked_shadow_ids:
                continue
            entry = record.get("entry") or {}
            if entry.get("mode") != "manual":
                self._checked_shadow_ids.add(decision_id)
                continue
            ts = float(record.get("ts_epoch") or 0.0)
            if now - ts < AI_WANTED_WINDOW_S:
                continue  # window still open; check on a later pass
            self._checked_shadow_ids.add(decision_id)
            verdict = shadow_would_switch(entry)
            if not verdict["would_switch"]:
                continue
            followed = any(
                ts < float(w.get("ts_epoch") or 0.0) <= ts + AI_WANTED_WINDOW_S
                for w in human_writes
            )
            if followed:
                continue
            challenger = entry.get("challenger") or {}
            out.append(
                {
                    "kind": "ai_wanted_human_stayed",
                    "phase": entry.get("phase"),
                    "human_action": None,
                    "ai_candidate": {
                        "team_id": challenger.get("team_id"),
                        "slot": challenger.get("slot"),
                        "score": challenger.get("score"),
                        "fight_id": challenger.get("fight_id"),
                    },
                    "shadow_decision_id": decision_id,
                    "detail": {
                        "shadow_outcome": entry.get("outcome"),
                        "shadow_rule": entry.get("rule"),
                        "would_switch": True,
                        "margin_required_recomputed": verdict["margin_required"],
                        "margin_actual": verdict["margin_actual"],
                        "margin_rule_recomputed": verdict["rule"],
                        "hold_window_recomputable": False,
                        "window_s": AI_WANTED_WINDOW_S,
                    },
                }
            )
        return out

    async def emit(self, divergence: Dict[str, Any]) -> None:
        """Persist one divergence through the injected callback."""
        await self._persist(divergence)
