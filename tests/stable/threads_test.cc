// Stable control: multi-threaded, but correctly synchronised. The flaky suite
// contains a deliberately racy twin of the producer/consumer test.

#include <atomic>
#include <condition_variable>
#include <mutex>
#include <queue>
#include <thread>
#include <vector>

#include <gtest/gtest.h>

namespace {

TEST(StableThreads, AtomicCounterIsExact) {
  constexpr int kThreads = 8;
  constexpr int kIncrements = 20000;
  std::atomic<int> counter{0};

  std::vector<std::thread> threads;
  for (int i = 0; i < kThreads; ++i) {
    threads.emplace_back([&counter] {
      for (int j = 0; j < kIncrements; ++j) {
        counter.fetch_add(1, std::memory_order_relaxed);
      }
    });
  }
  for (auto& t : threads) {
    t.join();
  }
  EXPECT_EQ(counter.load(), kThreads * kIncrements);
}

TEST(StableThreads, ProducerConsumerDeliversEverything) {
  constexpr int kItems = 10000;
  std::mutex mutex;
  std::condition_variable ready;
  std::queue<int> queue;
  bool done = false;
  long long consumed_sum = 0;

  std::thread consumer([&] {
    std::unique_lock<std::mutex> lock(mutex);
    for (;;) {
      ready.wait(lock, [&] { return !queue.empty() || done; });
      while (!queue.empty()) {
        consumed_sum += queue.front();
        queue.pop();
      }
      if (done) {
        return;
      }
    }
  });

  for (int i = 1; i <= kItems; ++i) {
    {
      std::lock_guard<std::mutex> lock(mutex);
      queue.push(i);
    }
    ready.notify_one();
  }
  {
    std::lock_guard<std::mutex> lock(mutex);
    done = true;
  }
  ready.notify_one();
  consumer.join();

  EXPECT_EQ(consumed_sum, static_cast<long long>(kItems) * (kItems + 1) / 2);
}

}  // namespace
