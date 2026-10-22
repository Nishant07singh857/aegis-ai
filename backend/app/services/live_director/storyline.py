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
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .config import LiveDirectorConfig


def compute_storyline(
    team_id: str,
    *,
    standings_row: Optional[Dict[str, Any]] = None,
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
