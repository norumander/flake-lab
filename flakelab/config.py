"""Lab configuration, read from lab.toml."""

from __future__ import annotations

import dataclasses
import pathlib
import tomllib


@dataclasses.dataclass(frozen=True)
class Package:
    target: str  # Bazel label of the fuchsia_*_package
    name: str  # Fuchsia package name
    components: tuple[str, ...]  # test component names, without .cm


@dataclasses.dataclass(frozen=True)
class Suite:
    """One test component, which `ffx test run` treats as one suite."""

    package: str
    component: str

    @property
    def name(self) -> str:
        return f"{self.package}/{self.component}"

    def url(self, repo: str) -> str:
        return f"fuchsia-pkg://{repo}/{self.package}#meta/{self.component}.cm"


@dataclasses.dataclass(frozen=True)
class HostSuite:
    """A Mobly test module under host_tests/, run on the host against the device."""

    component: str  # short name, used for artifact directories
    module: str  # importable module that defines TEST_CLASSES

    @property
    def name(self) -> str:
        return f"host/{self.component}"


@dataclasses.dataclass(frozen=True)
class Expectation:
    """What a test's author says it should do. Used only to score the detector.

    A case with no entry in lab.toml is expected to be stable.
    """

    flaky: bool = False
    rate: float | None = None  # injected failure probability, where one exists
    cause: str | None = None  # hint kind the detector should produce, e.g. "boot"


@dataclasses.dataclass(frozen=True)
class Config:
    product_bundle: pathlib.Path
    packages: tuple[Package, ...]
    host_suites: tuple[HostSuite, ...] = ()
    expect: dict[str, Expectation] = dataclasses.field(default_factory=dict)

    @property
    def suites(self) -> tuple[Suite, ...]:
        return tuple(Suite(p.name, c) for p in self.packages for c in p.components)


def load(path: pathlib.Path) -> Config:
    with path.open("rb") as f:
        raw = tomllib.load(f)
    packages = tuple(
        Package(p["target"], p["name"], tuple(p["components"])) for p in raw.get("package", [])
    )
    host_suites = tuple(HostSuite(h["name"], h["module"]) for h in raw.get("host_suite", []))
    if not packages and not host_suites:
        raise ValueError(f"{path}: no [[package]] or [[host_suite]] entries")
    expect = {
        name: Expectation(
            flaky=e.get("flaky", "rate" in e or "cause" in e),
            rate=e.get("rate"),
            cause=e.get("cause"),
        )
        for name, e in raw.get("expect", {}).items()
    }
    return Config(
        pathlib.Path(raw["product_bundle"]).expanduser(), packages, host_suites, expect
    )
