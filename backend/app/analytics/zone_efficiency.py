# backend/app/analytics/zone_efficiency.py
"""
Zone efficiency metric (Phase 22 / W3, M7).

DEFINITION — a team's zone efficiency over a set of zone observations
(rotation/positioning samples, each carrying the ingredients the
analyzers already compute) is:

    zone_efficiency = 100 * (0.5 * in_zone_fraction
                             + 0.3 * proximity_score
                             + 0.2 * safety_score)

* ``in_zone_fraction`` — fraction of samples with ``in_zone`` true.
* ``proximity_score`` — mean over OUT-of-zone samples of
  ``1 / (1 + distance_to_edge_m / 200)`` (in-zone samples contribute
  1.0): being 200 m out halves the credit.
* ``safety_score`` — ``1 - mean(danger_level / 100)`` over samples
  that carry a danger level (rotation analyzer's 0–100 danger).

Result is 0..100. With NO observations the score is ``None`` (unknown)
— never 0, never invented. Each observation is a plain mapping with
keys ``in_zone`` (bool), ``distance_to_edge`` (metres, optional) and
``danger_level`` (0–100, optional), exactly the fields
``RotationAnalyzer.zone_analysis`` / ``PositioningAnalyzer`` expose.

UPDATE CADENCE: computed on demand from analyzer state (see
docs/METRIC_CADENCE.md) — the publisher below is called by the
analytics API; there is no background writer inventing history.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional


def compute_zone_efficiency(observations: Iterable[Mapping[str, Any]]) -> Optional[float]:
    """Zone efficiency 0..100 from analyzer observations (see docstring)."""
    obs: List[Mapping[str, Any]] = list(observations or [])
    if not obs:
        return None
    in_zone_fraction = sum(1 for o in obs if o.get("in_zone")) / len(obs)

    proximity_terms: List[float] = []
    for o in obs:
        if o.get("in_zone"):
            proximity_terms.append(1.0)
        else:
            dist = o.get("distance_to_edge", o.get("distance_to_zone_edge"))
            if dist is None:
                proximity_terms.append(0.0)
            else:
                proximity_terms.append(1.0 / (1.0 + float(dist) / 200.0))
    proximity = sum(proximity_terms) / len(proximity_terms)

    dangers = [
        float(o["danger_level"]) for o in obs if o.get("danger_level") is not None
    ]
    safety = 1.0 - (sum(dangers) / len(dangers) / 100.0) if dangers else 1.0

    score = 100.0 * (0.5 * in_zone_fraction + 0.3 * proximity + 0.2 * safety)
    return round(max(0.0, min(100.0, score)), 2)


def zone_efficiency_report(
    team_observations: Dict[str, Iterable[Mapping[str, Any]]],
) -> Dict[str, Any]:
    """Publisher shape for the analytics API: per-team score + honesty.

    Teams without observations appear with ``score: None`` and the
    reason, so dashboards render "—" instead of a fabricated number.
    """
    teams: Dict[str, Any] = {}
    for team_id, obs in (team_observations or {}).items():
        obs_list = list(obs or [])
        teams[team_id] = {
            "score": compute_zone_efficiency(obs_list),
            "samples": len(obs_list),
            "reason": None if obs_list else "no zone observations recorded",
        }
    return {"metric": "zone_efficiency", "unit": "0-100", "teams": teams}
