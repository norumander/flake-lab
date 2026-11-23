"""Parse result files: ffx's run_summary.json and the host suites' summary.json."""

from __future__ import annotations

import dataclasses
import json
import pathlib

# Outcomes the lab assigns itself when ffx produced no usable summary. They are
# kept apart from test outcomes so that infrastructure trouble is never counted
# as a test failure.
INFRA_ERROR = "INFRA_ERROR"
RUNNER_TIMEOUT = "RUNNER_TIMEOUT"

PASSED = "PASSED"


@dataclasses.dataclass(frozen=True)
class CaseResult:
    name: str
    outcome: str
    start_offset_ms: int | None  # from suite start
    # ffx reports a start time per case but no duration. This is the gap to the
    # start of the next case, or to the end of the suite for the last case. It
    # is only meaningful because the runner executes cases one at a time.
    duration_ms: int | None


@dataclasses.dataclass(frozen=True)
class SuiteResult:
    outcome: str
    duration_ms: int | None
    cases: tuple[CaseResult, ...]


def parse_summary(text: str) -> SuiteResult:
    data = json.loads(text)["data"]
    suites = data.get("suites", [])
    if len(suites) != 1:
        raise ValueError(f"expected exactly one suite, got {len(suites)}")
    suite = suites[0]
    start = suite.get("start_time")
    duration = suite.get("duration_milliseconds")
    end = start + duration if start is not None and duration is not None else None

    raw_cases = sorted(
        suite.get("cases", []),
        key=lambda c: (c.get("start_time") is None, c.get("start_time") or 0),
    )
    cases = []
    for i, case in enumerate(raw_cases):
        case_start = case.get("start_time")
        next_start = raw_cases[i + 1].get("start_time") if i + 1 < len(raw_cases) else end
        offset = case_start - start if case_start is not None and start is not None else None
        case_duration = (
            max(0, next_start - case_start)
            if case_start is not None and next_start is not None
            else None
        )
        cases.append(CaseResult(case["name"], case["outcome"], offset, case_duration))
    return SuiteResult(suite["outcome"], duration, tuple(cases))


def load_summary(output_dir: pathlib.Path) -> SuiteResult:
    return parse_summary((output_dir / "run_summary.json").read_text())


def parse_host_summary(text: str) -> SuiteResult:
    """Parses the summary.json written by host_tests/run.py.

    Mobly records a begin and an end time per test, so these durations are
    measured rather than derived.
    """
    cases = sorted(json.loads(text)["cases"], key=lambda c: c["start_ms"])
    if not cases:
        raise ValueError("host suite reported no test cases")
    first = cases[0]["start_ms"]
    parsed = tuple(
        CaseResult(c["name"], c["outcome"], c["start_ms"] - first, c["end_ms"] - c["start_ms"])
        for c in cases
    )
    outcome = PASSED if all(c.outcome == PASSED for c in parsed) else "FAILED"
    return SuiteResult(outcome, max(c["end_ms"] for c in cases) - first, parsed)


def load_host_summary(output_dir: pathlib.Path) -> SuiteResult:
    return parse_host_summary((output_dir / "summary.json").read_text())
