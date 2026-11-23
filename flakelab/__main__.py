"""Command line: `python3 -m flakelab run|summary|report|dashboard`."""

from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

from . import analysis
from . import config as config_mod
from . import dashboard, db, report
from .ffx import Ffx
from .runner import HostStress, Lab, LabError, build_packages

WORKSPACE = pathlib.Path(__file__).resolve().parent.parent


def _percentile(sorted_values: list[int], q: float) -> int:
    """Nearest-rank percentile of an already sorted, non-empty list."""
    rank = max(1, -(-len(sorted_values) * q // 100))  # ceil without floats
    return sorted_values[int(rank) - 1]


def cmd_run(args: argparse.Namespace) -> int:
    cfg = config_mod.load(args.config)
    if not cfg.product_bundle.is_dir():
        print(f"product bundle not found: {cfg.product_bundle}", file=sys.stderr)
        return 2
    archives = build_packages(WORKSPACE, cfg, tuple(args.define))
    ffx = Ffx.from_workspace(WORKSPACE)
    store = db.Store(args.db)
    lab = Lab(
        workspace=WORKSPACE,
        ffx=ffx,
        config=cfg,
        store=store,
        archives=archives,
        out_dir=args.db.parent,
        run_timeout=args.run_timeout,
    )
    try:
        with HostStress(args.host_stress):
            lab.run(
                iterations=args.iterations,
                restart_every=args.restart_every,
                label=args.label,
                args={
                    "iterations": args.iterations,
                    "restart_every": args.restart_every,
                    "run_timeout": args.run_timeout,
                    "define": args.define,
                    "host_stress": args.host_stress,
                },
            )
    except LabError as e:
        print(f"lab error: {e}", file=sys.stderr)
        return 1
    finally:
        store.close()
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    conn = sqlite3.connect(args.db)
    where, params = "", ()
    if args.session is not None:
        where, params = "WHERE session_id = ?", (args.session,)

    rows = conn.execute(
        f"SELECT suite, outcome, wall_ms, suite_ms FROM run {where} ORDER BY suite", params
    ).fetchall()
    if not rows:
        print("no runs recorded")
        return 0
    by_suite: dict[str, list[tuple[str, int, int | None]]] = {}
    for suite, outcome, wall_ms, suite_ms in rows:
        by_suite.setdefault(suite, []).append((outcome, wall_ms, suite_ms))

    print(f"{'suite':44} {'runs':>5} {'pass':>5} {'other':>5} {'wall p50':>9} {'wall p95':>9}"
          f" {'suite p50':>10}")
    for suite, runs in by_suite.items():
        walls = sorted(r[1] for r in runs)
        suite_ms = sorted(r[2] for r in runs if r[2] is not None)
        passed = sum(1 for r in runs if r[0] == "PASSED")
        print(
            f"{suite:44} {len(runs):5} {passed:5} {len(runs) - passed:5}"
            f" {_percentile(walls, 50):6} ms {_percentile(walls, 95):6} ms"
            f" {(_percentile(suite_ms, 50) if suite_ms else 0):7} ms"
        )

    boots = [
        r[0]
        for r in conn.execute(
            f"SELECT boot_ms FROM boot {where} {'AND' if where else 'WHERE'} ok = 1"
            " ORDER BY boot_ms",
            params,
        )
    ]
    if boots:
        print(f"\nboots: {len(boots)}  min {boots[0]} ms  p50 {_percentile(boots, 50)} ms"
              f"  max {boots[-1]} ms")
    walls = sorted(r[2] for r in rows)
    print(f"runs: {len(rows)}  total wall {sum(walls) / 1000:.1f} s"
          f"  mean {sum(walls) / len(walls):.0f} ms per suite run")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    cfg = config_mod.load(args.config)
    conn = sqlite3.connect(args.db)
    result = analysis.build_report(conn, cfg.expect, args.session or None)
    print(report.render(result))
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    cfg = config_mod.load(args.config)
    conn = sqlite3.connect(args.db)
    dashboard.write(conn, cfg, args.out, args.session or None)
    print(f"wrote {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Progress lines should appear as they happen, also when redirected to a file.
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(prog="flakelab", description=__doc__)
    parser.add_argument("--db", type=pathlib.Path, default=WORKSPACE / "out" / "flakelab.sqlite")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="build the test packages and run them repeatedly")
    run.add_argument("--config", type=pathlib.Path, default=WORKSPACE / "lab.toml")
    run.add_argument("--iterations", type=int, default=25)
    run.add_argument("--restart-every", type=int, default=0, metavar="N",
                     help="restart the emulator every N iterations (0 = never)")
    run.add_argument("--run-timeout", type=float, default=120, metavar="SECONDS",
                     help="host-side limit for one suite run")
    run.add_argument("--label", help="name for the condition this session measures")
    run.add_argument("--define", action="append", default=[], metavar="KEY=VALUE",
                     help="passed to Bazel as --define; selects a build variant")
    run.add_argument("--host-stress", type=int, default=0, metavar="N",
                     help="run N busy-loop processes on the host during the session")
    run.set_defaults(func=cmd_run)

    summary = sub.add_parser("summary", help="per-suite pass counts and timings")
    summary.add_argument("--session", type=int)
    summary.set_defaults(func=cmd_summary)

    rep = sub.add_parser("report", help="flaky tests, likely causes and duration changes")
    rep.add_argument("--config", type=pathlib.Path, default=WORKSPACE / "lab.toml")
    rep.add_argument("--session", type=int, action="append",
                     help="limit to this session; may be repeated")
    rep.set_defaults(func=cmd_report)

    dash = sub.add_parser("dashboard", help="write the static HTML dashboard")
    dash.add_argument("--config", type=pathlib.Path, default=WORKSPACE / "lab.toml")
    dash.add_argument("--out", type=pathlib.Path, default=WORKSPACE / "out" / "dashboard.html")
    dash.add_argument("--session", type=int, action="append",
                      help="limit to this session; may be repeated")
    dash.set_defaults(func=cmd_dashboard)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
