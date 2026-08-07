#include <array>
#include <cstdlib>
#include <deque>
#include <iostream>
#include <utility>

#include "outgoing_frame_queue.h"

namespace {

caps_writer::tsf::Frame MakeSnapshot(
    const std::array<std::uint8_t, 16>& session,
    std::uint64_t revision,
    bool baseline = false) {
    caps_writer::tsf::Frame frame{};
    frame.header.operation = static_cast<std::uint16_t>(
        caps_writer::tsf::Operation::TrackingSnapshot);
    frame.header.session_id = session;
    frame.header.revision = revision;
    frame.header.status = baseline ? 0U : 1U;
    return frame;
}

void Check(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << '\n';
        std::exit(1);
    }
}

}  // namespace

int main() {
    constexpr std::array<std::uint8_t, 16> first_session{1};
    constexpr std::array<std::uint8_t, 16> second_session{2};
    std::deque<caps_writer::tsf::Frame> queue;

    caps_writer::tsf::EnqueueTrackingSnapshot(
        queue, MakeSnapshot(first_session, 1, true), 4);
    caps_writer::tsf::EnqueueTrackingSnapshot(
        queue, MakeSnapshot(first_session, 2), 4);
    const bool replaced = caps_writer::tsf::EnqueueTrackingSnapshot(
        queue, MakeSnapshot(first_session, 3), 4);
    Check(replaced, "replacement should be reported");
    Check(queue.size() == 2, "current snapshots should be coalesced");
    Check(queue.front().header.status == 0, "baseline snapshot was replaced");
    Check(queue.back().header.revision == 3, "latest snapshot was not retained");

    caps_writer::tsf::EnqueueTrackingSnapshot(
        queue, MakeSnapshot(second_session, 1), 4);
    Check(queue.size() == 3, "a different session snapshot was lost");

    caps_writer::tsf::EnqueueTrackingSnapshot(
        queue, MakeSnapshot(first_session, 4), 4);
    Check(queue.size() == 3, "replaced snapshot changed queue size");
    Check(queue.back().header.revision == 4, "replacement should remain newest");

    caps_writer::tsf::EnqueueTrackingSnapshot(
        queue, MakeSnapshot(second_session, 2, true), 3);
    Check(queue.size() == 3, "bounded queue size was exceeded");
    Check(queue.back().header.revision == 2, "new frame was not queued");
    return 0;
}
