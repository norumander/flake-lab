"""Emulator lifecycle and the package repository the tests are served from."""

from __future__ import annotations

import dataclasses
import pathlib
import re
import shutil

from .ffx import Ffx

# Kernel log lines look like "[00001.547] 15396:15398> [mdns] INFO: ...". The
# bracketed number is seconds since the guest kernel started.
_SERIAL_LINE = re.compile(r"^\[(\d+\.\d+)\] \d+:\d+> (.*)")

# Guest-side milestones looked for in the serial log. They are log messages,
# not an API, so a different product version may not print them; the fields
# are then left empty.
_RCS_MARKER = "[remote-control]"
_NET_MARKER = "[mdns] INFO: Starting mDNS"


@dataclasses.dataclass(frozen=True)
class Boot:
    instance: str
    ok: bool
    # Host wall clock of `ffx emu start`, which returns once ffx can reach the
    # remote control service (RCS) on the guest.
    boot_ms: int
    # Guest monotonic clock read right after `ffx emu start` returned.
    guest_uptime_ms: int | None = None
    # Guest kernel time of the first RCS log line and of mDNS starting on the
    # network interface, from the serial log.
    guest_rcs_ms: int | None = None
    guest_net_ms: int | None = None
    error: str | None = None


def parse_serial_milestones(text: str) -> tuple[int | None, int | None]:
    """Returns (first RCS log, mDNS start) in guest milliseconds."""
    rcs = net = None
    for line in text.splitlines():
        m = _SERIAL_LINE.match(line)
        if not m:
            continue
        ms = round(float(m.group(1)) * 1000)
        if rcs is None and m.group(2).startswith(_RCS_MARKER):
            rcs = ms
        if net is None and m.group(2).startswith(_NET_MARKER):
            net = ms
        if rcs is not None and net is not None:
            break
    return rcs, net


class Emulator:
    def __init__(self, ffx: Ffx, product_bundle: pathlib.Path, instance: str, log: pathlib.Path):
        self.ffx = ffx
        self.product_bundle = product_bundle
        self.instance = instance
        self.log = log

    @property
    def serial_log(self) -> pathlib.Path:
        return self.log.with_name(self.log.name + ".serial")

    def start(self, timeout: float = 180) -> Boot:
        r = self.ffx.run(
            "emu",
            "start",
            str(self.product_bundle),
            "--headless",
            "--name",
            self.instance,
            "--log",
            str(self.log),
            timeout=timeout,
        )
        if not r.ok:
            return Boot(self.instance, False, r.wall_ms, error=r.tail())

        uptime = None
        t = self.ffx.run("--target", self.instance, "target", "get-time", timeout=30)
        if t.ok and t.stdout.strip().isdigit():
            uptime = int(t.stdout.strip()) // 1_000_000
        try:
            rcs, net = parse_serial_milestones(self.serial_log.read_text(errors="replace"))
        except OSError:
            rcs = net = None
        return Boot(self.instance, True, r.wall_ms, uptime, rcs, net)

    def stop(self) -> None:
        self.ffx.run("emu", "stop", self.instance, timeout=60)


class PackageRepo:
    """A local package repository, served by ffx and registered with a target."""

    def __init__(self, ffx: Ffx, name: str, path: pathlib.Path):
        self.ffx = ffx
        self.name = name
        self.path = path

    def create(self, archives: list[pathlib.Path]) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
        self.ffx.run("repository", "create", "--time-versioning", str(self.path), check=True)
        self.publish(archives)

    def publish(self, archives: list[pathlib.Path]) -> None:
        cmd = ["repository", "publish", "--time-versioning"]
        for far in archives:
            cmd += ["--package-archive", str(far)]
        self.ffx.run(*cmd, str(self.path), check=True)

    def serve(self) -> None:
        # A server left over from a crashed session would still hold the name.
        self.ffx.run("repository", "server", "stop", self.name, timeout=30)
        self.ffx.run(
            "repository",
            "server",
            "start",
            "--no-device",
            "--address",
            "[::]:0",
            "--background",
            "--repository",
            self.name,
            "--repo-path",
            str(self.path),
            check=True,
        )

    def register(self, target: str) -> None:
        self.ffx.run(
            "--target", target, "target", "repository", "register", "-r", self.name, check=True
        )

    def stop(self) -> None:
        self.ffx.run("repository", "server", "stop", self.name, timeout=30)
        shutil.rmtree(self.path, ignore_errors=True)
