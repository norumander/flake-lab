// Flaky because of a data race: two threads increment a shared counter with no
// synchronisation, so an update is sometimes lost.
//
// The stable twin is StableThreads.AtomicCounterIsExact. The failure rate is
// not known in advance. It depends on how the two threads interleave, and so on
// the number of virtual CPUs and on host load. With 2,500 increments it
// measured about 5% on a quiet 4-vCPU emulator.

#include <atomic>
#include <thread>

#include <gtest/gtest.h>

namespace {

TEST(FlakyRace, LostUpdate) {
  constexpr int kIncrements = 2500;
  volatile int counter = 0;  // BUG (deliberate): should be std::atomic<int>.
  std::atomic<bool> go{false};

  auto work = [&] {
    while (!go.load(std::memory_order_acquire)) {
    }
    for (int i = 0; i < kIncrements; ++i) {
      counter = counter + 1;
    }
  };
  std::thread a(work), b(work);
  go.store(true, std::memory_order_release);
  a.join();
  b.join();

  EXPECT_EQ(counter, 2 * kIncrements);
}

}  // namespace
