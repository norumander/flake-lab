// Flaky because the client gives up before a cold server has started.
//
// The test component has a child, slow_server, that the component framework
// launches on the first connection to its Echo protocol. Connecting early is
// safe on Fuchsia: the request waits in the channel until the server serves its
// outgoing directory. What fails is the fixed deadline below, which is shorter
// than the slowest server start. The server's start-up delay is uniform in
// [0, 100) ms and the reply arrived about 2 ms after that when measured, so a
// 95 ms deadline should fail roughly 7% of the time.

#include <fidl/flakelab.echo/cpp/fidl.h>
#include <lib/async-loop/cpp/loop.h>
#include <lib/async-loop/default.h>
#include <lib/component/incoming/cpp/protocol.h>
#include <lib/zx/clock.h>
#include <lib/zx/time.h>

#include <gtest/gtest.h>

namespace {

TEST(FlakyStartup, ReplyBeforeDeadline) {
  constexpr zx::duration kDeadline = zx::msec(95);

  async::Loop loop(&kAsyncLoopConfigAttachToCurrentThread);
  zx::result client_end = component::Connect<flakelab_echo::Echo>();
  ASSERT_TRUE(client_end.is_ok()) << client_end.status_string();
  fidl::Client client(std::move(*client_end), loop.dispatcher());

  const zx::time start = zx::clock::get_monotonic();
  bool replied = false;
  client->EchoString({"ping"}).Then([&](fidl::Result<flakelab_echo::Echo::EchoString>& result) {
    replied = result.is_ok() && result->response() == "ping";
    loop.Quit();
  });

  // BUG (deliberate): the deadline assumes the server is already running.
  loop.Run(zx::deadline_after(kDeadline));
  const zx::duration waited = zx::clock::get_monotonic() - start;

  EXPECT_TRUE(replied) << "no reply after " << waited.to_msecs() << " ms";
}

}  // namespace
