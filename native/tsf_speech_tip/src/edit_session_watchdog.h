#pragma once

#include <cstdint>
#include <optional>

namespace caps_writer::tsf {

// Activation-thread-only generation tracker. A timed-out ITfEditSession can
// still be invoked by a host later, so recovery must make that callback stale
// before allowing another request to mutate TSF state.
class EditSessionWatchdogState {
public:
    std::uint64_t Begin() noexcept {
        outstanding_ = ++next_request_id_;
        return *outstanding_;
    }

    [[nodiscard]] bool IsCurrent(std::uint64_t request_id) const noexcept {
        return outstanding_.has_value() && *outstanding_ == request_id;
    }

    bool Complete(std::uint64_t request_id) noexcept {
        if (!IsCurrent(request_id)) {
            return false;
        }
        outstanding_.reset();
        return true;
    }

    std::optional<std::uint64_t> Expire() noexcept {
        auto expired = outstanding_;
        outstanding_.reset();
        return expired;
    }

    void Reset() noexcept { outstanding_.reset(); }

private:
    std::uint64_t next_request_id_ = 0;
    std::optional<std::uint64_t> outstanding_;
};

}  // namespace caps_writer::tsf
