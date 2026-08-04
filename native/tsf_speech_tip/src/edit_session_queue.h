#pragma once

#include <deque>
#include <optional>
#include <utility>

#include "protocol.h"

namespace caps_writer::tsf {

// Activation-thread-only queue for TSF edit requests. Only a frame returned by
// StartNext() may own an outstanding ITfEditSession. Consecutive REVISE frames
// for the same session are snapshots, so only the newest pending snapshot must
// reach TSF. Control operations stay in the deque and therefore form ordering
// boundaries that revisions cannot cross.
class EditSessionQueue {
public:
    std::optional<Frame> Push(Frame frame) {
        const auto operation = static_cast<Operation>(frame.header.operation);
        if (operation == Operation::Revise && !pending_.empty()) {
            Frame& tail = pending_.back();
            if (static_cast<Operation>(tail.header.operation) == Operation::Revise &&
                SameSession(tail.header.session_id, frame.header.session_id)) {
                if (frame.header.revision <= tail.header.revision) {
                    return frame;
                }
                Frame superseded = std::move(tail);
                tail = std::move(frame);
                return superseded;
            }
        }
        pending_.push_back(std::move(frame));
        return std::nullopt;
    }

    std::optional<Frame> StartNext() {
        if (outstanding_ || pending_.empty()) {
            return std::nullopt;
        }
        outstanding_ = true;
        Frame next = std::move(pending_.front());
        pending_.pop_front();
        return next;
    }

    void Complete() noexcept { outstanding_ = false; }

    std::deque<Frame> AbandonAndDrain() noexcept {
        outstanding_ = false;
        std::deque<Frame> abandoned;
        abandoned.swap(pending_);
        return abandoned;
    }

    void Reset() noexcept {
        outstanding_ = false;
        pending_.clear();
    }

    [[nodiscard]] bool outstanding() const noexcept { return outstanding_; }
    [[nodiscard]] std::size_t pending_size() const noexcept { return pending_.size(); }

private:
    bool outstanding_ = false;
    std::deque<Frame> pending_;
};

}  // namespace caps_writer::tsf
