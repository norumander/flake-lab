"""A host-driven test that reboots the device.

It is kept out of the repeated lab runs because a reboot drops the package
repository registration and resets the uptime that other tests depend on.
"""

from __future__ import annotations

from mobly import asserts, base_test

from host_tests import ffx_device


class RebootTest(base_test.BaseTestClass):
    def setup_class(self) -> None:
        self.dut = self.register_controller(ffx_device)[0]

    def test_soft_reboot_restores_remote_control(self) -> None:
        before = self.dut.uptime_ms()
        self.dut.reboot()
        self.dut.wait()
        after = self.dut.uptime_ms()
        # A reboot is the only way the monotonic clock can go down.
        asserts.assert_less(after, before, "uptime did not reset, so the device did not reboot")
        asserts.assert_in("flakelab-ping", self.dut.echo("flakelab-ping"))


TEST_CLASSES = (RebootTest,)
