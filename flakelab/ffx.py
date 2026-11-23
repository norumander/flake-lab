"""Thin wrapper around the ffx binary from the Bazel-fetched Fuchsia SDK.

ffx is always invoked through its real path inside the SDK directory. Invoked
through a symlink that has no SDK manifest above it (a Bazel runfiles tree, for
example), ffx finds no SDK root and loses every subtool such as `test`, `emu`
and `repository`.
"""

from __future__ import annotations

import dataclasses
import pathlib
import subprocess
import time

SDK_REPO_DIR = "external/fuchsia_infra++cipd_ext+fuchsia_sdk"


def _text(stream: bytes | str | None) -> str:
    if stream is None:
        return ""
    return stream.decode(errors="replace") if isinstance(stream, bytes) else stream


@dataclasses.dataclass(frozen=True)
class Result:
    args: tuple[str, ...]
    returncode: int | None  # None if the call timed out
    stdout: str
    stderr: str
    wall_ms: int

    @property
    def timed_out(self) -> bool:
        return self.returncode is None

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def tail(self, lines: int = 15) -> str:
        text = (self.stdout + "\n" + self.stderr).strip().splitlines()
        return "\n".join(text[-lines:])


def run_command(
    argv: list[str], *, timeout: float, cwd: pathlib.Path | None = None
) -> Result:
    """Runs a command to completion or timeout and captures its output."""
    start = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        code: int | None = proc.returncode
        out, err = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        code, out, err = None, _text(e.stdout), _text(e.stderr)
    return Result(tuple(argv[1:]), code, out, err, int((time.monotonic() - start) * 1000))


class FfxError(RuntimeError):
    def __init__(self, result: Result):
        self.result = result
        super().__init__(f"ffx {' '.join(result.args)} -> {result.returncode}\n{result.tail()}")


class Ffx:
    def __init__(self, binary: pathlib.Path):
        if not binary.is_file():
            raise FileNotFoundError(f"ffx not found at {binary}; build the workspace first")
        self.binary = binary.resolve()

    @classmethod
    def from_workspace(cls, workspace: pathlib.Path) -> "Ffx":
        out = subprocess.run(
            [str(workspace / "tools" / "bazel"), "info", "output_base"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        return cls(pathlib.Path(out.stdout.strip()) / SDK_REPO_DIR / "tools" / "x64" / "ffx")

    def run(self, *args: str, timeout: float = 120, check: bool = False) -> Result:
        result = run_command([str(self.binary), *args], timeout=timeout)
        if check and not result.ok:
            raise FfxError(result)
        return result

    def sdk_version(self) -> str:
        return self.run("sdk", "version", check=True).stdout.strip()
