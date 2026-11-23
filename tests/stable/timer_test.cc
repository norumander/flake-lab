// Stable control with a known duration. It only asserts a lower bound, which a
// monotonic clock guarantees, so it cannot fail on a slow host. Its recorded
// duration is the baseline for duration-anomaly detection.

#include <lib/zx/clock.h>
#include <lib/zx/time.h>
#include <zircon/syscalls.h>

#include <gtest/gtest.h>

namespace {

TEST(StableTimer, SleepLastsAtLeastAsLongAsAsked) {
  const zx::duration want = zx::msec(50);
  const zx::time start = zx::clock::get_monotonic();
  zx::nanosleep(zx::deadline_after(want));
  const zx::duration elapsed = zx::clock::get_monotonic() - start;
  EXPECT_GE(elapsed.get(), want.get());
}

TEST(StableTimer, MonotonicClockDoesNotGoBackwards) {
  zx::time previous = zx::clock::get_monotonic();
  for (int i = 0; i < 100000; ++i) {
    const zx::time now = zx::clock::get_monotonic();
    ASSERT_GE(now.get(), previous.get());
    previous = now;
  }
}

}  // namespace
