"""The run loop: boot an emulator, serve the test packages, run suites, record."""

from __future__ import annotations

import collections
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import tempfile
import time

from . import db, results
from collections.abc import Callable

from .config import Config, HostSuite, Suite
from .emulator import Emulator, PackageRepo
from .ffx import Ffx, FfxError, Result, run_command

INSTANCE = "flakelab"
REPO_NAME = "flakelab"

# After this many runs in a row without a usable result, the emulator is assumed
# to be unhealthy and is restarted.
MAX_CONSECUTIVE_INFRA_ERRORS = 3
MAX_BOOT_ATTEMPTS = 3


class LabError(RuntimeError):
    pass


def build_packages(
    workspace: pathlib.Path, config: Config, defines: tuple[str, ...] = ()
) -> list[pathlib.Path]:
    """Builds every configured package and returns the paths of the .far files.

    `defines` are KEY=VALUE pairs passed to Bazel as --define, which is how a
    session selects a build variant such as the slower perf test.
    """
    if not config.packages:
        return []
    bazel = str(workspace / "tools" / "bazel")
    targets = [p.target for p in config.packages]
    flags = [f"--define={d}" for d in defines]
    quiet = ["--noshow_progress", "--show_result=0", "--ui_event_filters=-info,-warning"]
    subprocess.run([bazel, "build", *quiet, *flags, *targets], cwd=workspace, check=True)
    archives = []
    for package in config.packages:
        out = subprocess.run(
            [bazel, "cquery", *flags, "--output=files", package.target],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        wanted = f"/{package.name}.far"
        matches = [line for line in out.stdout.splitlines() if line.endswith(wanted)]
        if len(matches) != 1:
            raise LabError(f"{package.target}: expected one {wanted}, found {matches}")
        archives.append(workspace / matches[0])
    return archives


def _host_mem_mb() -> int | None:
    try:
        for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def _product_version(bundle: pathlib.Path) -> str | None:
    try:
        return json.loads((bundle / "product_bundle.json").read_text()).get("product_version")
    except (OSError, ValueError):
        return None


class HostStress:
    """Busy-loop processes on the host, to compete with the emulator for CPU."""

    def __init__(self, processes: int):
        self.processes = processes
        self._children: list[subprocess.Popen] = []

    def __enter__(self) -> "HostStress":
        for _ in range(self.processes):
            self._children.append(subprocess.Popen([sys.executable, "-c", "while True: pass"]))
        return self

    def __exit__(self, *exc) -> None:
        for child in self._children:
            child.kill()
        for child in self._children:
            child.wait()


class Lab:
    def __init__(
        self,
        *,
        workspace: pathlib.Path,
        ffx: Ffx,
        config: Config,
        store: db.Store,
        archives: list[pathlib.Path],
        out_dir: pathlib.Path,
        run_timeout: float = 120,
    ):
        self.workspace = workspace
        # Host suites need Mobly, which lives in the repo's virtualenv.
        self.host_python = workspace / ".venv" / "bin" / "python"
        self.ffx = ffx
        self.config = config
        self.store = store
        self.out_dir = out_dir
        self.run_timeout = run_timeout
        self.archives = archives
        self.scratch = pathlib.Path(tempfile.mkdtemp(prefix="flakelab-"))
        self.emulator = Emulator(
            ffx, config.product_bundle, INSTANCE, self.scratch / "emulator.log"
        )
        self.repo = PackageRepo(ffx, REPO_NAME, self.scratch / "repo")
        self.session_id = 0
        self.boot_id = 0
        self.seq_in_boot = 0
        self.outcomes: collections.Counter[str] = collections.Counter()

    # -- emulator ---------------------------------------------------------

    def _boot(self) -> None:
        """Starts a fresh emulator and registers the repository with it."""
        for attempt in range(1, MAX_BOOT_ATTEMPTS + 1):
            self.emulator.stop()
            started_at = db.now()
            boot = self.emulator.start()
            self.boot_id = self.store.add_boot(
                session_id=self.session_id,
                instance=boot.instance,
                started_at=started_at,
                ok=int(boot.ok),
                boot_ms=boot.boot_ms,
                guest_uptime_ms=boot.guest_uptime_ms,
                guest_rcs_ms=boot.guest_rcs_ms,
                guest_net_ms=boot.guest_net_ms,
                error=boot.error,
            )
            if not boot.ok:
                print(f"  boot attempt {attempt} failed after {boot.boot_ms} ms")
                continue
            if self.archives:
                try:
                    start = time.monotonic()
                    self.repo.register(INSTANCE)
                    self.store.set_repo_setup(
                        self.boot_id, int((time.monotonic() - start) * 1000)
                    )
                except FfxError as e:
                    print(f"  repository registration failed on attempt {attempt}: {e}")
                    continue
            self.seq_in_boot = 0
            print(f"  booted in {boot.boot_ms} ms, guest uptime {boot.guest_uptime_ms} ms"
                  f" (boot {self.boot_id})")
            return
        raise LabError(f"emulator did not come up in {MAX_BOOT_ATTEMPTS} attempts")

    # -- one suite run ----------------------------------------------------

    def _fresh_output(self) -> pathlib.Path:
        output = self.scratch / "run"
        shutil.rmtree(output, ignore_errors=True)
        return output

    def _run_suite(self, iteration: int, suite: Suite) -> str:
        """Runs one test component on the device with `ffx test run`."""
        output = self._fresh_output()
        started_at, load1 = db.now(), os.getloadavg()[0]
        r = self.ffx.run(
            "--target",
            INSTANCE,
            "test",
            "run",
            suite.url(REPO_NAME),
            "--parallel",
            "1",
            "--output-directory",
            str(output),
            timeout=self.run_timeout,
        )
        return self._record(
            iteration, suite.name, suite.component, started_at, load1, r, output,
            results.load_summary,
        )

    def _run_host_suite(self, iteration: int, suite: HostSuite) -> str:
        """Runs one Mobly module on the host, pointed at the emulator."""
        output = self._fresh_output()
        started_at, load1 = db.now(), os.getloadavg()[0]
        r = run_command(
            [
                str(self.host_python),
                "-m",
                "host_tests.run",
                "--module",
                suite.module,
                "--ffx",
                str(self.ffx.binary),
                "--target",
                INSTANCE,
                "--out",
                str(output),
            ],
            timeout=self.run_timeout,
            cwd=self.workspace,
        )
        return self._record(
            iteration, suite.name, suite.component, started_at, load1, r, output,
            results.load_host_summary,
        )

    def _record(
        self,
        iteration: int,
        suite_name: str,
        artifact_name: str,
        started_at: str,
        load1: float,
        r: Result,
        output: pathlib.Path,
        load: Callable[[pathlib.Path], results.SuiteResult],
    ) -> str:
        """Classifies a finished run, keeps its artifacts if it did not pass, stores it."""
        summary = None
        error = None
        if r.timed_out:
            outcome = results.RUNNER_TIMEOUT
            error = r.tail()
        else:
            try:
                summary = load(output)
                outcome = summary.outcome
            except (OSError, ValueError, KeyError) as e:
                outcome = results.INFRA_ERROR
                error = f"{type(e).__name__}: {e}\n{r.tail()}"

        artifacts = None
        if outcome != results.PASSED:
            keep = self.out_dir / "artifacts" / f"s{self.session_id}" / (
                f"i{iteration:05d}-{artifact_name}"
            )
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.rmtree(keep, ignore_errors=True)
            if output.exists():
                shutil.move(str(output), keep)
            else:
                keep.mkdir()
            (keep / "stdout.txt").write_text(r.stdout)
            (keep / "stderr.txt").write_text(r.stderr)
            artifacts = str(keep.relative_to(self.out_dir))

        self.store.add_run(
            summary.cases if summary else (),
            session_id=self.session_id,
            boot_id=self.boot_id,
            iteration=iteration,
            seq_in_boot=self.seq_in_boot,
            suite=suite_name,
            started_at=started_at,
            outcome=outcome,
            exit_code=r.returncode,
            wall_ms=r.wall_ms,
            suite_ms=summary.duration_ms if summary else None,
            host_load1=load1,
            artifacts=artifacts,
            error=error,
        )
        self.seq_in_boot += 1
        self.outcomes[outcome] += 1
        return outcome

    # -- session ----------------------------------------------------------

    def run(
        self, *, iterations: int, restart_every: int, label: str | None, args: dict
    ) -> None:
        self.session_id = self.store.start_session(
            args=args,
            label=label,
            sdk_version=self.ffx.sdk_version(),
            product_bundle=str(self.config.product_bundle),
            product_version=_product_version(self.config.product_bundle),
            host_kernel=platform.release(),
            host_cpus=os.cpu_count(),
            host_mem_mb=_host_mem_mb(),
            kvm=int(os.access("/dev/kvm", os.R_OK | os.W_OK)),
        )
        if self.config.host_suites and not self.host_python.exists():
            raise LabError(
                f"host suites are configured but {self.host_python} does not exist; "
                "see 'Host-driven tests' in the README"
            )
        plan: list[Callable[[int], str]] = [
            *(lambda i, s=s: self._run_suite(i, s) for s in self.config.suites),
            *(lambda i, h=h: self._run_host_suite(i, h) for h in self.config.host_suites),
        ]
        print(f"session {self.session_id}: {iterations} iterations x {len(plan)} suites")
        started = time.monotonic()
        try:
            if self.archives:
                self.repo.create(self.archives)
                self.repo.serve()
            self._boot()
            infra_streak = 0
            for iteration in range(iterations):
                if restart_every and iteration and iteration % restart_every == 0:
                    self._boot()
                for run_one in plan:
                    outcome = run_one(iteration)
                    if outcome in (results.INFRA_ERROR, results.RUNNER_TIMEOUT):
                        infra_streak += 1
                        if infra_streak >= MAX_CONSECUTIVE_INFRA_ERRORS:
                            print(f"  {infra_streak} runs without a result; restarting emulator")
                            self._boot()
                            infra_streak = 0
                    else:
                        infra_streak = 0
                if (iteration + 1) % 10 == 0 or iteration + 1 == iterations:
                    elapsed = time.monotonic() - started
                    print(f"  iteration {iteration + 1}/{iterations}  {elapsed:6.1f} s  "
                          f"{dict(self.outcomes)}")
        finally:
            self.emulator.stop()
            if self.archives:
                self.repo.stop()
            shutil.rmtree(self.scratch, ignore_errors=True)
            self.store.end_session(self.session_id)
