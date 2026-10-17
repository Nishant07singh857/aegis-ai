# backend/app/ml/player_profiling/clustering.py
"""
AEGIS AI - Player Clustering (k-means)

k-means clustering implemented from scratch with the standard
library only (no sklearn, no numpy). Used to group players with
similar statistical profiles (fraggers, campers, supports, ...) so
the broadcast can pick archetypes for POV selection and commentary.

Algorithm
---------
1. k-means++ seeding: pick the first centroid uniformly at random,
   then pick each subsequent centroid with probability proportional
   to its squared distance to the nearest already-chosen centroid.
   This spreads seeds apart and avoids degenerate local minima.
2. Lloyd iterations: assign every point to its nearest centroid,
   then recompute each centroid as the mean of its cluster.
3. Converge when the maximum centroid shift between iterations falls
   below ``tolerance``, or after ``max_iter`` iterations.

Deterministic when ``seed`` is given (uses random.Random(seed)).
"""

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import structlog

logger = structlog.get_logger("aegis.ml.player_profiling.clustering")


@dataclass
class KMeansResult:
    """Output of :func:`fit`."""
    labels: List[int]              # cluster index per input vector
    centroids: List[List[float]]   # final centroid of each cluster
    inertia: float                 # sum of squared distances to centroids
    iterations: int                # Lloyd iterations actually run


def normalize(vectors: List[List[float]]) -> List[List[float]]:
    """
    Z-score normalize a list of vectors feature-wise.

    Each column is transformed to zero mean and unit standard
    deviation, so features with large raw scales (e.g. damage)
    don't dominate the distance metric. Zero-variance columns
    are left as zeros.
    """
    if not vectors:
        raise ValueError("normalize() needs at least one vector")

    dim = len(vectors[0])
    if any(len(v) != dim for v in vectors):
        raise ValueError("all vectors must have the same dimension")

    n = len(vectors)
    means = [sum(v[i] for v in vectors) / n for i in range(dim)]
    variances = [
        sum((v[i] - means[i]) ** 2 for v in vectors) / n for i in range(dim)
    ]
    stds = [math.sqrt(var) if var > 0 else 1.0 for var in variances]

    return [
        [(v[i] - means[i]) / stds[i] for i in range(dim)]
        for v in vectors
    ]


def _squared_distance(a: List[float], b: List[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b))


def _mean(points: List[List[float]]) -> List[float]:
    dim = len(points[0])
    n = len(points)
    return [sum(p[i] for p in points) / n for i in range(dim)]


def _kmeans_plus_plus(
    vectors: List[List[float]], k: int, rng: random.Random
) -> List[List[float]]:
    """k-means++ centroid seeding."""
    first = rng.randrange(len(vectors))
    centroids = [list(vectors[first])]

    for _ in range(1, k):
        distances = []
        for v in vectors:
            nearest = min(_squared_distance(v, c) for c in centroids)
            distances.append(nearest)
        total = sum(distances)
        if total == 0.0:
            # All points identical (or already covered): pick at random.
            centroids.append(list(vectors[rng.randrange(len(vectors))]))
            continue
        pick = rng.random() * total
        for v, d in zip(vectors, distances):
            pick -= d
            if pick <= 0:
                centroids.append(list(v))
                break
        else:
            centroids.append(list(vectors[-1]))

    return centroids


def fit(
    vectors: List[List[float]],
    k: int,
    max_iter: int = 100,
    tolerance: float = 1e-4,
    seed: Optional[int] = None,
    n_init: int = 3,
) -> KMeansResult:
    """
    Run k-means clustering on ``vectors``.

    Args:
        vectors: Non-empty list of equal-length float vectors.
        k: Number of clusters (1 <= k <= len(vectors)).
        max_iter: Lloyd iterations cap per run.
        tolerance: Converge when the largest centroid shift < tolerance.
        seed: Optional random seed for deterministic results.
        n_init: Number of k-means++ runs; the best (lowest inertia)
            run is returned.

    Returns:
        KMeansResult with labels, centroids, inertia, iterations.

    Raises:
        ValueError: On empty input, ragged vectors, or bad k.
    """
    if not vectors:
        raise ValueError("fit() needs at least one vector")
    dim = len(vectors[0])
    if any(len(v) != dim for v in vectors):
        raise ValueError("all vectors must have the same dimension")
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    if k > len(vectors):
        raise ValueError(
            f"k ({k}) cannot exceed number of vectors ({len(vectors)})"
        )
    if n_init < 1:
        raise ValueError(f"n_init must be >= 1, got {n_init}")

    best: Optional[KMeansResult] = None

    for run in range(n_init):
        rng = random.Random(seed + run if seed is not None else None)
        centroids = _kmeans_plus_plus(vectors, k, rng)

        labels = [-1] * len(vectors)
        iterations = 0

        for iteration in range(1, max_iter + 1):
            iterations = iteration
            # Assignment step: nearest centroid per point.
            new_labels = []
            for v in vectors:
                nearest = min(
                    range(k), key=lambda c: _squared_distance(v, centroids[c])
                )
                new_labels.append(nearest)

            # Update step: centroid = mean of assigned points.
            new_centroids = []
            for c in range(k):
                members = [v for v, lab in zip(vectors, new_labels) if lab == c]
                if members:
                    new_centroids.append(_mean(members))
                else:
                    # Empty cluster: re-seed on the worst-fitting point.
                    worst = max(
                        range(len(vectors)),
                        key=lambda i: _squared_distance(vectors[i], centroids[new_labels[i]]),
                    )
                    new_centroids.append(list(vectors[worst]))

            shift = max(
                math.sqrt(_squared_distance(old, new))
                for old, new in zip(centroids, new_centroids)
            )
            centroids, labels = new_centroids, new_labels
            if shift < tolerance:
                break

        inertia = sum(
            _squared_distance(v, centroids[lab])
            for v, lab in zip(vectors, labels)
        )
        result = KMeansResult(
            labels=labels, centroids=centroids, inertia=inertia,
            iterations=iterations,
        )
        if best is None or result.inertia < best.inertia:
            best = result

    logger.debug(
        "kmeans_fit_done",
        n=len(vectors), k=k, runs=n_init,
        inertia=round(best.inertia, 4), iterations=best.iterations,
    )
    return best


__all__ = ["KMeansResult", "fit", "normalize"]
