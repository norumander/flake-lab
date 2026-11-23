// Flaky because it depends on how long the device has been up.
//
// This simulates a test that needs a service which is only ready about ten
// seconds after boot. Keying it on uptime makes the cause exact: every failure
// happens in the first seconds after an emulator start and none later. A
// detector that looks only at the overall pass rate sees an ordinary flaky
// test. One that looks at position since boot can name the cause.

#include <lib/zx/clock.h>
#include <lib/zx/time.h>

#include <gtest/gtest.h>

namespace {

TEST(FlakyBoot, NeedsWarmDevice) {
  constexpr zx::duration kReadyAfter = zx::sec(10);
  const zx::duration uptime = zx::clock::get_monotonic() - zx::time(0);
  EXPECT_GE(uptime.get(), kReadyAfter.get())
      << "simulated dependency not ready: uptime is " << uptime.to_msecs() << " ms";
}

}  // namespace
