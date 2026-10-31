# backend/app/services/live_director/payoff.py
"""Phase 31B — Payoff pack: elimination banner + winner preference.

PURE LOGIC, no bus, no I/O, no clock reads: every timestamp arrives
as an input, so every decision is reproducible in a unit test. Most
of this module IS wired into service.py — the elimination banner is
built in ``_on_team_wipe``, ``apply_winner_preference`` runs in
``_evaluate``, the clutch-incumbent lock lives in ``_evaluate``'s
margin rules, and ``_prune`` constructs ``WinnerPreference``
directly (see HOOK STATUS below). The two exceptions are
:func:`resolve_winner` / :func:`build_winner_preference`: tested
pure builders with ZERO production callers. They are kept, not
deleted, because they are the correct exact-alive-count path for a
future per-team alive-count provider — but they must never be
called with counts synthesised from wipe observations (see HOOK
STATUS step 2).

What was missing (DIRECTOR_SPEC_V2_AUDIT §1 item 5)
---------------------------------------------------
The ``team.eliminated`` event and the lower-third plumbing already
existed, but there was NO "TEAM X ELIMINATED" banner template, and a
fight resolving gave the winning team NO next-incumbent preference.
This module supplies both halves on the backend side; the template
itself is ``frontend/components/graphics/templates/
lowerthird-eliminated.tsx`` (registered as ``lowerthird-eliminated``).

Bible grammar (non-negotiable)
------------------------------
* A payoff NEVER cuts mid-fight and NEVER uses PiP/split: the banner
  is a lower-third graphic only (no camera action of any kind), and
  the winner preference is a tie-break at the NEXT decision (see
  below), never a forced instant cut. The switch itself, when the
  normal margin/hold gates allow it, goes through service.py's
  existing hard-cut path — this module defines
  ``WINNER_SWITCH_TRANSITION = "hard_cut"`` and no other transition.
* No facecam: there is no facecam feed source on this rig, so the
  payoff pack contains no facecam element (audit reject #11).
* No invented numbers: placement / finishes / teams-alive render
  ONLY when the event actually carried them; otherwise they are None
  and the banner shows the team line alone. A team name/tag is never
  invented — an event with no usable team identifier yields None.

The two fight-over clocks — reconciled honestly
------------------------------------------------
* FightDetector resolves at 8s inactivity (``resolution_inactivity``
  in fight_detector.py thresholds; its hard resolve is 2x = 16s in
  ``check_fight_resolution``).
* LiveDirector resolves at 25s inactivity (``FIGHT_RESOLVE_IDLE_S``
  in service.py, applied in ``_prune``).
* There is NO ``fight_ended`` producer on the bus (service.py's own
  comment says so: "FightDetector resolves fights internally and
  publishes nothing"). The ~17s gap between the clocks is therefore
  real: for up to ~17s the detector considers a fight over while the
  director still holds it live (holds apply, replay gate stays shut).
* PAYOFF GATES ON THE DIRECTOR'S 25s CLOCK, not the detector's 8s.
  Reason: the payoff's consumer is the director's next decision, and
  the director only knows a fight resolved when ``_prune`` drops it.
  Building a winner preference at detector-resolve time would hand
  the director a preference for a fight it still treats as live —
  and there is no bus message to deliver it on anyway. At prune
  time ``_prune`` builds ``WinnerPreference`` DIRECTLY from
  wipe-inference (sole fight team not observed wiped, with at
  least one other fight team observed wiped) — see HOOK STATUS.
  :func:`build_winner_preference` is NOT called from ``_prune``:
  it needs exact per-team alive counts and no provider supplies
  them at prune time (the director holds only an aggregate
  teams-alive count). If alive counts are unknown at prune time,
  the exact-count builder returns None — an ambiguous winner is
  never guessed, and wipe observations must never be converted
  into synthetic alive counts to feed it. Do NOT invent a
  ``fight_ended`` producer to close the gap; that producer does
  not exist, and this module does not fake one.

HOOK STATUS — what landed, what did not (Phase 35; this status
supersedes the original Phase 31B hook instructions)
---------------------------------------------------------------
1. CLUTCH-INCUMBENT LOCK — LANDED in ``LiveDirector._evaluate``
   (service.py margin rules): while the incumbent fight is clutch,
   challengers need the normal hold margin (40), under rule
   ``margin_clutch_incumbent_lock_40``. The challenger-side clutch
   margin (5) is unchanged.

2. PAYOFF ON RESOLVE — DID NOT LAND AS WRITTEN. ``_prune`` does
   NOT call :func:`build_winner_preference`, and the
   ``_team_alive_at_resolve`` provider proposed in the original
   hook note was never built: no code supplies exact per-team
   alive counts at prune time (the director holds only an
   aggregate teams-alive count). Instead ``_prune`` constructs
   ``WinnerPreference`` DIRECTLY from wipe inference: the sole
   fight team NOT observed wiped, when at least one other fight
   team WAS observed wiped (``team_wipe_event``). That is
   elimination inference, not an exact alive count, and it is
   labelled as such in ``_prune``.
   Wiring ``_prune`` through :func:`build_winner_preference` was
   considered and REJECTED in Phase 35 (backlog M-7): it would
   require converting wipe observations into synthetic alive
   counts (wiped = 0, not-observed-wiped = 1), which fabricates
   exact counts that were never measured — "not observed wiped"
   is not "exactly 1 alive" — and it would change behaviour
   (a single-team fight with no wipe observed would gain a
   preference the wipe-inference path correctly withholds).
   :func:`resolve_winner` / :func:`build_winner_preference`
   therefore remain TESTED-BUT-UNWIRED pure builders (unit-tested
   in ``test_phase31_b_clutch_payoff.py``; zero production
   callers). Do NOT call them from production code until a real
   per-team alive-count source exists; if one ever lands, it is
   the builder to call with those counts, and the wipe-inference
   construction in ``_prune`` must then be re-examined rather
   than silently kept alongside it.

3. PREFERENCE AS TIE-BREAK ONLY — LANDED in ``_evaluate``:
   ``apply_winner_preference`` reorders ONLY exact first-place
   score ties; it never changes a score, never manufactures a
   switch, and never bypasses the margin/hold gates. The
   preference is one-shot: consumed (and cleared) after the
   first evaluation that runs with it set.

4. ELIMINATION BANNER — LANDED IN PART: ``_on_team_wipe`` builds
   the banner via :func:`build_elimination_banner`; in semi_auto
   it becomes a lower-third suggestion for operator approval, and
   in every other mode it is logged only (DirectorService
   lower-third writes are manual-gated by design). The banner
   title is always ``"ELIMINATED"``; the subtitle is
   ``"<TAG> ELIMINATED"`` plus ``" • #<placement>"`` ONLY when
   the event carried a placement. There is currently NO automatic
   team.eliminated -> lower-third path in the frontend (the
   overlay page renders commentary lines, the director page
   renders manual lower-third state) — until one lands, the
   template is selectable via ``?template=lowerthird-eliminated``
   on /overlays/lower-third and via the graphics template
   picker. The template alone does not auto-appear on
   elimination; claiming otherwise would be false.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

# ── Clock reconciliation (code constants, not measurements) ───────
#: FightDetector inactivity resolve (fight_detector.py thresholds).
DETECTOR_RESOLVE_IDLE_S = 8.0
#: FightDetector hard resolve (check_fight_resolution: 2x inactivity).
DETECTOR_HARD_RESOLVE_S = 16.0
#: LiveDirector resolve (service.py FIGHT_RESOLVE_IDLE_S, _prune).
DIRECTOR_RESOLVE_IDLE_S = 25.0
#: Which clock gates payoff (see module docstring).
PAYOFF_GATES_ON = "director_25s"
#: There is no fight_ended producer — stated, not papered over.
FIGHT_ENDED_PRODUCER = None

#: The ONLY transition a winner switch may use (Bible: hard cuts).
WINNER_SWITCH_TRANSITION = "hard_cut"

#: Default safety TTL for an unconsumed winner preference (mirrors
#: LiveDirectorConfig.payoff_preference_ttl_s; an editorial tunable).
DEFAULT_PAYOFF_PREFERENCE_TTL_S = 60.0

_UNKNOWN = {"", "unknown", "none"}


def payoff_clock_note() -> Dict[str, Any]:
    """The two-clock reconciliation as data (for ledgers / QA)."""
    return {
        "detector_resolve_idle_s": DETECTOR_RESOLVE_IDLE_S,
        "detector_hard_resolve_s": DETECTOR_HARD_RESOLVE_S,
        "director_resolve_idle_s": DIRECTOR_RESOLVE_IDLE_S,
        "gap_s": DIRECTOR_RESOLVE_IDLE_S - DETECTOR_RESOLVE_IDLE_S,
        "payoff_gates_on": PAYOFF_GATES_ON,
        "fight_ended_producer": FIGHT_ENDED_PRODUCER,
        "winner_switch_transition": WINNER_SWITCH_TRANSITION,
        "note": (
            "Payoff is built at the director's 25s resolve in _prune, "
            "not at the detector's 8s resolve: the detector publishes "
            "no fight_ended event, and the director still holds the "
            "fight live until its own 25s idle prune."
        ),
    }


def _clean(value: Any) -> Optional[str]:
    """Usable identifier string, or None for missing/Unknown."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in _UNKNOWN:
        return None
    return text


def _first(data: Mapping[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        cleaned = _clean(data.get(key))
        if cleaned is not None:
            return cleaned
    return None


def _exact_int(value: Any) -> Optional[int]:
    """Exact int for ints / integral floats, else None (bool rejected)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def _placement(data: Mapping[str, Any]) -> Optional[int]:
    for key in ("placement", "final_placement", "rank", "final_rank"):
        parsed = _exact_int(data.get(key))
        if parsed is not None and parsed > 0:
            return parsed
    return None


def _finishes(data: Mapping[str, Any]) -> Optional[int]:
    for key in ("finishes", "finish_count"):
        parsed = _exact_int(data.get(key))
        if parsed is not None and parsed >= 0:
            return parsed
    return None


# ── Elimination banner ────────────────────────────────────────────


@dataclass
class EliminationBanner:
    """Backend-side banner payload for one eliminated team.

    Only ``team_id``/``team_tag`` are required; every numeric field
    is Optional and stays None when the event did not carry it.
    """

    team_id: str
    team_tag: str
    title: str = "ELIMINATED"
    placement: Optional[int] = None
    finishes: Optional[int] = None
    teams_alive: Optional[int] = None
    match_id: Optional[str] = None
    event_id: Optional[str] = None

    @property
    def subtitle(self) -> str:
        """``<TAG> ELIMINATED`` (+ placement ONLY when known)."""
        line = f"{self.team_tag} ELIMINATED"
        if self.placement is not None:
            line += f" • #{self.placement}"
        return line

    @property
    def anim_key(self) -> str:
        """Stable re-trigger key: event id when present, else team."""
        return self.event_id or f"elim-{self.team_id}"

    def to_lower_third_payload(self) -> Dict[str, Any]:
        """LowerThirdProps-shaped payload (visible/title/subtitle)."""
        return {
            "visible": True,
            "title": self.title,
            "subtitle": self.subtitle,
            "animKey": self.anim_key,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "team_id": self.team_id,
            "team_tag": self.team_tag,
            "title": self.title,
            "subtitle": self.subtitle,
            "placement": self.placement,
            "finishes": self.finishes,
            "teams_alive": self.teams_alive,
            "match_id": self.match_id,
            "event_id": self.event_id,
            "animKey": self.anim_key,
        }


def build_elimination_banner(event: Mapping[str, Any]) -> Optional[EliminationBanner]:
    """Build an elimination banner from a team.eliminated payload.

    Returns None when no usable team identifier exists — a banner
    for an unnamed team would be fabrication. Tag preference:
    explicit tag keys first, team_id as the display fallback (the
    id is real data, not an invented name). Placement/finishes are
    read only from their own keys; kills are NOT relabelled as
    finishes.
    """
    if not isinstance(event, Mapping):
        return None
    team_id = _first(event, "team_id", "teamId", "team")
    tag = _first(event, "team_tag", "teamTag", "tag")
    if team_id is None and tag is None:
        return None
    display_tag = tag or team_id  # type: ignore[assignment]
    teams_alive = _exact_int(event.get("teams_alive"))
    if teams_alive is not None and teams_alive < 0:
        teams_alive = None
    return EliminationBanner(
        team_id=team_id or display_tag,
        team_tag=display_tag,
        placement=_placement(event),
        finishes=_finishes(event),
        teams_alive=teams_alive,
        match_id=_clean(event.get("match_id")),
        event_id=_clean(event.get("event_id")) or _clean(event.get("id")),
    )


# ── Winner preference ─────────────────────────────────────────────


def resolve_winner(
    teams_alive: Mapping[str, Any],
    fight_teams: Optional[List[str]] = None,
) -> Optional[str]:
    """Sole-survivor winner from exact alive counts, or None.

    STATUS (Phase 35, backlog M-7): tested-but-unwired pure
    builder — zero production callers. It must only ever be fed
    exact per-team alive counts that were actually measured; wipe
    observations must never be converted into synthetic counts
    for it (see module HOOK STATUS step 2).

    Candidates are the fight's teams when given, else every key in
    ``teams_alive``. The winner is the ONE team with alive > 0 while
    every other candidate is exactly 0. ANY unknown candidate (None /
    non-integral / bool / negative) makes the result None — an
    ambiguous winner is never guessed. Zero survivors (mutual wipe)
    and 2+ survivors (fight unresolved or multi-team remainder) also
    yield None.
    """
    if not isinstance(teams_alive, Mapping):
        return None
    candidates = list(fight_teams) if fight_teams else list(teams_alive.keys())
    if not candidates:
        return None
    counts: Dict[str, int] = {}
    for team_id in candidates:
        if team_id not in teams_alive:
            return None
        parsed = _exact_int(teams_alive.get(team_id))
        if parsed is None or parsed < 0:
            return None
        counts[str(team_id)] = parsed
    alive = [team for team, count in counts.items() if count > 0]
    if len(alive) != 1:
        return None
    return alive[0]


@dataclass
class WinnerPreference:
    """One-shot next-decision preference for a fight's winner.

    Created at the director's resolve (25s clock) and consumed by
    the FIRST ``_evaluate`` after creation, whether or not it
    changed the ordering — it is a next-decision preference, not a
    standing boost. ``ttl_s`` is only a safety expiry for a
    preference whose next decision never arrives.
    """

    fight_id: str
    preferred_team_id: str
    created_at: float
    ttl_s: float = DEFAULT_PAYOFF_PREFERENCE_TTL_S
    consumed: bool = False

    def is_active(self, now: float) -> bool:
        """True while unconsumed and inside the TTL window."""
        if self.consumed:
            return False
        try:
            now_f = float(now)
        except (TypeError, ValueError):
            return False
        return now_f <= self.created_at + self.ttl_s

    def applies_to(self, team_id: Any, now: float) -> bool:
        """True when this preference names ``team_id`` and is active."""
        return self.is_active(now) and str(team_id) == self.preferred_team_id

    def mark_consumed(self) -> None:
        """Consume: a one-shot preference never applies twice."""
        self.consumed = True


def build_winner_preference(
    resolved_fight: Mapping[str, Any],
    teams_alive: Mapping[str, Any],
    now: float,
    ttl_s: float = DEFAULT_PAYOFF_PREFERENCE_TTL_S,
) -> Optional[WinnerPreference]:
    """Build the winner preference for a director-resolved fight.

    STATUS (Phase 35, backlog M-7): tested-but-unwired — zero
    production callers; ``_prune`` builds ``WinnerPreference``
    directly from wipe inference instead (see module HOOK STATUS
    step 2). Call only with exact per-team alive counts that were
    actually measured, never with counts synthesised from wipe
    observations.

    ``resolved_fight`` carries ``fight_id`` and ``teams_involved``
    (or ``teams``). Returns None when the fight id is missing or
    the winner is ambiguous (see :func:`resolve_winner`) — never a
    forced cut, never a guessed winner.
    """
    if not isinstance(resolved_fight, Mapping):
        return None
    fight_id = _clean(resolved_fight.get("fight_id")) or _clean(
        resolved_fight.get("fightId")
    )
    if fight_id is None:
        return None
    raw_teams = resolved_fight.get("teams_involved") or resolved_fight.get("teams")
    fight_teams = None
    if isinstance(raw_teams, (list, tuple)):
        cleaned = [_clean(t) for t in raw_teams]
        fight_teams = [t for t in cleaned if t is not None]
        if not fight_teams:
            fight_teams = None
    winner = resolve_winner(teams_alive, fight_teams)
    if winner is None:
        return None
    try:
        ttl = float(ttl_s)
    except (TypeError, ValueError):
        ttl = DEFAULT_PAYOFF_PREFERENCE_TTL_S
    if not math.isfinite(ttl) or ttl <= 0:
        ttl = DEFAULT_PAYOFF_PREFERENCE_TTL_S
    return WinnerPreference(
        fight_id=fight_id,
        preferred_team_id=winner,
        created_at=float(now),
        ttl_s=ttl,
    )


def apply_winner_preference(
    challengers: List[Dict[str, Any]],
    preference: Optional[WinnerPreference],
    now: float,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Apply a winner preference as a FIRST-PLACE TIE-BREAK ONLY.

    Returns (ordered_challengers, changed). If the preference is
    inactive, names no challenger, or the preferred challenger is
    not tied for the top score, the order is returned unchanged
    (``changed=False``). Scores are never modified, so the margin /
    hold gates downstream still decide every switch — a preferred
    team that is behind on score stays behind. The input list is
    never mutated; a new list is returned.
    """
    ordered = list(challengers)
    if preference is None or not preference.is_active(now):
        return ordered, False
    if not ordered:
        return ordered, False

    def _score(entry: Any) -> Optional[float]:
        if not isinstance(entry, Mapping):
            return None
        try:
            value = float(entry.get("score"))
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    scores = [_score(entry) for entry in ordered]
    if any(score is None for score in scores):
        return ordered, False
    top = max(scores)  # type: ignore[arg-type]
    tied_idx = [i for i, score in enumerate(scores) if score == top]
    if len(tied_idx) <= 1:
        return ordered, False
    preferred_idx = None
    for i in tied_idx:
        entry = ordered[i]
        if isinstance(entry, Mapping) and str(entry.get("team_id")) == (
            preference.preferred_team_id
        ):
            preferred_idx = i
            break
    if preferred_idx is None or preferred_idx == tied_idx[0]:
        return ordered, False
    entry = ordered.pop(preferred_idx)
    ordered.insert(tied_idx[0], entry)
    return ordered, True


__all__ = [
    "DETECTOR_RESOLVE_IDLE_S",
    "DETECTOR_HARD_RESOLVE_S",
    "DIRECTOR_RESOLVE_IDLE_S",
    "PAYOFF_GATES_ON",
    "FIGHT_ENDED_PRODUCER",
    "WINNER_SWITCH_TRANSITION",
    "DEFAULT_PAYOFF_PREFERENCE_TTL_S",
    "EliminationBanner",
    "WinnerPreference",
    "build_elimination_banner",
    "build_winner_preference",
    "resolve_winner",
    "apply_winner_preference",
    "payoff_clock_note",
]
