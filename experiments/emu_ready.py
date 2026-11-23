"""How long does the emulator really take to become usable?

`ffx emu start` waits for the guest by "knocking" on its remote control
service. This script measures the same boot three ways, in turn:

  default   `ffx emu start`, which returns when its own wait loop is satisfied
  wait      `ffx emu start --startup-timeout 0` (returns without waiting),
            followed by one `ffx target wait`
  poll      the same, followed by `ffx target echo` every 100 ms until one
            succeeds

For each boot it prints the host wall-clock time until the emulator was usable
and the guest's own uptime at that moment.

Usage: python3 experiments/emu_ready.py [boots per method]
"""

from __future__ import annotations

import pathlib
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from flakelab import config as config_mod  # noqa: E402
from flakelab.ffx import Ffx  # noqa: E402

WORKSPACE = pathlib.Path(__file__).resolve().parent.parent
INSTANCE = "flakelab-probe"
GIVE_UP_AFTER_S = 60


def guest_uptime_ms(ffx: Ffx) -> int | None:
    r = ffx.run("--target", INSTANCE, "target", "get-time", timeout=30)
    return int(r.stdout.strip()) // 1_000_000 if r.ok and r.stdout.strip().isdigit() else None


def boot(ffx: Ffx, bundle: pathlib.Path, method: str, log: pathlib.Path) -> tuple[int, int | None]:
    """Boots once and returns (host ms until usable, guest uptime ms at that point)."""
    start = time.monotonic()
    args = ["emu", "start", str(bundle), "--headless", "--name", INSTANCE, "--log", str(log)]
    if method != "default":
        args += ["--startup-timeout", "0"]
    ffx.run(*args, timeout=180, check=True)

    if method == "wait":
        ffx.run("--target", INSTANCE, "target", "wait", "-t", str(GIVE_UP_AFTER_S),
                timeout=GIVE_UP_AFTER_S + 30, check=True)
    elif method == "poll":
        while True:
            if ffx.run("--target", INSTANCE, "target", "echo", timeout=10).ok:
                break
            if time.monotonic() - start > GIVE_UP_AFTER_S:
                raise TimeoutError("emulator did not answer an echo")
            time.sleep(0.1)
    usable_ms = int((time.monotonic() - start) * 1000)
    return usable_ms, guest_uptime_ms(ffx)


def main() -> int:
    boots = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    cfg = config_mod.load(WORKSPACE / "lab.toml")
    ffx = Ffx.from_workspace(WORKSPACE)
    results: dict[str, list[int]] = {"default": [], "wait": [], "poll": []}
    with tempfile.TemporaryDirectory(prefix="flakelab-probe-") as tmp:
        log = pathlib.Path(tmp) / "emulator.log"
        try:
            # Interleave the methods so that drift on the host affects all three alike.
            for i in range(boots):
                for method in results:
                    ffx.run("emu", "stop", INSTANCE, timeout=60)
                    usable_ms, uptime = boot(ffx, cfg.product_bundle, method, log)
                    # Prove the emulator is really usable, not just answering.
                    echo = ffx.run("--target", INSTANCE, "target", "echo", timeout=30)
                    results[method].append(usable_ms)
                    print(f"boot {i + 1} {method:8} usable after {usable_ms:6} ms, "
                          f"guest uptime {uptime} ms, echo {'ok' if echo.ok else 'FAILED'}",
                          flush=True)
        finally:
            ffx.run("emu", "stop", INSTANCE, timeout=60)
    print()
    for method, values in results.items():
        print(f"{method:8} n={len(values)}  median {statistics.median(values):7.0f} ms  "
              f"min {min(values)}  max {max(values)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
