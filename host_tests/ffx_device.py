"""A Mobly controller for one Fuchsia device, reached through ffx.

Mobly loads this module by name and calls `create` with the entries found under
`FfxDevice` in the testbed configuration. Each entry needs the target name and
the path of the ffx binary.
"""

from __future__ import annotations

import subprocess
import time

MOBLY_CONTROLLER_CONFIG_NAME = "FfxDevice"


class FfxCommandError(RuntimeError):
    """An ffx command exited non-zero or timed out."""


class FfxDevice:
    def __init__(self, name: str, ffx: str):
        self.name = name
        self.ffx = ffx

    def run(self, *args: str, timeout: float = 30) -> str:
        """Runs `ffx --target <name> <args>` and returns its stdout."""
        cmd = [self.ffx, "--target", self.name, *args]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise FfxCommandError(f"ffx {' '.join(args)} timed out after {timeout} s") from e
        if proc.returncode != 0:
            raise FfxCommandError(
                f"ffx {' '.join(args)} exited {proc.returncode}: "
                f"{(proc.stderr or proc.stdout).strip()[-400:]}"
            )
        return proc.stdout

    def echo(self, text: str) -> str:
        """Sends text through the remote control service and returns the reply line."""
        return self.run("target", "echo", text).strip()

    def timed_echo_ms(self, text: str) -> float:
        start = time.monotonic()
        self.echo(text)
        return (time.monotonic() - start) * 1000

    def uptime_ms(self) -> int:
        return int(self.run("target", "get-time").strip()) // 1_000_000

    def components(self) -> list[str]:
        return self.run("component", "list").split()

    def log_message(self, tag: str, message: str) -> None:
        self.run("target", "log-message", "--tag", tag, "--", message)

    def log_dump(self, tag: str) -> str:
        return self.run("log", "--tag", tag, "dump", timeout=60)

    def reboot(self) -> None:
        self.run("target", "reboot", timeout=60)

    def wait(self, timeout_s: int = 90) -> None:
        self.run("target", "wait", "-t", str(timeout_s), timeout=timeout_s + 30)


def create(configs: list[dict]) -> list[FfxDevice]:
    return [FfxDevice(name=c["name"], ffx=c["ffx"]) for c in configs]


def destroy(devices: list[FfxDevice]) -> None:
    # Nothing to release: every ffx call is its own process.
    del devices


def get_info(devices: list[FfxDevice]) -> list[dict]:
    return [{"name": d.name, "ffx": d.ffx} for d in devices]
