"""Unit tests for the serial-log parsing in emulator.py."""

import unittest

from flakelab import emulator

SERIAL = """\
linux-x86-boot-shim: Legacy boot from Linux/x86 bzImage b000.
[00000.000] 00000:01024> welcome to Zircon
[00000.401] 15784:15788> [remote-control] INFO: Setting up http server for authorized_keys
[00000.459] 15784:15788> [remote-control] INFO: Trying to load file
[00001.547] 15396:15398> [mdns] INFO: Starting mDNS on interface ethv74 using port 5353
[00006.158] 15396:15398> [mdns] INFO: Starting mDNS on interface ethv74 using port 5353
"""


class ParseSerialMilestonesTest(unittest.TestCase):
    def test_takes_first_occurrence_of_each_marker(self):
        self.assertEqual(emulator.parse_serial_milestones(SERIAL), (401, 1547))

    def test_carriage_returns_do_not_matter(self):
        self.assertEqual(
            emulator.parse_serial_milestones(SERIAL.replace("\n", "\r\n")), (401, 1547)
        )

    def test_missing_markers_give_none(self):
        self.assertEqual(emulator.parse_serial_milestones("no kernel lines here\n"), (None, None))
        only_rcs = "[00000.401] 1:2> [remote-control] INFO: up\n"
        self.assertEqual(emulator.parse_serial_milestones(only_rcs), (401, None))


if __name__ == "__main__":
    unittest.main()
