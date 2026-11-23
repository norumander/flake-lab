"""Unit tests for analysis.py, on synthetic runs written through the real store."""

import pathlib
import tempfile
import unittest

from flakelab import analysis, db
from flakelab.config import Expectation
from flakelab.results import CaseResult

SUITE = "pkg/suite"


class Fixture:
    """Writes sessions, boots and single-case runs into a temporary database."""

    def __init__(self, test: unittest.TestCase):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.store = db.Store(pathlib.Path(tmp.name) / "t.sqlite")
        test.addCleanup(self.store.close)
        self.session_id = 0
        self.boot_id = 0
        self.seq = 0

    def session(self, label: str) -> None:
        self.session_id = self.store.start_session(
            args={}, label=label, sdk_version="test", product_bundle="test"
        )
        self.boot()

    def boot(self, boot_ms: int = 8000, uptime: int | None = None, net: int | None = None):
        self.boot_id = self.store.add_boot(
            session_id=self.session_id, instance="t", started_at=db.now(), ok=1,
            boot_ms=boot_ms, guest_uptime_ms=uptime, guest_net_ms=net,
        )
        self.seq = 0

    def run(self, case: str, passed: bool = True, duration: int = 10, load: float = 0.5):
        outcome = "PASSED" if passed else "FAILED"
        self.store.add_run(
            [CaseResult(case, outcome, 0, duration)],
            session_id=self.session_id, boot_id=self.boot_id, iteration=self.seq,
            seq_in_boot=self.seq, suite=SUITE, started_at=db.now(), outcome=outcome,
            wall_ms=duration + 50, host_load1=load,
        )
        self.seq += 1

    def report(self, expect=None) -> analysis.Report:
        return analysis.build_report(self.store.conn, expect)

    def case(self, name: str) -> analysis.CaseReport:
        return next(c for c in self.report().cases if c.name == name)


class VerdictTest(unittest.TestCase):
    def test_stable_case(self):
        f = Fixture(self)
        f.session("baseline")
        for _ in range(100):
            f.run("A.stable")
        c = f.case("A.stable")
        self.assertEqual((c.verdict, c.failures, c.runs), (analysis.STABLE, 0, 100))
        self.assertEqual(c.ci_low, 0.0)
        self.assertGreater(c.ci_high, 0.0)
        self.assertIsNone(c.runs_to_first_failure)
        self.assertEqual(c.hints, [])

    def test_flaky_case_counts_first_failure_and_retry_masking(self):
        f = Fixture(self)
        f.session("baseline")
        # Fails on runs 5, 6 and 20 of 30: one failure is followed by a failure.
        for i in range(1, 31):
            f.run("A.flaky", passed=i not in (5, 6, 20))
        c = f.case("A.flaky")
        self.assertEqual((c.verdict, c.failures), (analysis.FLAKY, 3))
        self.assertEqual(c.runs_to_first_failure, 5)
        self.assertAlmostEqual(c.masked_by_retry, 2 / 3)

    def test_always_failing_case_is_broken_not_flaky(self):
        f = Fixture(self)
        f.session("baseline")
        for _ in range(10):
            f.run("A.broken", passed=False)
        c = f.case("A.broken")
        self.assertEqual(c.verdict, analysis.BROKEN)
        self.assertEqual(c.ci_high, 1.0)


class HintTest(unittest.TestCase):
    def test_failures_right_after_boot_are_attributed_to_boot(self):
        f = Fixture(self)
        f.session("baseline")
        for _ in range(10):
            f.boot()
            for position in range(20):
                f.run("A.boot", passed=position >= 2)
        c = f.case("A.boot")
        self.assertEqual(c.hint_kinds, ["boot"])
        self.assertIn("early after boot", c.hints[0])

    def test_evenly_spread_failures_get_no_hint(self):
        f = Fixture(self)
        f.session("baseline")
        for _ in range(10):
            f.boot()
            for position in range(20):
                f.run("A.even", passed=position % 10 != 5)
        self.assertEqual(f.case("A.even").hint_kinds, [])

    def test_rate_change_between_conditions_is_reported(self):
        f = Fixture(self)
        f.session("baseline")
        for i in range(200):
            f.run("A.load", passed=i % 100 != 0, load=0.5)
        f.session("stress")
        for i in range(200):
            f.run("A.load", passed=i % 5 != 0, load=40.0)
        c = f.case("A.load")
        # The condition explains it, so the weaker host-load hint is not added.
        self.assertEqual(c.hint_kinds, ["condition:stress"])
        self.assertEqual(c.rate_by_label, {"baseline": (2, 200), "stress": (40, 200)})

    def test_load_hint_within_one_condition(self):
        f = Fixture(self)
        f.session("baseline")
        for i in range(300):
            busy = i % 3 == 0
            f.run("A.busy", passed=not (busy and i % 6 == 0), load=30.0 if busy else 0.5)
        self.assertEqual(f.case("A.busy").hint_kinds, ["load"])


class DurationTest(unittest.TestCase):
    def test_step_change_is_located(self):
        f = Fixture(self)
        f.session("baseline")
        for i in range(100):
            f.run("P.hash", duration=44 + i % 3)
        f.session("regression")
        for i in range(100):
            f.run("P.hash", duration=66 + i % 3)
        (step,) = f.case("P.hash").steps
        self.assertEqual((step.label, step.before_ms, step.after_ms), ("regression", 45, 67))
        self.assertEqual(step.run_id, 101)
        self.assertAlmostEqual(step.relative, 22 / 45)

    def test_two_steps_are_both_located(self):
        f = Fixture(self)
        for label, base in (("baseline", 44), ("regression", 66), ("stress", 120)):
            f.session(label)
            for i in range(100):
                f.run("P.hash", duration=base + i % 3)
        steps = f.case("P.hash").steps
        self.assertEqual([(s.label, s.run_id) for s in steps],
                         [("regression", 101), ("stress", 201)])
        self.assertEqual([(s.before_ms, s.after_ms) for s in steps], [(45, 67), (67, 121)])

    def test_step_found_just_before_a_session_start_is_reported_at_the_start(self):
        f = Fixture(self)
        f.session("baseline")
        for i in range(100):
            # The last two baseline runs are already slow, as noise can make them.
            f.run("P.hash", duration=(66 if i >= 98 else 44) + i % 3)
        f.session("regression")
        for i in range(100):
            f.run("P.hash", duration=66 + i % 3)
        (step,) = f.case("P.hash").steps
        self.assertEqual((step.label, step.run_id), ("regression", 101))

    def test_slow_runs_are_judged_within_their_own_stretch(self):
        f = Fixture(self)
        f.session("baseline")
        for i in range(100):
            f.run("P.hash", duration=20 + i % 3)
        f.session("busy")
        for i in range(100):
            f.run("P.hash", duration=400 if i == 50 else 100 + i % 3)
        c = f.case("P.hash")
        self.assertEqual(len(c.steps), 1)
        # Only the 400 ms run stands out; the other busy runs are normal for busy.
        self.assertEqual((c.outliers, c.worst_outlier_ms), (1, 400))

    def test_small_shift_is_ignored(self):
        f = Fixture(self)
        f.session("baseline")
        for i in range(200):
            f.run("P.flat", duration=(50 if i < 100 else 51) + i % 2)
        self.assertEqual(f.case("P.flat").steps, [])

    def test_outliers_are_counted_among_passing_runs(self):
        f = Fixture(self)
        f.session("baseline")
        for i in range(100):
            f.run("P.spiky", duration=90 if i in (10, 60) else 20 + i % 3)
        c = f.case("P.spiky")
        self.assertEqual((c.outliers, c.worst_outlier_ms), (2, 90))
        self.assertEqual(c.steps, [])


class BootTest(unittest.TestCase):
    def test_split_and_slow_boots_per_condition(self):
        f = Fixture(self)
        f.session("baseline")  # creates one boot without guest timestamps
        for ms in (8000, 8010, 7990, 8005, 14800, 7995):
            f.boot(boot_ms=ms, uptime=ms - 800, net=1500)
        f.session("busy")
        for ms in (16000, 16400):
            f.boot(boot_ms=ms, uptime=ms - 2500, net=3800)
        baseline, busy = analysis.analyse_boots(f.store.conn).groups
        self.assertEqual((baseline.label, baseline.boots), ("baseline", 7))
        self.assertEqual([ms for _, ms in baseline.slow], [14800])
        self.assertEqual((baseline.launch_ms, baseline.guest_net_ms), (800, 1500))
        self.assertEqual(baseline.host_wait_ms, (8005 + 8000) / 2 - 800 - 1500)
        # The busy condition is judged against the baseline median. The fixture
        # adds one 8 s boot per session, so two of its three boots are slow.
        self.assertEqual((busy.label, busy.boots, len(busy.slow)), ("busy", 3, 2))
        self.assertEqual(busy.launch_ms, 2500)


class EvaluationTest(unittest.TestCase):
    def test_scores_verdicts_against_expectations(self):
        f = Fixture(self)
        f.session("baseline")
        for i in range(100):
            f.run("E.injected", passed=i % 5 != 0)  # 20%, expected 20%
            f.run("E.missed", passed=True)  # expected flaky, never failed
            f.run("E.surprise", passed=i != 50)  # expected stable, failed once
            f.run("E.control", passed=True)
        expect = {
            "E.injected": Expectation(flaky=True, rate=0.2),
            "E.missed": Expectation(flaky=True),
        }
        e = f.report(expect).evaluation
        self.assertEqual((e.true_positives, e.true_negatives), (1, 1))
        self.assertEqual(e.false_positives, ["E.surprise"])
        self.assertEqual(e.false_negatives, ["E.missed"])
        self.assertEqual((e.rates_checked, e.rates_covered), (1, 1))


if __name__ == "__main__":
    unittest.main()
