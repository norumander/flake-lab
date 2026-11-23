// Stable control: Zircon kernel objects used single-threaded.

#include <lib/zx/channel.h>
#include <lib/zx/event.h>
#include <lib/zx/vmo.h>
#include <zircon/types.h>

#include <cstring>

#include <gtest/gtest.h>

namespace {

TEST(StableZircon, ChannelRoundTrip) {
  zx::channel a, b;
  ASSERT_EQ(zx::channel::create(0, &a, &b), ZX_OK);

  const char msg[] = "flake-lab";
  ASSERT_EQ(a.write(0, msg, sizeof(msg), nullptr, 0), ZX_OK);

  char buf[32] = {};
  uint32_t actual = 0;
  ASSERT_EQ(b.read(0, buf, nullptr, sizeof(buf), 0, &actual, nullptr), ZX_OK);
  EXPECT_EQ(actual, sizeof(msg));
  EXPECT_STREQ(buf, msg);
}

TEST(StableZircon, VmoReadBackWhatWasWritten) {
  zx::vmo vmo;
  ASSERT_EQ(zx::vmo::create(4096, 0, &vmo), ZX_OK);

  const uint64_t value = 0x0123456789abcdefULL;
  ASSERT_EQ(vmo.write(&value, 128, sizeof(value)), ZX_OK);

  uint64_t out = 0;
  ASSERT_EQ(vmo.read(&out, 128, sizeof(out)), ZX_OK);
  EXPECT_EQ(out, value);
}

TEST(StableZircon, EventSignalIsObserved) {
  zx::event event;
  ASSERT_EQ(zx::event::create(0, &event), ZX_OK);
  ASSERT_EQ(event.signal(0, ZX_USER_SIGNAL_0), ZX_OK);

  zx_signals_t observed = 0;
  // The signal is already set, so an infinite-past deadline must still succeed.
  ASSERT_EQ(event.wait_one(ZX_USER_SIGNAL_0, zx::time::infinite_past(), &observed), ZX_OK);
  EXPECT_TRUE(observed & ZX_USER_SIGNAL_0);
}

}  // namespace
