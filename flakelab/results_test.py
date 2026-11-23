"""Unit tests for results.py. Run with `python3 -m unittest discover -p '*_test.py'`."""

import json
import unittest

from flakelab import results


def _summary(cases, *, outcome="PASSED", start=1000, duration=100, suites=1):
    suite = {
        "name": "fuchsia-pkg://repo/pkg#meta/t.cm",
        "outcome": outcome,
        "start_time": start,
        "duration_milliseconds": duration,
        "cases": cases,
    }
    return json.dumps({"data": {"outcome": outcome, "suites": [suite] * suites}})


class ParseSummaryTest(unittest.TestCase):
    def test_case_durations_are_gaps_between_starts(self):
        parsed = results.parse_summary(
            _summary(
                [
                    {"name": "A.first", "outcome": "PASSED", "start_time": 1000},
                    {"name": "A.second", "outcome": "FAILED", "start_time": 1056},
                ],
                outcome="FAILED",
                duration=65,
            )
        )
        self.assertEqual(parsed.outcome, "FAILED")
        self.assertEqual(parsed.duration_ms, 65)
        self.assertEqual(
            parsed.cases,
            (
                results.CaseResult("A.first", "PASSED", 0, 56),
                results.CaseResult("A.second", "FAILED", 56, 9),
            ),
        )

    def test_cases_are_ordered_by_start_time(self):
        parsed = results.parse_summary(
            _summary(
                [
                    {"name": "late", "outcome": "PASSED", "start_time": 1040},
                    {"name": "early", "outcome": "PASSED", "start_time": 1010},
                ]
            )
        )
        self.assertEqual([c.name for c in parsed.cases], ["early", "late"])
        self.assertEqual([c.duration_ms for c in parsed.cases], [30, 60])

    def test_missing_times_give_none_not_zero(self):
        text = json.dumps(
            {
                "data": {
                    "suites": [
                        {
                            "name": "s",
                            "outcome": "TIMEDOUT",
                            "cases": [{"name": "c", "outcome": "TIMEDOUT"}],
                        }
                    ]
                }
            }
        )
        parsed = results.parse_summary(text)
        self.assertIsNone(parsed.duration_ms)
        self.assertEqual(parsed.cases, (results.CaseResult("c", "TIMEDOUT", None, None),))

    def test_rejects_anything_but_one_suite(self):
        with self.assertRaises(ValueError):
            results.parse_summary(_summary([], suites=2))
        with self.assertRaises(ValueError):
            results.parse_summary(json.dumps({"data": {"suites": []}}))


class ParseHostSummaryTest(unittest.TestCase):
    def _cases(self, *cases):
        return json.dumps({"cases": [
            {"name": n, "outcome": o, "start_ms": start, "end_ms": end, "details": None}
            for n, o, start, end in cases
        ]})

    def test_durations_are_measured_and_offsets_relative(self):
        parsed = results.parse_host_summary(self._cases(
            ("T.second", "PASSED", 1040, 1100),
            ("T.first", "PASSED", 1000, 1030),
        ))
        self.assertEqual(parsed.outcome, "PASSED")
        self.assertEqual(parsed.duration_ms, 100)
        self.assertEqual(parsed.cases, (
            results.CaseResult("T.first", "PASSED", 0, 30),
            results.CaseResult("T.second", "PASSED", 40, 60),
        ))

    def test_any_case_that_did_not_pass_fails_the_suite(self):
        for outcome in ("FAILED", "ERROR", "SKIPPED"):
            parsed = results.parse_host_summary(self._cases(
                ("T.a", "PASSED", 0, 10), ("T.b", outcome, 10, 20),
            ))
            self.assertEqual(parsed.outcome, "FAILED")

    def test_no_cases_is_not_a_result(self):
        with self.assertRaises(ValueError):
            results.parse_host_summary(json.dumps({"cases": []}))


if __name__ == "__main__":
    unittest.main()
