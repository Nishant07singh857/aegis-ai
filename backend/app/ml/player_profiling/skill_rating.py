# backend/app/ml/player_profiling/skill_rating.py
"""
AEGIS AI - Player Skill Rating (Elo)

Classic Elo rating system for ranking BGMI players by head-to-head
match outcomes. Zero training data required — ratings evolve
incrementally as matches are recorded.

Math
----
Expected score of player A against player B (logistic curve, base 10):

    E_a = 1 / (1 + 10 ** ((R_b - R_a) / 400))

Rating update after the match (K = K-factor, S_a = actual outcome):

    R_a' = R_a + K * (S_a - E_a)

The 400-point divisor means a player rated 400 points higher is
expected to win ~91% of the time (10:1 odds). The K-factor controls
how fast ratings move: higher K = more volatile.
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger("aegis.ml.player_profiling.skill_rating")

#: Rating every new player starts at.
DEFAULT_RATING: float = 1000.0

#: Standard K-factor for established players.
DEFAULT_K_FACTOR: float = 32.0

#: Denominator of the logistic curve (10:1 odds per 400 points).
ELO_DIVISOR: float = 400.0


def expected_score(rating_a: float, rating_b: float) -> float:
    """
    Expected score of player A against player B.

    Returns a probability in [0, 1]. Symmetric: expected_score(a, b)
    == 1 - expected_score(b, a).
    """
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / ELO_DIVISOR))


def update_rating(
    rating_a: float,
    rating_b: float,
    outcome: float,
    k_factor: float = DEFAULT_K_FACTOR,
) -> Tuple[float, float]:
    """
    Update both players' ratings after a match.

    Args:
        rating_a: Current rating of player A.
        rating_b: Current rating of player B.
        outcome: Actual score for player A: 1.0 = A wins,
            0.5 = draw, 0.0 = B wins.
        k_factor: How many points move at stake (volatility).

    Returns:
        (new_rating_a, new_rating_b). The points A gains equal the
        points B loses, so the pool is zero-sum.

    Raises:
        ValueError: If outcome is not in {1.0, 0.5, 0.0}, or if
            k_factor is not positive.
    """
    if outcome not in (1.0, 0.5, 0.0):
        raise ValueError(
            f"outcome must be 1.0 (win), 0.5 (draw) or 0.0 (loss), got {outcome!r}"
        )
    if k_factor <= 0:
        raise ValueError(f"k_factor must be positive, got {k_factor!r}")

    exp_a = expected_score(rating_a, rating_b)
    exp_b = 1.0 - exp_a

    new_a = rating_a + k_factor * (outcome - exp_a)
    new_b = rating_b + k_factor * ((1.0 - outcome) - exp_b)
    return new_a, new_b


@dataclass
class RatingStore:
    """
    In-memory store of player Elo ratings.

    A real deployment should persist ratings to the database. To do
    that, subclass and override :meth:`load` / :meth:`save`:

        class DbRatingStore(RatingStore):
            def load(self) -> None:
                self._ratings = {row.player_id: row.rating for row in ...}
            def save(self) -> None:
                for player_id, rating in self._ratings.items(): ...
    """

    initial_rating: float = DEFAULT_RATING
    k_factor: float = DEFAULT_K_FACTOR
    on_update: Optional[Callable[[str, str, float, float, float], None]] = None

    _ratings: Dict[str, float] = field(default_factory=dict, init=False)

    def load(self) -> None:
        """Hook: populate ``_ratings`` from durable storage. No-op by default."""
        logger.debug("RatingStore.load: in-memory only, nothing to load")

    def save(self) -> None:
        """Hook: flush ``_ratings`` to durable storage. No-op by default."""
        logger.debug("RatingStore.save: in-memory only, nothing to save")

    def get(self, player_id: str) -> float:
        """Current rating for a player (initial rating if unseen)."""
        return self._ratings.get(player_id, self.initial_rating)

    def record_match(
        self,
        player_a: str,
        player_b: str,
        outcome: float,
        k_factor: Optional[float] = None,
    ) -> Tuple[float, float]:
        """
        Record a match result and update both ratings.

        Args:
            player_a: ID of player A.
            player_b: ID of player B.
            outcome: 1.0 = A wins, 0.5 = draw, 0.0 = B wins.
            k_factor: Override the store default for this match.

        Returns:
            (new_rating_a, new_rating_b).
        """
        k = self.k_factor if k_factor is None else k_factor
        old_a, old_b = self.get(player_a), self.get(player_b)
        new_a, new_b = update_rating(old_a, old_b, outcome, k)
        self._ratings[player_a] = new_a
        self._ratings[player_b] = new_b

        logger.info(
            "elo_match_recorded",
            player_a=player_a, player_b=player_b,
            old_a=round(old_a, 2), old_b=round(old_b, 2),
            new_a=round(new_a, 2), new_b=round(new_b, 2),
            outcome=outcome,
        )
        if self.on_update is not None:
            self.on_update(player_a, player_b, outcome, new_a, new_b)
        return new_a, new_b

    def leaderboard(self, top_n: int = 10) -> List[Tuple[str, float]]:
        """Top ``top_n`` players by rating, descending."""
        return sorted(self._ratings.items(), key=lambda kv: kv[1], reverse=True)[:top_n]


__all__ = [
    "DEFAULT_RATING",
    "DEFAULT_K_FACTOR",
    "ELO_DIVISOR",
    "expected_score",
    "update_rating",
    "RatingStore",
]
