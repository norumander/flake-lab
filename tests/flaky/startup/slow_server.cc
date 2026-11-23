// An Echo server whose start-up takes a different amount of time on every
// launch. It stands in for a real service with slow, variable initialisation.

#include <fidl/flakelab.echo/cpp/fidl.h>
#include <lib/async-loop/cpp/loop.h>
#include <lib/async-loop/default.h>
#include <lib/component/outgoing/cpp/outgoing_directory.h>
#include <lib/zx/time.h>
#include <zircon/syscalls.h>

#include <cstdint>
#include <memory>

namespace {

// Start-up delay is uniform in [0, kMaxStartupDelayMs).
constexpr uint32_t kMaxStartupDelayMs = 100;

class EchoImpl : public fidl::Server<flakelab_echo::Echo> {
 public:
  void EchoString(EchoStringRequest& request, EchoStringCompleter::Sync& completer) override {
    completer.Reply({{request.value()}});
  }
};

}  // namespace

int main() {
  uint32_t random = 0;
  zx_cprng_draw(&random, sizeof(random));
  zx::nanosleep(zx::deadline_after(zx::msec(random % kMaxStartupDelayMs)));

  async::Loop loop(&kAsyncLoopConfigAttachToCurrentThread);
  component::OutgoingDirectory outgoing(loop.dispatcher());
  if (outgoing.AddProtocol<flakelab_echo::Echo>(std::make_unique<EchoImpl>()).is_error()) {
    return 1;
  }
  if (outgoing.ServeFromStartupInfo().is_error()) {
    return 1;
  }
  return loop.Run();
}
