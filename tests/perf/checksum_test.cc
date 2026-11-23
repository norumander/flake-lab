// Never fails, but can be built slower.
//
// Built normally, the test hashes a buffer a fixed number of times. Built with
// `--define=flakelab_regression=1` it does half as much work again. That stands
// in for a commit that makes a test slower without breaking it, which only
// shows up if durations are recorded and watched.

#include <cstddef>
#include <cstdint>
#include <vector>

#include <gtest/gtest.h>

namespace {

#ifdef FLAKELAB_REGRESSION
constexpr int kRounds = 30;
#else
constexpr int kRounds = 20;
#endif

uint64_t Fnv1a(const std::vector<uint8_t>& data, uint64_t hash) {
  for (uint8_t byte : data) {
    hash ^= byte;
    hash *= 1099511628211ull;
  }
  return hash;
}

TEST(PerfChecksum, HashBuffer) {
  std::vector<uint8_t> data(size_t{1} << 20);
  for (size_t i = 0; i < data.size(); ++i) {
    data[i] = static_cast<uint8_t>(i * 31);
  }
  uint64_t hash = 14695981039346656037ull;
  for (int round = 0; round < kRounds; ++round) {
    hash = Fnv1a(data, hash);
  }
  // The value itself does not matter; using it keeps the loop from being
  // optimised away.
  EXPECT_NE(hash, 0u);
}

}  // namespace
