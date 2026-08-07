#pragma once

#include <algorithm>
#include <cstddef>
#include <deque>
#include <utility>

#include "protocol.h"

namespace caps_writer::tsf {

inline constexpr std::uint32_t kCurrentTrackingSnapshotStatus = 1;

inline bool EnqueueTrackingSnapshot(
    std::deque<Frame>& outgoing,
    Frame frame,
    std::size_t maximum_frames) {
    const bool is_current =
        frame.header.operation ==
            static_cast<std::uint16_t>(Operation::TrackingSnapshot) &&
        frame.header.status == kCurrentTrackingSnapshotStatus;
    bool replaced = false;
    if (is_current) {
        const auto existing = std::find_if(
            outgoing.begin(),
            outgoing.end(),
            [&frame](const Frame& queued) {
                return queued.header.operation == frame.header.operation &&
                    queued.header.status == kCurrentTrackingSnapshotStatus &&
                    SameSession(
                        queued.header.session_id, frame.header.session_id);
            });
        if (existing != outgoing.end()) {
            outgoing.erase(existing);
            replaced = true;
        }
    }

    if (maximum_frames == 0) {
        return replaced;
    }
    if (outgoing.size() >= maximum_frames) {
        outgoing.pop_front();
    }
    outgoing.push_back(std::move(frame));
    return replaced;
}

}  // namespace caps_writer::tsf
