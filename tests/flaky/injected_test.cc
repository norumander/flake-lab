// Flaky by construction: each case fails with a fixed, known probability.
//
// These are the lab's ground truth. The detector's estimate for each case can
// be compared with the rate written here.

#include <zircon/syscalls.h>

#include <cstdint>

#include <gtest/gtest.h>

namespace {

// Returns true with probability `per_ten_thousand` / 10000, using the kernel
// CPRNG so that no seed is shared between runs.
bool Roll(uint32_t per_ten_thousand) {
  uint32_t value = 0;
  zx_cprng_draw(&value, sizeof(value));
  return value % 10000 < per_ten_thousand;
}

TEST(FlakyInjected, Rate20Percent) { EXPECT_FALSE(Roll(2000)) << "injected failure, p = 0.20"; }

TEST(FlakyInjected, Rate5Percent) { EXPECT_FALSE(Roll(500)) << "injected failure, p = 0.05"; }

TEST(FlakyInjected, Rate1Percent) { EXPECT_FALSE(Roll(100)) << "injected failure, p = 0.01"; }

}  // namespace
