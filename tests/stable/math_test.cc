// Stable control: pure computation, no I/O, no threads, no clock.

#include <algorithm>
#include <cstdint>
#include <numeric>
#include <vector>

#include <gtest/gtest.h>

namespace {

TEST(StableMath, PrimeCountBelow100k) {
  const int n = 100000;
  std::vector<bool> composite(n + 1, false);
  int primes = 0;
  for (int i = 2; i <= n; ++i) {
    if (composite[i]) {
      continue;
    }
    ++primes;
    for (int64_t j = int64_t{i} * i; j <= n; j += i) {
      composite[j] = true;
    }
  }
  EXPECT_EQ(primes, 9592);
}

TEST(StableMath, SortIsOrdered) {
  // Fixed-seed LCG so the input is the same on every run.
  uint32_t state = 12345;
  std::vector<uint32_t> values(50000);
  for (auto& v : values) {
    state = state * 1664525u + 1013904223u;
    v = state;
  }
  std::sort(values.begin(), values.end());
  EXPECT_TRUE(std::is_sorted(values.begin(), values.end()));
}

TEST(StableMath, SumOfFirstMillion) {
  std::vector<uint64_t> values(1000000);
  std::iota(values.begin(), values.end(), uint64_t{1});
  EXPECT_EQ(std::accumulate(values.begin(), values.end(), uint64_t{0}), 500000500000u);
}

}  // namespace
