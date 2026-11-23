"""Turns recorded runs into verdicts: which tests are flaky, why, and what got slower.

Three questions are answered per test case:

1. Is it flaky, and how often does it fail? A case with both passes and
   failures on the same build is flaky. The failure rate is reported with a
   Wilson 95% interval.
2. Is there a pattern in when it fails? Position since the last emulator boot,
   host load and the session's condition are each tested for association with
   failure. These are hints for a person to follow up, not proofs.
3. Did its duration change? Runs several times slower than the median of
   their own stretch between steps are counted as outliers.
   Step changes are looked for with Pettitt's test, applied again to each side
   of a change it finds so that more than one step can be reported.

Thresholds are deliberately strict (p < 0.01 plus a minimum effect size)
because many cases and several hints are tested at once.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from collections import defaultdict

from . import stats
from .config import Expectation
from .results import PASSED

P_THRESHOLD = 0.01
# Mann-Whitney effect (probability a failing run ranks above a passing one)
# further than this from 0.5 counts as a real difference.
EFFECT_MARGIN = 0.15
# Durations are right-skewed, so an outlier is defined by a plain rule rather
# than a z-score: this many times the case's median, and at least this many
# milliseconds above it.
OUTLIER_FACTOR = 3.0
OUTLIER_MIN_MS = 20
SLOW_BOOT_FACTOR = 1.25  # a boot this many times the median counts as slow
STEP_P_THRESHOLD = 0.001
STEP_MIN_RELATIVE = 0.10
STEP_MIN_MS = 2
STEP_MIN_SEGMENT = 20  # passing runs on each side of a step
STEP_MAX_DEPTH = 2  # at most 2**2 - 1 = 3 steps per case
# Pettitt's test places a step to within a run or two in noisy data. A step
# found this close to the start of a session is reported at that start,
# because a change of condition is the likelier cause.
STEP_SNAP_RUNS = 3

STABLE, FLAKY, BROKEN = "stable", "flaky", "broken"


@dataclasses.dataclass(frozen=True)
class Sample:
    run_id: int
    session_id: int
    label: str
    boot_id: int
    seq_in_boot: int
    outcome: str
    duration_ms: int | None
    host_load1: float | None

    @property
    def passed(self) -> bool:
        return self.outcome == PASSED


@dataclasses.dataclass(frozen=True)
class Step:
    run_id: int  # first run after the change
    session_id: int
    label: str
    before_ms: float
    after_ms: float
    p: float

    @property
    def relative(self) -> float:
        return (self.after_ms - self.before_ms) / self.before_ms if self.before_ms else 0.0


@dataclasses.dataclass
class CaseReport:
    name: str
    suite: str
    runs: int
    failures: int
    rate: float
    ci_low: float
    ci_high: float
    verdict: str
    runs_to_first_failure: int | None = None
    # Share of failures that were followed by a pass on the very next run, which
    # is what a retry-once policy would have reported as a pass.
    masked_by_retry: float | None = None
    hints: list[str] = dataclasses.field(default_factory=list)
    hint_kinds: list[str] = dataclasses.field(default_factory=list)
    rate_by_label: dict[str, tuple[int, int]] = dataclasses.field(default_factory=dict)
    median_ms: float | None = None
    outliers: int = 0
    worst_outlier_ms: int | None = None
    steps: list[Step] = dataclasses.field(default_factory=list)
    expected: Expectation | None = None

    @property
    def flagged_flaky(self) -> bool:
        return self.verdict in (FLAKY, BROKEN)


@dataclasses.dataclass(frozen=True)
class BootGroup:
    """Emulator starts under one session label."""

    label: str
    boots: int
    failed: int
    median_ms: float | None
    min_ms: int | None
    max_ms: int | None
    # Medians, in milliseconds, for boots that have guest-side timestamps.
    launch_ms: float | None  # host: before the guest kernel started
    guest_net_ms: float | None  # guest: kernel start to network up
    host_wait_ms: float | None  # guest reachable, ffx not yet reporting ready
    # Boots that took more than SLOW_BOOT_FACTOR times the median of the first
    # group, as (boot id, boot_ms).
    slow: list[tuple[int, int]]


@dataclasses.dataclass(frozen=True)
class BootReport:
    groups: list[BootGroup]  # in the order the labels first appear

    @property
    def boots(self) -> int:
        return sum(g.boots for g in self.groups)


@dataclasses.dataclass(frozen=True)
class Evaluation:
    true_positives: int
    false_positives: list[str]
    false_negatives: list[str]
    true_negatives: int
    rates_checked: int
    rates_covered: int  # expected rate inside the 95% interval
    causes_checked: int
    causes_found: int


@dataclasses.dataclass(frozen=True)
class Report:
    sessions: list[tuple[int, str, str, str | None]]  # id, label, started, ended
    suite_runs: int
    infra_errors: dict[str, int]  # outcome -> count, lab-assigned outcomes only
    cases: list[CaseReport]
    boots: BootReport
    evaluation: Evaluation | None


def _where(session_ids: list[int] | None, column: str) -> tuple[str, tuple]:
    if not session_ids:
        return "", ()
    return f"WHERE {column} IN ({', '.join('?' for _ in session_ids)})", tuple(session_ids)


def load_samples(
    conn: sqlite3.Connection, session_ids: list[int] | None = None
) -> dict[tuple[str, str], list[Sample]]:
    """Returns samples per (suite, case name), in the order the runs happened."""
    where, params = _where(session_ids, "r.session_id")
    rows = conn.execute(
        f"""
        SELECT r.suite, t.name, r.id, r.session_id, COALESCE(s.label, ''), r.boot_id,
               r.seq_in_boot,
               t.outcome, t.duration_ms, r.host_load1
        FROM test_case t
        JOIN run r ON r.id = t.run_id
        JOIN session s ON s.id = r.session_id
        {where}
        ORDER BY r.id
        """,
        params,
    )
    samples: dict[tuple[str, str], list[Sample]] = defaultdict(list)
    for suite, name, *rest in rows:
        samples[(suite, name)].append(Sample(*rest))
    return samples


def _hints(samples: list[Sample]) -> tuple[list[str], list[str], dict[str, tuple[int, int]]]:
    """Looks for patterns in when a flaky case fails."""
    hints: list[str] = []
    kinds: list[str] = []
    failing = [s for s in samples if not s.passed]
    passing = [s for s in samples if s.passed]

    # Position since boot.
    p, effect = stats.mann_whitney(
        [s.seq_in_boot for s in failing], [s.seq_in_boot for s in passing]
    )
    if p < P_THRESHOLD and abs(effect - 0.5) > EFFECT_MARGIN:
        where = "early" if effect < 0.5 else "late"
        hints.append(
            f"fails {where} after boot: median position "
            f"{stats.median([s.seq_in_boot for s in failing]):.0f} for failures vs "
            f"{stats.median([s.seq_in_boot for s in passing]):.0f} for passes (p={p:.1e})"
        )
        kinds.append("boot")

    # Session condition, compared pairwise against the first label seen.
    by_label: dict[str, tuple[int, int]] = {}
    for s in samples:
        failed, total = by_label.get(s.label, (0, 0))
        by_label[s.label] = (failed + (not s.passed), total + 1)
    labels = list(by_label)
    condition_explains = False
    if len(labels) > 1:
        base_failed, base_total = by_label[labels[0]]
        for label in labels[1:]:
            failed, total = by_label[label]
            p = stats.fisher_exact(base_failed, base_total - base_failed, failed, total - failed)
            if p < P_THRESHOLD:
                hints.append(
                    f"rate differs by condition: {base_failed}/{base_total} under "
                    f"'{labels[0]}' vs {failed}/{total} under '{label}' (p={p:.1e})"
                )
                kinds.append(f"condition:{label}")
                condition_explains = True

    # Host load. Skipped when a condition already explains the difference,
    # because a stressed session trivially has both more load and more failures.
    if not condition_explains:
        fail_load = [s.host_load1 for s in failing if s.host_load1 is not None]
        pass_load = [s.host_load1 for s in passing if s.host_load1 is not None]
        p, effect = stats.mann_whitney(fail_load, pass_load)
        if p < P_THRESHOLD and effect - 0.5 > EFFECT_MARGIN:
            hints.append(
                f"fails under higher host load: median {stats.median(fail_load):.1f} for "
                f"failures vs {stats.median(pass_load):.1f} for passes (p={p:.1e})"
            )
            kinds.append("load")
    return hints, kinds, by_label


def _find_steps(samples: list[Sample]) -> list[Step]:
    """Finds step changes in the durations of passing runs, in run order."""
    series = [s for s in samples if s.passed and s.duration_ms is not None]
    values = [float(s.duration_ms) for s in series]
    cuts: dict[int, float] = {}  # index of first run after a step -> p value

    def split(lo: int, hi: int, depth: int) -> None:
        if depth >= STEP_MAX_DEPTH or hi - lo < 2 * STEP_MIN_SEGMENT:
            return
        offset, p = stats.pettitt(values[lo:hi])
        index = lo + offset
        if p >= STEP_P_THRESHOLD or min(index - lo, hi - index) < STEP_MIN_SEGMENT:
            return
        before, after = stats.median(values[lo:index]), stats.median(values[index:hi])
        if abs(after - before) < max(STEP_MIN_MS, STEP_MIN_RELATIVE * before):
            return
        cuts[index] = p
        split(lo, index, depth + 1)
        split(index, hi, depth + 1)

    split(0, len(values), 0)
    bounds = [0, *sorted(cuts), len(values)]
    # A cut found while a second step was still inside the window can be off by
    # a run or two. Locate each one again between its two neighbouring cuts,
    # where it is the only step.
    for i in range(1, len(bounds) - 1):
        lo, hi = bounds[i - 1], bounds[i + 1]
        offset, p_value = stats.pettitt(values[lo:hi])
        if 0 < offset < hi - lo:
            cuts[lo + offset] = min(p_value, cuts.pop(bounds[i]))
            bounds[i] = lo + offset
    session_starts = [
        i for i in range(1, len(series)) if series[i].session_id != series[i - 1].session_id
    ]
    for i in range(1, len(bounds) - 1):
        near = [s for s in session_starts if abs(s - bounds[i]) <= STEP_SNAP_RUNS]
        if near:
            snapped = min(near, key=lambda s: abs(s - bounds[i]))
            cuts[snapped] = cuts.pop(bounds[i])
            bounds[i] = snapped
    steps = []
    for i in range(1, len(bounds) - 1):
        index = bounds[i]
        first = series[index]
        steps.append(
            Step(
                first.run_id,
                first.session_id,
                first.label,
                stats.median(values[bounds[i - 1] : index]),
                stats.median(values[index : bounds[i + 1]]),
                cuts[index],
            )
        )
    return steps


def analyse_case(suite: str, name: str, samples: list[Sample]) -> CaseReport:
    runs = len(samples)
    failures = sum(1 for s in samples if not s.passed)
    low, high = stats.wilson_interval(failures, runs)
    verdict = STABLE if failures == 0 else BROKEN if failures == runs else FLAKY
    report = CaseReport(name, suite, runs, failures, failures / runs, low, high, verdict)

    if failures:
        report.runs_to_first_failure = next(i for i, s in enumerate(samples, 1) if not s.passed)
        followed = [
            samples[i + 1].passed for i, s in enumerate(samples[:-1]) if not s.passed
        ]
        if followed:
            report.masked_by_retry = sum(followed) / len(followed)
    if verdict == FLAKY:
        report.hints, report.hint_kinds, report.rate_by_label = _hints(samples)

    report.steps = _find_steps(samples)
    timed = [s for s in samples if s.passed and s.duration_ms is not None]
    if timed:
        report.median_ms = stats.median([s.duration_ms for s in timed])
        # A run is an outlier relative to its own stretch between steps, so that
        # a test which got slower is not reported as hundreds of outliers too.
        starts = [step.run_id for step in report.steps]
        stretches: list[list[int]] = [[] for _ in range(len(starts) + 1)]
        for s in timed:
            stretches[sum(s.run_id >= start for start in starts)].append(s.duration_ms)
        extreme: list[int] = []
        for stretch in stretches:
            if not stretch:
                continue
            centre = stats.median(stretch)
            extreme += [
                d for d in stretch if d > OUTLIER_FACTOR * centre and d - centre >= OUTLIER_MIN_MS
            ]
        report.outliers = len(extreme)
        if extreme:
            report.worst_outlier_ms = max(extreme)
    return report


def analyse_boots(conn: sqlite3.Connection, session_ids: list[int] | None = None) -> BootReport:
    where, params = _where(session_ids, "b.session_id")
    rows = conn.execute(
        f"""
        SELECT COALESCE(s.label, ''), b.id, b.ok, b.boot_ms, b.guest_uptime_ms, b.guest_net_ms
        FROM boot b JOIN session s ON s.id = b.session_id {where} ORDER BY b.id
        """,
        params,
    ).fetchall()
    by_label: dict[str, list[tuple]] = {}
    for label, *rest in rows:
        by_label.setdefault(label, []).append(tuple(rest))

    def med(values: list[float]) -> float | None:
        return stats.median(values) if values else None

    groups: list[BootGroup] = []
    reference: float | None = None
    for label, boots in by_label.items():
        good = [b for b in boots if b[1]]
        times = [b[2] for b in good]
        if reference is None and times:
            reference = stats.median(times)
        split = [b for b in good if b[3] is not None and b[4] is not None]
        groups.append(
            BootGroup(
                label=label,
                boots=len(boots),
                failed=len(boots) - len(good),
                median_ms=med(times),
                min_ms=min(times) if times else None,
                max_ms=max(times) if times else None,
                launch_ms=med([b[2] - b[3] for b in split]),
                guest_net_ms=med([b[4] for b in split]),
                host_wait_ms=med([b[3] - b[4] for b in split]),
                slow=[
                    (b[0], b[2])
                    for b in good
                    if reference is not None and b[2] > SLOW_BOOT_FACTOR * reference
                ],
            )
        )
    return BootReport(groups)


def evaluate(cases: list[CaseReport], expect: dict[str, Expectation]) -> Evaluation:
    """Scores the verdicts against what the test authors wrote down in advance."""
    tp = tn = 0
    fp: list[str] = []
    fn: list[str] = []
    rates_checked = rates_covered = causes_checked = causes_found = 0
    for case in cases:
        expected = expect.get(case.name, Expectation())
        case.expected = expected
        if expected.flaky and case.flagged_flaky:
            tp += 1
        elif expected.flaky:
            fn.append(case.name)
        elif case.flagged_flaky:
            fp.append(case.name)
        else:
            tn += 1
        if expected.rate is not None:
            rates_checked += 1
            rates_covered += case.ci_low <= expected.rate <= case.ci_high
        if expected.cause is not None:
            causes_checked += 1
            causes_found += any(k.split(":")[0] == expected.cause for k in case.hint_kinds)
    return Evaluation(tp, fp, fn, tn, rates_checked, rates_covered, causes_checked, causes_found)


def build_report(
    conn: sqlite3.Connection,
    expect: dict[str, Expectation] | None = None,
    session_ids: list[int] | None = None,
) -> Report:
    where, params = _where(session_ids, "id")
    sessions = conn.execute(
        f"SELECT id, COALESCE(label, ''), started_at, ended_at FROM session {where} ORDER BY id",
        params,
    ).fetchall()
    where, params = _where(session_ids, "session_id")
    suite_runs = conn.execute(f"SELECT COUNT(*) FROM run {where}", params).fetchone()[0]
    infra = dict(
        conn.execute(
            f"SELECT outcome, COUNT(*) FROM run {where} {'AND' if where else 'WHERE'}"
            " outcome IN ('INFRA_ERROR', 'RUNNER_TIMEOUT') GROUP BY outcome",
            params,
        ).fetchall()
    )
    cases = [
        analyse_case(suite, name, samples)
        for (suite, name), samples in sorted(load_samples(conn, session_ids).items())
    ]
    evaluation = evaluate(cases, expect) if expect is not None else None
    return Report(sessions, suite_runs, infra, cases, analyse_boots(conn, session_ids), evaluation)
