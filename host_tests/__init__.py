"""Host-driven tests: Mobly test classes that drive the Fuchsia device through ffx.

This mirrors the shape of Fuchsia's Lacewing framework (Mobly on the host, a
device controller, ffx as the transport) without Honeydew, which is not part of
the SDK. These modules need Mobly and therefore run from the repo's virtualenv,
not from the system Python that the rest of the lab uses.
"""
