#include <array>
#include <cstdint>
#include <stdexcept>
#include <string_view>
#include <utility>

#include "edit_session_queue.h"

using caps_writer::tsf::EditSessionQueue;
using caps_writer::tsf::Frame;
using caps_writer::tsf::Operation;

namespace {

void Check(bool condition, std::string_view message) {
    if (!condition) {
        throw std::runtime_error(message.data());
    }
}

Frame MakeFrame(
    Operation operation,
    const std::array<std::uint8_t, 16>& session,
    std::uint64_t revision) {
    Frame frame{};
    frame.header.magic = caps_writer::tsf::kMagic;
    frame.header.version = caps_writer::tsf::kVersion;
    frame.header.operation = static_cast<std::uint16_t>(operation);
    frame.header.session_id = session;
    frame.header.revision = revision;
    return frame;
}

void TestAllowsOnlyOneOutstandingEdit() {
    constexpr std::array<std::uint8_t, 16> session{1};
    EditSessionQueue queue;
    queue.Push(MakeFrame(Operation::Begin, session, 1));
    queue.Push(MakeFrame(Operation::Revise, session, 2));

    auto begin = queue.StartNext();
    Check(begin.has_value(), "BEGIN should start");
    Check(
        begin->header.operation == static_cast<std::uint16_t>(Operation::Begin),
        "BEGIN must be first");
    Check(queue.outstanding(), "queue must report an outstanding edit");
    Check(!queue.StartNext().has_value(), "a second edit must not start");

    queue.Complete();
    auto revision = queue.StartNext();
    Check(revision.has_value(), "revision should start after completion");
    Check(revision->header.revision == 2, "revision order changed");
}

void TestCoalescesOnlyConsecutiveSameSessionRevisions() {
    constexpr std::array<std::uint8_t, 16> first_session{1};
    constexpr std::array<std::uint8_t, 16> second_session{2};
    EditSessionQueue queue;

    queue.Push(MakeFrame(Operation::Begin, first_session, 1));
    auto begin = queue.StartNext();
    Check(begin.has_value(), "BEGIN should start");

    queue.Push(MakeFrame(Operation::Revise, first_session, 2));
    auto superseded = queue.Push(MakeFrame(Operation::Revise, first_session, 3));
    Check(superseded.has_value(), "older pending revision should be superseded");
    Check(superseded->header.revision == 2, "wrong revision was superseded");
    Check(queue.pending_size() == 1, "same-session revisions were not coalesced");

    Check(
        !queue.Push(MakeFrame(Operation::Revise, second_session, 4)).has_value(),
        "different-session revision must not be coalesced");
    Check(queue.pending_size() == 2, "different-session revision was lost");

    auto stale = queue.Push(MakeFrame(Operation::Revise, second_session, 3));
    Check(stale.has_value(), "out-of-order revision should be rejected");
    Check(stale->header.revision == 3, "newer pending revision was replaced by stale data");
    Check(queue.pending_size() == 2, "stale revision changed queue size");
}

void TestCommitIsAFenceForPendingRevision() {
    constexpr std::array<std::uint8_t, 16> session{1};
    EditSessionQueue queue;
    queue.Push(MakeFrame(Operation::Begin, session, 1));
    auto begin = queue.StartNext();
    Check(begin.has_value(), "BEGIN should start");

    queue.Push(MakeFrame(Operation::Revise, session, 2));
    auto superseded = queue.Push(MakeFrame(Operation::Revise, session, 3));
    Check(superseded.has_value(), "pending revision should coalesce");
    queue.Push(MakeFrame(Operation::Commit, session, 4));
    queue.Push(MakeFrame(Operation::Revise, session, 5));

    queue.Complete();
    auto latest_before_commit = queue.StartNext();
    Check(latest_before_commit.has_value(), "pre-commit revision is missing");
    Check(latest_before_commit->header.revision == 3, "commit crossed latest revision");

    queue.Complete();
    auto commit = queue.StartNext();
    Check(commit.has_value(), "commit is missing");
    Check(
        commit->header.operation == static_cast<std::uint16_t>(Operation::Commit),
        "commit did not follow its target revision");

    queue.Complete();
    auto after_commit = queue.StartNext();
    Check(after_commit.has_value(), "post-commit frame is missing");
    Check(after_commit->header.revision == 5, "revision crossed commit boundary");
}

void TestCancelAndBeginRemainOrderingBoundaries() {
    constexpr std::array<std::uint8_t, 16> session{1};
    constexpr std::array<std::uint8_t, 16> next_session{2};
    EditSessionQueue queue;
    queue.Push(MakeFrame(Operation::Revise, session, 2));
    queue.Push(MakeFrame(Operation::Cancel, session, 3));
    queue.Push(MakeFrame(Operation::Begin, next_session, 1));
    queue.Push(MakeFrame(Operation::Revise, next_session, 2));

    const std::array<Operation, 4> expected{
        Operation::Revise,
        Operation::Cancel,
        Operation::Begin,
        Operation::Revise,
    };
    for (const Operation operation : expected) {
        auto frame = queue.StartNext();
        Check(frame.has_value(), "control-boundary frame is missing");
        Check(
            frame->header.operation == static_cast<std::uint16_t>(operation),
            "control operation ordering changed");
        queue.Complete();
    }
}

}  // namespace

int main() {
    TestAllowsOnlyOneOutstandingEdit();
    TestCoalescesOnlyConsecutiveSameSessionRevisions();
    TestCommitIsAFenceForPendingRevision();
    TestCancelAndBeginRemainOrderingBoundaries();
    return 0;
}
