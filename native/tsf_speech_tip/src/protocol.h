#pragma once

#include <array>
#include <cstdint>
#include <string>

namespace caps_writer::tsf {

inline constexpr std::uint32_t kMagic = 0x50545743;
inline constexpr std::uint16_t kVersion = 1;
inline constexpr wchar_t kPipeName[] = LR"(\\.\pipe\CapsWriter.TsfSpeechTip.v1)";
inline constexpr std::uint32_t kMaxTextBytes = 4 * 1024 * 1024;

enum class Operation : std::uint16_t {
    Hello = 0,
    Begin = 1,
    Revise = 2,
    Commit = 3,
    Cancel = 4,
    Ping = 5,
    CompositionTerminated = 6,
    QueryContext = 7,
    EditSessionWatchdog = 8,
    TrackedTextChanged = 9,
    TrackingDiagnostic = 10,
    TrackingSnapshot = 11,
    AckFlag = 0x8000,
};

enum class Status : std::uint32_t {
    Applied = 0,
    IgnoredNotForeground = 1,
    StaleRevision = 2,
    NoContext = 3,
    EditSessionFailed = 4,
    InactiveSession = 5,
    Queued = 6,
    InvalidFrame = 7,
    EditSessionTimeout = 8,
};

// Request frames use FrameHeader::status for visual composition state. ACK
// frames use the same field for Status. Values default safely for older peers.
enum class CompositionStyle : std::uint32_t {
    Transcription = 0,
    Polishing = 1,
};

#pragma pack(push, 1)
struct FrameHeader {
    std::uint32_t magic;
    std::uint16_t version;
    std::uint16_t operation;
    std::uint64_t revision;
    std::array<std::uint8_t, 16> session_id;
    std::uint32_t text_bytes;
    std::uint32_t status;
};
#pragma pack(pop)

static_assert(sizeof(FrameHeader) == 40);

struct Frame {
    FrameHeader header{};
    std::wstring text;
};

inline bool SameSession(
    const std::array<std::uint8_t, 16>& left,
    const std::array<std::uint8_t, 16>& right) noexcept {
    return left == right;
}

}  // namespace caps_writer::tsf
