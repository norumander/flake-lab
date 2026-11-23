"""Builds the static dashboard: one HTML file with the data embedded.

The page needs no server and no network. `build_payload` turns the database
into plain JSON; the template in dashboard_template.html draws it.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import sqlite3

from . import analysis, stats
from .config import Config

TEMPLATE = pathlib.Path(__file__).with_name("dashboard_template.html")
DATA_MARKER = "/*__FLAKELAB_DATA__*/null"

# Duration charts show a median per bin of runs rather than every run.
MAX_BINS_PER_CHART = 120


def _percentile(sorted_values: list[float], q: float) -> float:
    """Nearest-rank percentile of a sorted, non-empty list."""
    rank = max(1, -(-len(sorted_values) * q // 100))
    return sorted_values[int(rank) - 1]


def _segments(samples: list[analysis.Sample]) -> list[dict]:
    """Runs of consecutive samples that belong to the same session."""
    segments: list[dict] = []
    for i, s in enumerate(samples):
        if not segments or segments[-1]["session"] != s.session_id:
            segments.append({"session": s.session_id, "label": s.label, "start": i, "end": i + 1})
        else:
            segments[-1]["end"] = i + 1
    return segments


def _duration_bins(samples: list[analysis.Sample], segments: list[dict]) -> list[list[float]]:
    """[first run, last run, p10, median, p90] per bin; bins never span sessions."""
    size = max(1, -(-len(samples) // MAX_BINS_PER_CHART))
    bins = []
    for seg in segments:
        for start in range(seg["start"], seg["end"], size):
            end = min(start + size, seg["end"])
            values = sorted(
                s.duration_ms
                for s in samples[start:end]
                if s.passed and s.duration_ms is not None
            )
            if values:
                bins.append(
                    [start, end - 1, _percentile(values, 10), stats.median(values),
                     _percentile(values, 90)]
                )
    return bins


def build_payload(
    conn: sqlite3.Connection, cfg: Config, session_ids: list[int] | None = None
) -> dict:
    report = analysis.build_report(conn, cfg.expect, session_ids)
    samples = analysis.load_samples(conn, session_ids)

    # Every case runs once per iteration, so the position of a run within its
    # case's history is a shared x axis. The longest history defines it.
    reference = max(samples.values(), key=len) if samples else []
    segments = _segments(reference)
    boot_starts = [
        i for i, s in enumerate(reference) if i == 0 or s.boot_id != reference[i - 1].boot_id
    ]
    positions = [{"since_boot": s.seq_in_boot, "label": s.label} for s in reference]

    cases = []
    for c in report.cases:
        history = samples[(c.suite, c.name)]
        ordinal = {s.run_id: i for i, s in enumerate(history)}
        own_segments = _segments(history)
        session_starts = {seg["start"] for seg in own_segments}
        cases.append(
            {
                "name": c.name,
                "suite": c.suite,
                "verdict": c.verdict,
                "runs": c.runs,
                "failures": c.failures,
                "rate": c.rate,
                "ci": [c.ci_low, c.ci_high],
                "first_failure": c.runs_to_first_failure,
                "masked_by_retry": c.masked_by_retry,
                "hints": c.hints,
                "expected_flaky": bool(c.expected and c.expected.flaky),
                "expected_rate": c.expected.rate if c.expected else None,
                "by_label": {k: list(v) for k, v in c.rate_by_label.items()},
                "median_ms": c.median_ms,
                "outliers": c.outliers,
                "steps": [
                    {
                        "at": ordinal[s.run_id],
                        "at_session_start": ordinal[s.run_id] in session_starts,
                        "label": s.label,
                        "before": s.before_ms,
                        "after": s.after_ms,
                        "relative": s.relative,
                    }
                    for s in c.steps
                ],
                "fails": [i for i, s in enumerate(history) if not s.passed],
                "bins": _duration_bins(history, own_segments),
            }
        )

    where, params = analysis._where(session_ids, "b.session_id")
    boots = [
        {
            "id": boot_id,
            "label": label,
            "ok": bool(ok),
            "total": boot_ms,
            "launch": boot_ms - uptime if uptime is not None else None,
            "guest": net,
            "wait": uptime - net if uptime is not None and net is not None else None,
        }
        for boot_id, label, ok, boot_ms, uptime, net in conn.execute(
            f"""
            SELECT b.id, COALESCE(s.label, ''), b.ok, b.boot_ms, b.guest_uptime_ms, b.guest_net_ms
            FROM boot b JOIN session s ON s.id = b.session_id {where} ORDER BY b.id
            """,
            params,
        )
    ]

    where, params = analysis._where(session_ids, "id")
    sessions = [
        {
            "id": sid,
            "label": label or "",
            "started": started,
            "ended": ended,
            "sdk": sdk,
            "product": product,
            "cpus": cpus,
            "mem_mb": mem,
            "kvm": bool(kvm),
            "args": json.loads(args) if args else {},
        }
        for sid, label, started, ended, sdk, product, cpus, mem, kvm, args in conn.execute(
            f"""
            SELECT id, label, started_at, ended_at, sdk_version, product_version, host_cpus,
                   host_mem_mb, kvm, args
            FROM session {where} ORDER BY id
            """,
            params,
        )
    ]

    e = report.evaluation
    return {
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "sessions": sessions,
        "suite_runs": report.suite_runs,
        "case_runs": sum(c.runs for c in report.cases),
        "infra_errors": report.infra_errors,
        "segments": segments,
        "boot_starts": boot_starts,
        "positions": positions,
        "cases": cases,
        "boots": boots,
        "boot_groups": [
            {
                "label": g.label,
                "boots": g.boots,
                "median": g.median_ms,
                "min": g.min_ms,
                "max": g.max_ms,
                "launch": g.launch_ms,
                "guest": g.guest_net_ms,
                "wait": g.host_wait_ms,
                "slow": len(g.slow),
            }
            for g in report.boots.groups
        ],
        "evaluation": {
            "true_positives": e.true_positives,
            "false_positives": e.false_positives,
            "false_negatives": e.false_negatives,
            "true_negatives": e.true_negatives,
            "rates_checked": e.rates_checked,
            "rates_covered": e.rates_covered,
            "causes_checked": e.causes_checked,
            "causes_found": e.causes_found,
        }
        if e
        else None,
    }


def render(payload: dict) -> str:
    # "</" is escaped so that no string in the data can close the script element.
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    template = TEMPLATE.read_text()
    if template.count(DATA_MARKER) != 1:
        raise ValueError(f"{TEMPLATE} must contain the data marker exactly once")
    return template.replace(DATA_MARKER, data)


def write(conn: sqlite3.Connection, cfg: Config, out: pathlib.Path,
          session_ids: list[int] | None = None) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(build_payload(conn, cfg, session_ids)))
