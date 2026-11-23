"""Host-driven tests that leave the device as they found it.

`DeviceTest` holds controls. `FlakyHostTest` holds one deliberately flaky case
of a kind that only exists on the host side: an assertion about how long a
round trip to the device takes.
"""

from __future__ import annotations

import uuid

from mobly import asserts, base_test

from host_tests import ffx_device


class DeviceTest(base_test.BaseTestClass):
    def setup_class(self) -> None:
        self.dut = self.register_controller(ffx_device)[0]

    def test_remote_control_echoes(self) -> None:
        reply = self.dut.echo("flakelab-ping")
        asserts.assert_in("flakelab-ping", reply)

    def test_uptime_advances(self) -> None:
        first = self.dut.uptime_ms()
        second = self.dut.uptime_ms()
        asserts.assert_greater(second, first)

    def test_core_components_exist(self) -> None:
        components = self.dut.components()
        for moniker in ("core", "core/remote-control", "bootstrap/archivist"):
            asserts.assert_in(moniker, components)

    def test_log_message_reaches_the_device_log(self) -> None:
        marker = f"flakelab-{uuid.uuid4().hex}"
        self.dut.log_message("flakelab", marker)
        asserts.assert_in(marker, self.dut.log_dump("flakelab"))


class FlakyHostTest(base_test.BaseTestClass):
    # BUG (deliberate): a fixed latency budget for a call that crosses from the
    # host into the emulator. Over 400 calls on a quiet host the round trip took
    # 28 ms at the median and 36 ms at most, and exceeded 32 ms in 7% of calls
    # and 35 ms in 1%. The budget sits between those, so the test fails now and
    # then on a quiet host and nearly always on a busy one.
    ECHO_BUDGET_MS = 33.0

    def setup_class(self) -> None:
        self.dut = self.register_controller(ffx_device)[0]

    def test_echo_within_budget(self) -> None:
        elapsed = self.dut.timed_echo_ms("flakelab-ping")
        asserts.assert_less(
            elapsed, self.ECHO_BUDGET_MS, f"echo took {elapsed:.0f} ms"
        )


TEST_CLASSES = (DeviceTest, FlakyHostTest)
