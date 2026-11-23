// Flaky because it sleeps instead of synchronising: the test assumes a worker
// thread has finished after a fixed wait.
//
// The worker needs one millisecond and the test waits 1.1 ms, so it fails
// whenever the worker is scheduled more than about 100 microseconds late. It
// measured about 1% on a quiet emulator and should get worse under host load.

#include <lib/zx/clock.h>
#include <lib/zx/time.h>

#include <atomic>
#include <thread>

#include <gtest/gtest.h>

namespace {

TEST(FlakyTiming, SleepInsteadOfSync) {
  constexpr zx::duration kWork = zx::msec(1);
  constexpr zx::duration kWait = zx::usec(1100);
  std::atomic<bool> done{false};

  std::thread worker([&] {
    const zx::time until = zx::deadline_after(kWork);
    while (zx::clock::get_monotonic() < until) {
    }
    done.store(true);
  });

  // BUG (deliberate): should wait for the worker, not for the clock.
  zx::nanosleep(zx::deadline_after(kWait));
  const bool finished = done.load();
  worker.join();

  EXPECT_TRUE(finished) << "worker had not finished after " << kWait.to_usecs() << " us";
}

}  // namespace
