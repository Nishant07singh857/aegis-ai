# backend/app/services/live_director/storyline.py
"""Storyline multiplier W (Phase 25, P1-4) — pure computation.

W is derived ONLY from data that exists at decision time:

  * live standings rank / points — the live writer's rows
    (``LiveStatsService.live_standings`` shape: team_id, rank,
    total_points, finishes),
  * match-point / Smash status — the match-point service's evaluated
    team set,
  * hot streak — finishes by this team inside a rolling window of the
    LIVE kill stream (counted by the director itself from kill events),
  * storyteller narrative flags active for the team (rivalry /
    milestone / comeback / head-to-head / zone advantage),
  * win probability — only the current WWCD% leader, only above the
    configured floor.

Every input is optional. A missing input contributes nothing; when NO
input exists for a team the result is exactly
``{"multiplier": 1.0, "factors": []}`` — never an invented narrative.

The multiplier is applied by the caller as ``score = base * W`` over
the fight-signal base only (see config.py for the bound argument).
This module never sees or produces a camera decision.

Phase 31 (audit item 3): the championship close-gap uplift. The caller
computes the #1–#2 points gap from the standings table BEFORE scoring
(:func:`championship_gap_from_standings`) and passes it in; when the
gap is at most 5 points BOTH top-2 teams (rank 1 and rank 2 only) get
+0.05 inside W. The spec's 2x multiplier is REJECTED — it cannot fit
the 1.35 cap — and no "drop everything" override exists anywhere here:
a championship-fight challenger takes the director's existing
clutch-challenger margin path (margin 5 in service.py), never a
bypass. The uplift lives in config-independent module constants
because LiveDirectorConfig is owned outside this worker's files;
a future move of these two numbers into config.py changes no
behaviour as long as the values stay 5.0 / 0.05.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .config import LiveDirectorConfig

#: Championship close-gap threshold (points). Gap <= this value counts
#: as a close championship race. Design choice from the audit's
#: adapted item 3 — NOT a measured number.
CHAMPIONSHIP_GAP_MAX_POINTS = 5.0
#: Uplift inside W for each top-2 team when the gap is close. The
#: spec's 2x is rejected: this small additive uplift respects the
#: 1.35 cap and the base-0 => score-0 rule.
CHAMPIONSHIP_GAP_UPLIFT = 0.05


def _coerce_rank(value: Any) -> Optional[int]:
    """A standings rank as an int, or None when it is not one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit():
            return int(text)
    return None


def _coerce_points(value: Any) -> Optional[float]:
    """A standings points total as a float, or None when it is not one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def championship_gap_from_standings(
    rows: Optional[Sequence[Dict[str, Any]]],
) -> Optional[float]:
    """The #1–#2 points gap from a standings table, or None.

    Needs a row with rank 1 AND a row with rank 2, each carrying a
    numeric ``total_points``. Any missing leg -> None (missing input
    -> no output, never an estimated gap). The gap is the absolute
    difference, so a mis-ordered table still yields its true gap
    rather than a negative one.
    """
    if not rows:
        return None
    points_by_rank: Dict[int, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        rank = _coerce_rank(row.get("rank"))
        if rank not in (1, 2) or rank in points_by_rank:
            continue
        points = _coerce_points(row.get("total_points"))
        if points is None:
            continue
        points_by_rank[rank] = points
    if 1 not in points_by_rank or 2 not in points_by_rank:
        return None
    return abs(points_by_rank[1] - points_by_rank[2])


def compute_storyline(
    team_id: str,
    *,
    standings_row: Optional[Dict[str, Any]] = None,
    championship_gap_points: Optional[float] = None,
    on_match_point: bool = False,
    recent_finishes: int = 0,
    narrative_flags: Sequence[str] = (),
    win_probability: Optional[float] = None,
    win_prob_is_leader: bool = False,
    config: Optional[LiveDirectorConfig] = None,
) -> Dict[str, Any]:
    """Return ``{multiplier, factors, uplift}`` for one team.

    ``factors`` entries are audit records: ``{factor, detail, uplift}``
    — the decision log carries them verbatim so a human can see WHY a
    leader got (or kept) the camera.
    """
    cfg = config or LiveDirectorConfig()
    factors: List[Dict[str, Any]] = []
    uplift = 0.0

    # ── live standings rank ──────────────────────────────────────
    if standings_row is not None:
        rank = standings_row.get("rank")
        points = standings_row.get("total_points")
        rank_uplift = 0.0
        rank_factor = None
        if isinstance(rank, int) and rank >= 1:
            if rank == 1:
                rank_uplift, rank_factor = cfg.storyline_rank1_uplift, "standings_rank_1"
            elif rank == 2:
                rank_uplift, rank_factor = cfg.storyline_rank2_uplift, "standings_rank_2"
            elif rank <= 4:
                rank_uplift, rank_factor = (
                    cfg.storyline_rank_top4_uplift,
                    "standings_rank_top4",
                )
        if rank_factor is not None:
            detail = f"live standings rank {rank}"
            if points is not None:
                detail += f", {points:g} pts"
            factors.append(
                {"factor": rank_factor, "detail": detail, "uplift": rank_uplift}
            )
            uplift += rank_uplift

    # ── championship close gap (Phase 31, audit item 3) ──────────
    # Rank 1 or 2 only, gap <= 5 pts only. The gap itself must come
    # from the caller (championship_gap_from_standings over the same
    # table the rank came from); this module never recomputes a
    # table it was not given. Rank 3+ never gets this uplift, and a
    # missing gap contributes nothing.
    if (
        standings_row is not None
        and isinstance(standings_row.get("rank"), int)
        and standings_row.get("rank") in (1, 2)
        and championship_gap_points is not None
        and championship_gap_points <= CHAMPIONSHIP_GAP_MAX_POINTS
    ):
        factors.append(
            {
                "factor": "championship_close_gap",
                "detail": (
                    f"#1-#2 gap {championship_gap_points:g} pts "
                    f"(<= {CHAMPIONSHIP_GAP_MAX_POINTS:g})"
                ),
                "uplift": CHAMPIONSHIP_GAP_UPLIFT,
            }
        )
        uplift += CHAMPIONSHIP_GAP_UPLIFT

    # ── match point / Smash ──────────────────────────────────────
    if on_match_point:
        factors.append(
            {
                "factor": "match_point",
                "detail": "team is on match point (match-point/Smash status)",
                "uplift": cfg.storyline_match_point_uplift,
            }
        )
        uplift += cfg.storyline_match_point_uplift

    # ── hot streak from the live kill stream ─────────────────────
    if recent_finishes >= cfg.hot_streak_finishes:
        factors.append(
            {
                "factor": "hot_streak",
                "detail": (
                    f"{recent_finishes} finishes in the last "
                    f"{cfg.hot_streak_window_s:g}s of the live kill stream"
                ),
                "uplift": cfg.storyline_hot_streak_uplift,
            }
        )
        uplift += cfg.storyline_hot_streak_uplift

    # ── storyteller narrative flags ──────────────────────────────
    seen = set()
    for raw in narrative_flags or ():
        kind = str(raw or "").strip().lower()
        if not kind or kind in seen:
            continue
        seen.add(kind)
        flag_uplift = cfg.storyline_narrative_uplifts.get(
            kind, cfg.storyline_narrative_default_uplift
        )
        factors.append(
            {
                "factor": f"narrative_{kind}",
                "detail": f"storyteller flag active: {kind}",
                "uplift": flag_uplift,
            }
        )
        uplift += flag_uplift

    # ── win probability (leader only, above the floor) ───────────
    if (
        win_prob_is_leader
        and win_probability is not None
        and win_probability >= cfg.win_prob_min_pct
    ):
        factors.append(
            {
                "factor": "win_probability_leader",
                "detail": f"WWCD% leader at {win_probability:g}%",
                "uplift": cfg.storyline_win_prob_uplift,
            }
        )
        uplift += cfg.storyline_win_prob_uplift

    multiplier = round(min(cfg.storyline_max_multiplier, 1.0 + uplift), 4)
    return {
        "multiplier": multiplier,
        "uplift": round(multiplier - 1.0, 4),
        "factors": factors,
    }
