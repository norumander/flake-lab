"""Unit tests for stats.py, checked against values worked out independently."""

import math
import random
import unittest

from flakelab import stats


class WilsonTest(unittest.TestCase):
    def test_known_value(self):
        # 5 failures in 100: the Wilson 95% interval is (0.0215, 0.1118).
        lo, hi = stats.wilson_interval(5, 100)
        self.assertAlmostEqual(lo, 0.0215, places=3)
        self.assertAlmostEqual(hi, 0.1118, places=3)

    def test_zero_failures_has_positive_upper_bound(self):
        lo, hi = stats.wilson_interval(0, 400)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.0095, places=3)

    def test_no_runs(self):
        self.assertEqual(stats.wilson_interval(0, 0), (0.0, 1.0))


class RunsToObserveTest(unittest.TestCase):
    def test_values(self):
        self.assertEqual(stats.runs_to_observe(0.20), 14)
        self.assertEqual(stats.runs_to_observe(0.05), 59)
        self.assertEqual(stats.runs_to_observe(0.01), 299)

    def test_rejects_degenerate_rates(self):
        for rate in (0, 1, -0.1):
            with self.assertRaises(ValueError):
                stats.runs_to_observe(rate)


class MedianTest(unittest.TestCase):
    def test_odd_and_even_lengths(self):
        self.assertEqual(stats.median([3, 1, 2]), 2)
        self.assertEqual(stats.median([4, 1, 2, 3]), 2.5)

    def test_empty_is_an_error(self):
        with self.assertRaises(ValueError):
            stats.median([])


class RanksTest(unittest.TestCase):
    def test_ties_share_mean_rank(self):
        self.assertEqual(stats.average_ranks([10, 20, 10, 30]), [1.5, 3.0, 1.5, 4.0])


class MannWhitneyTest(unittest.TestCase):
    def test_separated_samples(self):
        p, effect = stats.mann_whitney([1, 2, 3, 4, 5, 6, 7, 8], [11, 12, 13, 14, 15, 16, 17, 18])
        self.assertLess(p, 0.001)
        self.assertEqual(effect, 0.0)

    def test_identical_samples(self):
        p, effect = stats.mann_whitney([1, 2, 3, 4], [1, 2, 3, 4])
        self.assertEqual(p, 1.0)
        self.assertEqual(effect, 0.5)

    def test_all_values_equal(self):
        self.assertEqual(stats.mann_whitney([7, 7, 7], [7, 7]), (1.0, 0.5))

    def test_empty_side(self):
        self.assertEqual(stats.mann_whitney([], [1, 2]), (1.0, 0.5))


class FisherTest(unittest.TestCase):
    def test_tea_tasting(self):
        # Fisher's own example: [[3, 1], [1, 3]] has two-sided p = 34/70.
        self.assertAlmostEqual(stats.fisher_exact(3, 1, 1, 3), 34 / 70)

    def test_strong_association(self):
        self.assertLess(stats.fisher_exact(20, 0, 0, 20), 1e-9)

    def test_no_association(self):
        self.assertAlmostEqual(stats.fisher_exact(10, 10, 10, 10), 1.0)


class PettittTest(unittest.TestCase):
    def test_finds_step(self):
        rng = random.Random(1)
        series = [rng.gauss(40, 2) for _ in range(120)] + [rng.gauss(60, 2) for _ in range(80)]
        index, p = stats.pettitt(series)
        self.assertEqual(index, 120)
        self.assertLess(p, 1e-6)

    def test_false_positive_rate_on_noise(self):
        # With no change point, p < 0.05 should happen about 5% of the time.
        hits = 0
        for seed in range(1, 41):
            rng = random.Random(seed)
            _, p = stats.pettitt([rng.gauss(40, 2) for _ in range(200)])
            hits += p < 0.05
        self.assertLessEqual(hits, 5)

    def test_short_series(self):
        self.assertEqual(stats.pettitt([1.0]), (0, 1.0))
        self.assertFalse(math.isnan(stats.pettitt([1.0, 2.0])[1]))


if __name__ == "__main__":
    unittest.main()
