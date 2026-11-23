"""SQLite store. One row per session, emulator boot, suite run and test case."""

from __future__ import annotations

import datetime
import json
import pathlib
import sqlite3
from collections.abc import Iterable

from .results import CaseResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id              INTEGER PRIMARY KEY,
    started_at      TEXT NOT NULL,          -- UTC, ISO 8601
    ended_at        TEXT,
    label           TEXT,                   -- free text naming the condition under test
    sdk_version     TEXT NOT NULL,
    product_bundle  TEXT NOT NULL,
    product_version TEXT,
    host_kernel     TEXT,
    host_cpus       INTEGER,
    host_mem_mb     INTEGER,
    kvm             INTEGER,                -- 1 if /dev/kvm was usable
    args            TEXT                    -- JSON of the command-line options
);
CREATE TABLE IF NOT EXISTS boot (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL REFERENCES session(id),
    instance      TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    ok            INTEGER NOT NULL,
    boot_ms       INTEGER NOT NULL,         -- host wall clock of `ffx emu start`
    guest_uptime_ms INTEGER,                -- guest clock right after it returned
    guest_rcs_ms  INTEGER,                  -- guest time of first remote-control log line
    guest_net_ms  INTEGER,                  -- guest time of mDNS starting
    repo_setup_ms INTEGER,                  -- registering the package repository
    error         TEXT
);
CREATE TABLE IF NOT EXISTS run (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES session(id),
    boot_id     INTEGER NOT NULL REFERENCES boot(id),
    iteration   INTEGER NOT NULL,           -- pass over the whole suite list
    seq_in_boot INTEGER NOT NULL,           -- runs on this boot before this one
    suite       TEXT NOT NULL,              -- package/component
    started_at  TEXT NOT NULL,
    outcome     TEXT NOT NULL,              -- ffx outcome, or INFRA_ERROR / RUNNER_TIMEOUT
    exit_code   INTEGER,
    wall_ms     INTEGER NOT NULL,           -- host wall clock of the ffx call
    suite_ms    INTEGER,                    -- duration reported by the device
    host_load1  REAL,
    artifacts   TEXT,                       -- kept only for runs that did not pass
    error       TEXT
);
CREATE TABLE IF NOT EXISTS test_case (
    id              INTEGER PRIMARY KEY,
    run_id          INTEGER NOT NULL REFERENCES run(id),
    name            TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    start_offset_ms INTEGER,
    duration_ms     INTEGER                 -- derived, see results.CaseResult
);
CREATE INDEX IF NOT EXISTS run_suite ON run(suite);
CREATE INDEX IF NOT EXISTS case_run ON test_case(run_id);
CREATE INDEX IF NOT EXISTS case_name ON test_case(name);
"""


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, path: pathlib.Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def _insert(self, table: str, cols: dict) -> int:
        names = ", ".join(cols)
        marks = ", ".join("?" for _ in cols)
        cur = self.conn.execute(
            f"INSERT INTO {table} ({names}) VALUES ({marks})", tuple(cols.values())
        )
        return cur.lastrowid

    def start_session(self, *, args: dict, **cols) -> int:
        with self.conn:
            return self._insert(
                "session", {"started_at": now(), "args": json.dumps(args, sort_keys=True), **cols}
            )

    def end_session(self, session_id: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE session SET ended_at = ? WHERE id = ?", (now(), session_id))

    def add_boot(self, **cols) -> int:
        with self.conn:
            return self._insert("boot", cols)

    def set_repo_setup(self, boot_id: int, ms: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE boot SET repo_setup_ms = ? WHERE id = ?", (ms, boot_id))

    def add_run(self, cases: Iterable[CaseResult], **cols) -> int:
        with self.conn:
            run_id = self._insert("run", cols)
            self.conn.executemany(
                "INSERT INTO test_case (run_id, name, outcome, start_offset_ms, duration_ms)"
                " VALUES (?, ?, ?, ?, ?)",
                [(run_id, c.name, c.outcome, c.start_offset_ms, c.duration_ms) for c in cases],
            )
            return run_id

    def close(self) -> None:
        self.conn.close()
