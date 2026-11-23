"""Small statistics helpers, standard library only.

Everything here is a textbook method, chosen because it needs no assumption
that durations or failures are normally distributed.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Z95 = 1.959963984540054  # two-sided 95% normal quantile


def wilson_interval(failures: int, runs: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Unlike the plain normal approximation it stays sensible near 0 and 1, which
    is where flaky-test failure rates live.
    """
    if runs == 0:
        return 0.0, 1.0
    p = failures / runs
    denom = 1 + z * z / runs
    centre = (p + z * z / (2 * runs)) / denom
    half = z * math.sqrt(p * (1 - p) / runs + z * z / (4 * runs * runs)) / denom
    # At the extremes the exact bound is 0 or 1; say so rather than return
    # floating-point residue.
    low = 0.0 if failures == 0 else max(0.0, centre - half)
    high = 1.0 if failures == runs else min(1.0, centre + half)
    return low, high


def runs_to_observe(rate: float, confidence: float = 0.95) -> int:
    """Runs needed to see at least one failure with the given confidence."""
    if not 0 < rate < 1:
        raise ValueError("rate must be strictly between 0 and 1")
    return math.ceil(math.log(1 - confidence) / math.log(1 - rate))


def median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        raise ValueError("median of empty sequence")
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def average_ranks(values: Sequence[float]) -> list[float]:
    """1-based ranks; tied values share the mean of the ranks they span."""
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = rank
        i = j + 1
    return ranks


def _normal_two_sided_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


def mann_whitney(a: Sequence[float], b: Sequence[float]) -> tuple[float, float]:
    """Mann-Whitney U test with tie correction and normal approximation.

    Returns (p, effect). `effect` is the probability that a value drawn from
    `a` is larger than one drawn from `b`, with ties counted as half: 0.5 means
    no difference, values near 0 mean `a` tends to be smaller.
    """
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        return 1.0, 0.5
    ranks = average_ranks(list(a) + list(b))
    u1 = sum(ranks[:n1]) - n1 * (n1 + 1) / 2
    n = n1 + n2
    tie_term = 0.0
    counts: dict[float, int] = {}
    for v in list(a) + list(b):
        counts[v] = counts.get(v, 0) + 1
    for t in counts.values():
        tie_term += t**3 - t
    variance = n1 * n2 / 12 * ((n + 1) - tie_term / (n * (n - 1)))
    effect = u1 / (n1 * n2)
    if variance <= 0:
        return 1.0, effect
    mean = n1 * n2 / 2
    # Continuity correction.
    z = (u1 - mean - math.copysign(0.5, u1 - mean)) / math.sqrt(variance) if u1 != mean else 0.0
    return _normal_two_sided_p(z), effect


def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact test for the table [[a, b], [c, d]].

    Sums the probabilities of all tables with the same margins that are no more
    likely than the observed one.
    """
    row1, row2, col1 = a + b, c + d, a + c
    n = row1 + row2
    if n == 0:
        return 1.0
    total = math.comb(n, col1)

    def prob(x: int) -> float:
        return math.comb(row1, x) * math.comb(row2, col1 - x) / total

    observed = prob(a)
    lo, hi = max(0, col1 - row2), min(col1, row1)
    p = sum(prob(x) for x in range(lo, hi + 1) if prob(x) <= observed * (1 + 1e-9))
    return min(1.0, p)


def pettitt(values: Sequence[float]) -> tuple[int, float]:
    """Pettitt's test for a single change point in a series.

    Returns (index, p). `index` is the position of the first value after the
    change, so values[:index] and values[index:] are the two segments. The p
    value uses Pettitt's approximation and is for the hypothesis that the series
    has no change point.
    """
    n = len(values)
    if n < 2:
        return 0, 1.0
    ranks = average_ranks(values)
    best_k, best_u = 0, 0.0
    running = 0.0
    for t in range(1, n):
        running += ranks[t - 1]
        u = 2 * running - t * (n + 1)
        if abs(u) > abs(best_u):
            best_k, best_u = t, u
    p = 2 * math.exp(-6 * best_u * best_u / (n**3 + n**2))
    return best_k, min(1.0, p)
