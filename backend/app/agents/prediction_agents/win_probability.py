# backend/app/agents/prediction_agents/win_probability.py
"""
AEGIS AI — Live Win-Probability Scorer (Phase 20, W1).

Computes per-team WWCD% for the broadcast prediction cards, updated on a
30s cadence by the predictions API (frontend polls
``GET /api/v1/predictions/win-probability`` every 30s).

Consistency with the Phase 19 storyteller
-----------------------------------------
``StorytellerAgent.generate_win_probability`` (Phase 19) falls back to
``_heuristic_win_prob``: ``alive * 3.0 + kills * 2.0 + in_zone * 5.0``,
normalised to 100. This module uses the EXACT SAME base weights
(:data:`BASE_WEIGHTS`) so the live cards can never contradict the
storyteller's bars — the two surfaces agree on ranking and relative
order for identical input. Everything beyond the base is an additive
*bonus* that defaults to 0 when its data is missing, never a rewrite.

Signals (in evaluation order):
  1. alive players (base) + kills (base) + zone position (base)
  2. zone phase scaling — from zone 4 on, alive weight 3.0 -> 4.5
     (late game: being alive matters more than frags)
  3. form bonus — +2.0 * form_index, where form_index in [0, 1] comes
     from the team's last-5 match results (wins/top-5s). Neutral (0)
     when fewer than 2 results exist (same honesty bar as the
     storyteller's form generator).
  4. head-to-head bonus — among currently alive teams, a team's pairwise
     fight win rate vs every other alive team shifts its score by
     ``+3.0 * (avg_win_rate - 0.5) * 2`` (i.e. -3..+3). Neutral (0) when
     no fight history exists for a pair.

HARD RULE (zero fake data): the scorer returns ``None`` when there is no
usable live state (no alive teams, zero total score). Missing per-signal
data degrades to a neutral bonus, never to an invented number. Missing
percentages on the cards render "—" on the frontend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger("aegis.agents.win_probability")

#: Base weights — IDENTICAL to StorytellerAgent._heuristic_win_prob.
#: Do not change one without the other (see module docstring).
BASE_WEIGHTS = {
    "alive": 3.0,
    "kills": 2.0,
    "in_zone": 5.0,
}

#: Zone phase at/after which survival outweighs frags.
LATE_ZONE_PHASE = 4
#: Late-zone alive weight (3.0 -> 4.5).
LATE_ALIVE_WEIGHT = 4.5
#: Maximum absolute form bonus (form_index in [0, 1]).
FORM_BONUS_MAX = 2.0
#: Maximum absolute head-to-head swing (avg win rate in [0, 1] -> -3..+3).
H2H_SWING_MAX = 3.0
#: Minimum results before a form bonus is applied (matches storyteller).
FORM_MIN_RESULTS = 2


@dataclass
class ScoredTeam:
    """Intermediate scoring record for one team."""

    team_id: str
    team_name: str
    base_score: float = 0.0
    form_bonus: float = 0.0
    h2h_bonus: float = 0.0
    total_score: float = 0.0
    win_probability: float = 0.0
    delta: Optional[float] = None
    alive_count: int = 0
    kills: int = 0
    in_zone: bool = True
    breakdown: Dict[str, Any] = field(default_factory=dict)


def form_index(results: Optional[List[Dict[str, Any]]]) -> Optional[float]:
    """Normalised recent-form index in [0, 1] from last-5 match results.

    win = 1.0, top-5 = 0.5, otherwise 0.0 — averaged over the results.
    Returns None when fewer than :data:`FORM_MIN_RESULTS` results exist
    (honest: no bonus from thin history).
    """
    if not results or len(results) < FORM_MIN_RESULTS:
        return None
    points = 0.0
    for r in results:
        if r.get("won"):
            points += 1.0
        elif r.get("placement") is not None and r["placement"] <= 5:
            points += 0.5
    return points / len(results)


def h2h_rate_matrix(
    h2h: Optional[Dict[Tuple[str, str], Dict[str, Any]]],
) -> Dict[Tuple[str, str], float]:
    """Normalise raw pair records to ``(a, b) -> a's fight win rate``.

    Each record carries ``wins_a`` / ``wins_b`` (storyteller's
    ``_team_h2h`` contract). Pairs with zero fights are dropped —
    they contribute a neutral bonus, never an invented one.
    """
    rates: Dict[Tuple[str, str], float] = {}
    for (a, b), rec in (h2h or {}).items():
        wins_a = rec.get("wins_a") or 0
        wins_b = rec.get("wins_b") or 0
        total = wins_a + wins_b
        if total <= 0:
            continue
        rates[(a, b)] = wins_a / total
    return rates


def score_live_win_probability(
    teams: List[Dict[str, Any]],
    form_by_team: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    h2h: Optional[Dict[Tuple[str, str], Dict[str, Any]]] = None,
    zone_phase: Optional[int] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Score every alive team and normalise to win probabilities (0-100).

    Args:
        teams: live team state dicts — ``team_id``, ``team_name``,
            ``alive_count``, ``kills``/``finishes``, ``in_zone``.
        form_by_team: ``team_id -> last-5 match result dicts``.
        h2h: ``(team_a, team_b) -> _team_h2h record``.
        zone_phase: current zone phase number (int), if known.

    Returns:
        Ordered (descending) list of team dicts with ``win_probability``
        and a per-signal ``breakdown``, or None when no usable live
        state exists.
    """
    form_by_team = form_by_team or {}
    rates = h2h_rate_matrix(h2h)

    alive_weight = BASE_WEIGHTS["alive"]
    if zone_phase is not None and zone_phase >= LATE_ZONE_PHASE:
        alive_weight = LATE_ALIVE_WEIGHT

    scored: List[ScoredTeam] = []
    for t in teams:
        alive = int(t.get("alive_count") or 0)
        if alive <= 0:
            continue  # eliminated — 0% and excluded from the cards
        kills = int(t.get("kills") or t.get("finishes") or 0)
        in_zone = bool(t.get("in_zone", True))
        team_id = str(t.get("team_id"))

        base = alive * alive_weight + kills * BASE_WEIGHTS["kills"]
        if in_zone:
            base += BASE_WEIGHTS["in_zone"]

        fi = form_index(form_by_team.get(team_id))
        form_bonus = FORM_BONUS_MAX * fi if fi is not None else 0.0

        scored.append(
            ScoredTeam(
                team_id=team_id,
                team_name=str(t.get("team_name") or team_id),
                base_score=round(base, 2),
                form_bonus=round(form_bonus, 2),
                alive_count=alive,
                kills=kills,
                in_zone=in_zone,
            )
        )

    if not scored:
        return None

    # Head-to-head swing among currently alive teams.
    ids = {s.team_id for s in scored}
    for s in scored:
        vs_rates = []
        for (a, b), rate in rates.items():
            if a == s.team_id and b in ids and b != s.team_id:
                vs_rates.append(rate)
            elif b == s.team_id and a in ids and a != s.team_id:
                vs_rates.append(1.0 - rate)
        if vs_rates:
            avg = sum(vs_rates) / len(vs_rates)
            s.h2h_bonus = round(H2H_SWING_MAX * (avg - 0.5) * 2, 2)

    total = sum(s.base_score + s.form_bonus + s.h2h_bonus for s in scored)
    if total <= 0:
        return None

    for s in scored:
        s.total_score = round(s.base_score + s.form_bonus + s.h2h_bonus, 2)
        s.win_probability = round(s.total_score / total * 100, 1)
        s.breakdown = {
            "base": s.base_score,
            "form_bonus": s.form_bonus,
            "h2h_bonus": s.h2h_bonus,
            "alive_weight": alive_weight,
        }

    scored.sort(key=lambda s: s.win_probability, reverse=True)
    return [
        {
            "team_id": s.team_id,
            "team_name": s.team_name,
            "win_probability": s.win_probability,
            "delta": None,  # filled by the service against the last snapshot
            "alive_count": s.alive_count,
            "kills": s.kills,
            "in_zone": s.in_zone,
            "breakdown": s.breakdown,
        }
        for s in scored
    ]


def attach_deltas(
    current: List[Dict[str, Any]],
    previous: Optional[Dict[str, float]],
) -> List[Dict[str, Any]]:
    """Stamp per-team delta vs the previous 30s snapshot (None when new)."""
    prev = previous or {}
    for p in current:
        old = prev.get(p["team_id"])
        p["delta"] = round(p["win_probability"] - old, 1) if old is not None else None
    return current


def snapshot_map(current: List[Dict[str, Any]]) -> Dict[str, float]:
    """Flatten a scored list to ``team_id -> win_probability`` for caching."""
    return {
        p["team_id"]: p["win_probability"]
        for p in current
        if p.get("team_id") is not None and p.get("win_probability") is not None
    }
