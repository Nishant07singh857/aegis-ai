# backend/app/services/live_director/config.py
"""Phase 25 P1 configuration — ONE documented spot for every P1 weight.

All P1 behaviour (storyline multiplier W, PRE_COMBAT engagement state,
drop-clash watch, context-first beat) reads its numbers from
:class:`LiveDirectorConfig`. Nothing is scattered across the engine:
to retune the show, change a value here (or pass an override instance
to ``LiveDirector``) and every consumer follows.

Storyline bounds (the honesty contract)
----------------------------------------
``storyline_max_multiplier = 1.35`` caps W. The storyline term is a
MULTIPLIER over the fight-signal base (precombat + active_combat +
zone_pressure + clutch + priority), never an additive bonus:

    score = base * W,   W in [1.0, 1.35]

A team with no fight signal has base == 0, so its score is 0 no matter
how large W is — storyline can tip a close call (at base 50 the maximum
storyline contribution is 17.5 points, just above the default 15-point
switch margin) but it can NEVER manufacture a switch by itself. When
no storyline data exists for a team, W is exactly 1.0 and its factor
list is empty — stated in every decision-log breakdown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class LiveDirectorConfig:
    """Every Phase 25 P1 weight / threshold, with documented defaults."""

    # ── Build 1: storyline multiplier W ──────────────────────────
    #: Hard cap on W. See module docstring for the bound argument.
    storyline_max_multiplier: float = 1.35
    #: Live-standings rank uplifts (added inside W, before the cap).
    storyline_rank1_uplift: float = 0.10
    storyline_rank2_uplift: float = 0.06
    storyline_rank_top4_uplift: float = 0.03
    #: Team is on match point (match-point service / Smash engine).
    storyline_match_point_uplift: float = 0.10
    #: Hot streak: >= hot_streak_finishes finishes inside the window.
    storyline_hot_streak_uplift: float = 0.08
    hot_streak_finishes: int = 3
    hot_streak_window_s: float = 120.0
    #: Storyteller narrative flags active for the team.
    storyline_narrative_uplifts: Dict[str, float] = field(
        default_factory=lambda: {
            "rivalry": 0.05,
            "head_to_head": 0.05,
            "comeback": 0.05,
            "milestone": 0.04,
            "zone_advantage": 0.03,
            "win_probability": 0.03,
        }
    )
    #: Uplift for a narrative flag kind not listed above.
    storyline_narrative_default_uplift: float = 0.03
    #: Narrative flags (noted via the director) stay active this long.
    narrative_flag_ttl_s: float = 300.0
    #: Win-probability leader uplift: only for the highest-WWCD% team
    #: and only when its probability clears this floor (a 12% "leader"
    #: in a 16-team field is not a storyline).
    storyline_win_prob_uplift: float = 0.05
    win_prob_min_pct: float = 30.0

    # ── Build 2: PRE_COMBAT engagement state ─────────────────────
    #: Score value of the precombat term for a team whose engagement
    #: state is PRE_COMBAT (combined with intel via max, never summed
    #: past PRECOMBAT_CAP — the same signal is never double-counted).
    engagement_precombat_points: float = 15.0
    #: PotentialFight confidence at/above which PRE_COMBAT is entered
    #: (promotion to a real fight happens at 0.7 in FightDetector;
    #: PRE_COMBAT is the rising edge below that).
    precombat_confidence_enter: float = 0.5
    #: Minimap-pixel proximity at/below which teams count as
    #: converging. Pixels, honestly labelled — a metre threshold is
    #: only meaningful with a calibrated scale, which the caller may
    #: supply separately (see engagement.ProximityReading).
    precombat_proximity_threshold_px: float = 60.0
    #: PRE_COMBAT decays back to the phase baseline after this long
    #: without a refreshing convergence signal.
    precombat_ttl_s: float = 20.0
    #: ROTATING decays after this long without movement refresh.
    rotating_ttl_s: float = 20.0
    #: ACTIVE_COMBAT / CLUTCH decay when the underlying fight has had
    #: no activity for this long (mirrors the director's own fight
    #: resolution window, with a small grace margin).
    active_combat_ttl_s: float = 30.0

    # ── Build 3: drop clash ──────────────────────────────────────
    #: Precombat/watch weight a drop-clash team carries at plane/loot
    #: phase: enough to pre-position PVW on the clash, far below the
    #: on-air base (50), so a predicted clash never cuts PGM by itself.
    drop_clash_watch_points: float = 10.0
    #: Drop-clash watch entries expire with the intel TTL semantics.
    drop_clash_ttl_s: float = 300.0

    # ── Build 5: clone-mode grab (Phase 27, W3) ──────────────────
    #: Seconds without a human output write after which a clone grab
    #: auto-releases (checked on the LiveDirector evaluation tick).
    #: Ops config — deliberately NOT a learnable knob.
    clone_grab_inactivity_s: float = 120.0

    # ── Build 4: context-first beat ──────────────────────────────
    context_beat_enabled: bool = True
    #: Beat length. A few seconds: long enough to read the establishing
    #: view, far inside the grammar's scene minimums (early holds are
    #: 15-30 s; the beat is a punctuation, not a scene hold).
    context_beat_duration_s: float = 3.0
    #: Establishing scene used for the beat when the rig provides it.
    context_beat_scene: str = "map_cam"
