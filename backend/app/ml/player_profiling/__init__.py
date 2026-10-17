# backend/app/ml/player_profiling/__init__.py
"""
AEGIS AI - Player Profiling ML Module

Statistical player profiling with zero training data required:
- ``skill_rating``: Elo ratings updated per match outcome.
- ``clustering``: k-means (from scratch, stdlib only) to group
  similar player profiles.
- ``playstyle_classifier``: rule-based archetypes (fragger, support,
  anchor, igl, scout) with documented thresholds.

Typical pipeline: rate players over matches with :class:`RatingStore`,
cluster their aggregate stat vectors with :func:`fit`, and label each
profile with :func:`classify` for POV selection and commentary.
"""

from .skill_rating import (
    DEFAULT_K_FACTOR,
    DEFAULT_RATING,
    ELO_DIVISOR,
    RatingStore,
    expected_score,
    update_rating,
)
from .clustering import KMeansResult, fit as kmeans_fit, normalize
from .playstyle_classifier import (
    MATCH_LENGTH_MIN,
    PlayerFeatures,
    classify,
    extract_features,
)

__all__ = [
    # skill_rating
    "DEFAULT_RATING",
    "DEFAULT_K_FACTOR",
    "ELO_DIVISOR",
    "expected_score",
    "update_rating",
    "RatingStore",
    # clustering
    "KMeansResult",
    "kmeans_fit",
    "normalize",
    # playstyle_classifier
    "MATCH_LENGTH_MIN",
    "PlayerFeatures",
    "extract_features",
    "classify",
]
