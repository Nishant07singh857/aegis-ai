# backend/app/ml/player_profiling/playstyle_classifier.py
"""
AEGIS AI - Playstyle Classifier

Rule-based archetype classifier for BGMI players. Takes a per-player
stats dict and returns one archetype label with a confidence and
human-readable reasons. No training data or ML model needed — the
thresholds below were picked from typical BGMI competitive ranges
(per-match basis) and are documented so broadcast/analyst users can
tune them.

Labels
------
- ``fragger``: high kill output and damage per match.
- ``support``: team utility — assists, revives, heals per match.
- ``anchor``: survives long and places well, but fights less.
- ``igl``: in-game leader — good placement plus playmaking assists.
- ``scout``: roams far for information, lower frag output.
- ``flex``: fallback when no archetype fires clearly.

Thresholds (per match unless stated)
------------------------------------
- fragger:  kills/match >= 3.0 AND damage/match >= 450
- support:  (assists + revives + heals)/match >= 1.2 AND kills/match <= 2.5
- anchor:   survival_ratio >= 0.60 (avg survival vs 32-min match) AND
            kills/match < 2.0 AND top10_rate >= 0.35
- igl:      top10_rate >= 0.45 AND assists/match >= 0.8 AND
            survival_ratio >= 0.55 AND kills/match <= 3.5
- scout:    distance_travelled_km/match >= 6.0 AND kills/match <= 2.0
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import structlog

logger = structlog.get_logger("aegis.ml.player_profiling.playstyle")

#: Full-length BGMI match in minutes; used to turn survival time into a ratio.
MATCH_LENGTH_MIN: float = 32.0

FRAGGER_KILLS_PER_MATCH: float = 3.0
FRAGGER_DAMAGE_PER_MATCH: float = 450.0
SUPPORT_UTILITY_PER_MATCH: float = 1.2
SUPPORT_MAX_KILLS: float = 2.5
ANCHOR_SURVIVAL_RATIO: float = 0.60
ANCHOR_MAX_KILLS: float = 2.0
ANCHOR_TOP10_RATE: float = 0.35
IGL_TOP10_RATE: float = 0.45
IGL_ASSISTS_PER_MATCH: float = 0.8
IGL_SURVIVAL_RATIO: float = 0.55
IGL_MAX_KILLS: float = 3.5
SCOUT_DISTANCE_KM_PER_MATCH: float = 6.0
SCOUT_MAX_KILLS: float = 2.0


@dataclass
class PlayerFeatures:
    """Per-match derived features used by the rule engine."""
    kills: float = 0.0
    damage: float = 0.0
    utility: float = 0.0        # assists + revives + heals per match
    assists: float = 0.0
    survival_ratio: float = 0.0
    top10_rate: float = 0.0
    distance_km: float = 0.0
    matches: float = 0.0


def extract_features(stats: Dict[str, float]) -> PlayerFeatures:
    """
    Convert a raw stats dict into per-match features.

    Accepts either career totals (supply ``matches_played``) or
    already-per-match values (omit ``matches_played`` / set to 0).

    Recognized keys: kills, damage_dealt, assists, revives, heals,
    headshot_kills, matches_played, avg_survival_min, wins, top10,
    top10_finishes, distance_travelled_km.
    """
    matches = float(stats.get("matches_played", 0) or 0)

    def per_match(total: float) -> float:
        return total / matches if matches > 0 else float(total)

    survival_min = float(stats.get("avg_survival_min", 0) or 0)
    top10 = float(stats.get("top10", stats.get("top10_finishes", 0)) or 0)

    return PlayerFeatures(
        kills=per_match(float(stats.get("kills", 0) or 0)),
        damage=per_match(float(stats.get("damage_dealt", 0) or 0)),
        utility=per_match(
            float(stats.get("assists", 0) or 0)
            + float(stats.get("revives", 0) or 0)
            + float(stats.get("heals", 0) or 0)
        ),
        assists=per_match(float(stats.get("assists", 0) or 0)),
        survival_ratio=min(1.0, survival_min / MATCH_LENGTH_MIN),
        top10_rate=(top10 / matches) if matches > 0 else 0.0,
        distance_km=per_match(float(stats.get("distance_travelled_km", 0) or 0)),
        matches=matches,
    )


@dataclass
class _ArchetypeHit:
    label: str
    score: float
    reasons: List[str] = field(default_factory=list)


def _rule_scores(feat: PlayerFeatures) -> List[_ArchetypeHit]:
    """Evaluate every archetype rule, returning the ones that fire."""
    hits: List[_ArchetypeHit] = []

    if feat.kills >= FRAGGER_KILLS_PER_MATCH and feat.damage >= FRAGGER_DAMAGE_PER_MATCH:
        hits.append(_ArchetypeHit(
            "fragger",
            score=(feat.kills / FRAGGER_KILLS_PER_MATCH
                   + feat.damage / FRAGGER_DAMAGE_PER_MATCH) / 2,
            reasons=[
                f"{feat.kills:.1f} kills/match (>= {FRAGGER_KILLS_PER_MATCH})",
                f"{feat.damage:.0f} damage/match (>= {FRAGGER_DAMAGE_PER_MATCH:.0f})",
            ],
        ))

    if feat.utility >= SUPPORT_UTILITY_PER_MATCH and feat.kills <= SUPPORT_MAX_KILLS:
        hits.append(_ArchetypeHit(
            "support",
            score=feat.utility / SUPPORT_UTILITY_PER_MATCH,
            reasons=[
                f"{feat.utility:.1f} assists/revives/heals per match "
                f"(>= {SUPPORT_UTILITY_PER_MATCH})",
                f"{feat.kills:.1f} kills/match (<= {SUPPORT_MAX_KILLS}, utility-first)",
            ],
        ))

    if (feat.survival_ratio >= ANCHOR_SURVIVAL_RATIO
            and feat.kills < ANCHOR_MAX_KILLS
            and feat.top10_rate >= ANCHOR_TOP10_RATE):
        hits.append(_ArchetypeHit(
            "anchor",
            score=(feat.survival_ratio / ANCHOR_SURVIVAL_RATIO
                   + feat.top10_rate / ANCHOR_TOP10_RATE) / 2,
            reasons=[
                f"survives {feat.survival_ratio:.0%} of matches "
                f"(>= {ANCHOR_SURVIVAL_RATIO:.0%})",
                f"top-10 rate {feat.top10_rate:.0%} (>= {ANCHOR_TOP10_RATE:.0%})",
                f"{feat.kills:.1f} kills/match (< {ANCHOR_MAX_KILLS}, plays safe)",
            ],
        ))

    if (feat.top10_rate >= IGL_TOP10_RATE
            and feat.assists >= IGL_ASSISTS_PER_MATCH
            and feat.survival_ratio >= IGL_SURVIVAL_RATIO
            and feat.kills <= IGL_MAX_KILLS):
        hits.append(_ArchetypeHit(
            "igl",
            score=(feat.top10_rate / IGL_TOP10_RATE
                   + feat.assists / IGL_ASSISTS_PER_MATCH) / 2,
            reasons=[
                f"top-10 rate {feat.top10_rate:.0%} (>= {IGL_TOP10_RATE:.0%})",
                f"{feat.assists:.1f} assists/match (>= {IGL_ASSISTS_PER_MATCH})",
                f"survives {feat.survival_ratio:.0%} of matches "
                f"(>= {IGL_SURVIVAL_RATIO:.0%}, stays alive to call)",
            ],
        ))

    if feat.distance_km >= SCOUT_DISTANCE_KM_PER_MATCH and feat.kills <= SCOUT_MAX_KILLS:
        hits.append(_ArchetypeHit(
            "scout",
            score=feat.distance_km / SCOUT_DISTANCE_KM_PER_MATCH,
            reasons=[
                f"{feat.distance_km:.1f} km travelled/match "
                f"(>= {SCOUT_DISTANCE_KM_PER_MATCH})",
                f"{feat.kills:.1f} kills/match (<= {SCOUT_MAX_KILLS}, roams not fights)",
            ],
        ))

    return hits


def classify(stats: Dict[str, float]) -> Tuple[str, float, List[str]]:
    """
    Classify a player's playstyle.

    Args:
        stats: Raw stats dict (see :func:`extract_features`).

    Returns:
        (label, confidence, reasons). Confidence is in [0, 1] and
        grows with the margin by which the winning rule's thresholds
        were cleared. Returns ``("flex", low_confidence, [reason])``
        when no archetype fires clearly.
    """
    feat = extract_features(stats)
    hits = _rule_scores(feat)

    if not hits:
        logger.debug("playstyle_classify_flex", stats=stats)
        return ("flex", 0.25, ["no archetype thresholds met; mixed playstyle"])

    hits.sort(key=lambda h: h.score, reverse=True)
    winner = hits[0]
    # Confidence: 0.5 for exactly meeting the thresholds, rising toward
    # 1.0 as the margin above the thresholds grows (score is the mean
    # threshold-clearance ratio).
    margin = max(0.0, winner.score - 1.0)
    confidence = max(0.5, min(1.0, 0.5 + 0.3 * margin))

    runner_up = f", runner-up {hits[1].label}" if len(hits) > 1 else ""
    logger.debug(
        "playstyle_classified",
        label=winner.label, confidence=round(confidence, 2),
        reasons=winner.reasons, note=runner_up,
    )
    return winner.label, round(confidence, 3), winner.reasons


__all__ = [
    "MATCH_LENGTH_MIN",
    "PlayerFeatures",
    "extract_features",
    "classify",
]
