"""Runs one host test module with Mobly and writes a summary the lab can read.

Usage, from the repo root with the virtualenv's Python:

    .venv/bin/python -m host_tests.run --module host_tests.device_test \\
        --ffx <path to ffx> --target <device name> --out <directory>

Mobly's own logs and its test_summary.yaml go to <out>/mobly. The lab's runner
only uses the standard library, so this script also writes <out>/summary.json:

    {"cases": [{"name": "Class.test", "outcome": "PASSED", "start_ms": 0,
                "end_ms": 12, "details": null}, ...]}
"""

from __future__ import annotations

import argparse
import importlib
import json
import pathlib
import sys

from mobly import config_parser, test_runner

from host_tests import ffx_device

# Mobly result names mapped to the outcome names ffx uses, so both kinds of
# test read the same in the database.
OUTCOMES = {"PASS": "PASSED", "FAIL": "FAILED", "ERROR": "ERROR", "SKIP": "SKIPPED"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--module", required=True, help="module with a TEST_CLASSES tuple")
    parser.add_argument("--ffx", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)

    classes = importlib.import_module(args.module).TEST_CLASSES
    log_dir = args.out / "mobly"
    log_dir.mkdir(parents=True, exist_ok=True)

    config = config_parser.TestRunConfig()
    config.testbed_name = args.target
    config.log_path = str(log_dir)
    config.controller_configs = {
        ffx_device.MOBLY_CONTROLLER_CONFIG_NAME: [{"name": args.target, "ffx": args.ffx}]
    }

    runner = test_runner.TestRunner(log_dir=str(log_dir), testbed_name=args.target)
    with runner.mobly_logger():
        for cls in classes:
            runner.add_test_class(config, cls)
        runner.run()

    cases = [
        {
            "name": f"{record.test_class}.{record.test_name}",
            "outcome": OUTCOMES.get(record.result, record.result),
            "start_ms": record.begin_time,
            "end_ms": record.end_time,
            "details": record.details,
        }
        for record in runner.results.executed
    ]
    (args.out / "summary.json").write_text(json.dumps({"cases": cases}, indent=2))
    return 0 if runner.results.is_all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
