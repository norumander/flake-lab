"""Plain-text rendering of an analysis.Report."""

from __future__ import annotations

from . import stats
from .analysis import FLAKY, STABLE, CaseReport, Report


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _expected(case: CaseReport) -> str:
    e = case.expected
    if e is None:
        return ""
    if not e.flaky:
        return "stable"
    return f"flaky, p={e.rate:g}" if e.rate is not None else "flaky"


def render(report: Report) -> str:
    out: list[str] = []
    add = out.append

    labels = ", ".join(f"{sid} ({label or 'no label'})" for sid, label, _, _ in report.sessions)
    add(f"Sessions: {labels}")
    lab_errors = ", ".join(f"{k} x{v}" for k, v in report.infra_errors.items()) or "none"
    add(f"Suite runs: {report.suite_runs}   runs without a result: {lab_errors}")

    for g in report.boots.groups:
        add("")
        add(f"Emulator boots under '{g.label or 'no label'}': {g.boots} ({g.failed} failed)")
        if g.median_ms is not None:
            add(f"  ffx emu start: median {g.median_ms / 1000:.2f} s, "
                f"min {g.min_ms / 1000:.2f} s, max {g.max_ms / 1000:.2f} s")
        if g.launch_ms is not None:
            add(f"  median split: {g.launch_ms:.0f} ms before the guest kernel starts, "
                f"{g.guest_net_ms:.0f} ms guest boot to network up, "
                f"{g.host_wait_ms:.0f} ms until ffx reports ready")
        if g.slow:
            worst = max(ms for _, ms in g.slow)
            add(f"  slow boots (over 1.25x the median of the first condition): {len(g.slow)} of "
                f"{g.boots - g.failed}, slowest {worst / 1000:.2f} s")

    flaky = [c for c in report.cases if c.verdict != STABLE]
    stable = [c for c in report.cases if c.verdict == STABLE]

    add("")
    add(f"Flaky or failing test cases: {len(flaky)}")
    if flaky:
        add(f"  {'case':38} {'runs':>5} {'fail':>5} {'rate':>6}  {'95% interval':15} "
            f"{'1st fail':>8} {'retry hides':>11}  expected")
        for c in sorted(flaky, key=lambda c: -c.rate):
            masked = _pct(c.masked_by_retry) if c.masked_by_retry is not None else "-"
            interval = f"{_pct(c.ci_low)} - {_pct(c.ci_high)}"
            note = "" if c.verdict == FLAKY else "  [always fails]"
            add(f"  {c.name:38} {c.runs:5} {c.failures:5} {_pct(c.rate):>6}  {interval:15} "
                f"{c.runs_to_first_failure:8} {masked:>11}  {_expected(c)}{note}")
            for hint in c.hints:
                add(f"      -> {hint}")

    add("")
    add(f"Test cases with no failures: {len(stable)}")
    if stable:
        add(f"  {'case':46} {'runs':>5}  failure rate is below (95%)")
        for c in stable:
            flag = "   <-- expected flaky" if c.expected and c.expected.flaky else ""
            add(f"  {c.name:46} {c.runs:5}  {_pct(c.ci_high)}{flag}")

    steps = [(c, s) for c in report.cases for s in c.steps]
    noisy = [c for c in report.cases if c.outliers]
    add("")
    add(f"Duration step changes: {len(steps)}")
    for c, s in steps:
        add(f"  {c.name}: {s.before_ms:.0f} ms -> {s.after_ms:.0f} ms ({s.relative:+.0%}) "
            f"from run {s.run_id}, session {s.session_id} ({s.label or 'no label'}), p={s.p:.1e}")
    add(f"Cases with very slow runs: {len(noisy)}")
    for c in sorted(noisy, key=lambda c: -c.outliers):
        add(f"  {c.name}: {c.outliers} of {c.runs - c.failures} passing runs took over "
            f"3x the median of {c.median_ms:.0f} ms; slowest {c.worst_outlier_ms} ms")

    e = report.evaluation
    if e is not None:
        add("")
        add("Detector against the expectations in lab.toml")
        add(f"  flaky and flagged: {e.true_positives}   stable and not flagged: {e.true_negatives}")
        add(f"  flagged but expected stable: {len(e.false_positives)} "
            f"{', '.join(e.false_positives)}".rstrip())
        add(f"  expected flaky but not flagged: {len(e.false_negatives)} "
            f"{', '.join(e.false_negatives)}".rstrip())
        add(f"  injected rate inside the 95% interval: {e.rates_covered} of {e.rates_checked}")
        add(f"  expected cause found: {e.causes_found} of {e.causes_checked}")
        for c in report.cases:
            if c.expected and c.expected.rate is not None:
                need = stats.runs_to_observe(c.expected.rate)
                seen = c.runs_to_first_failure
                add(f"    {c.name}: 95% chance of a first failure within {need} runs; "
                    f"first seen at run {seen if seen is not None else 'never'}")
    return "\n".join(out)
