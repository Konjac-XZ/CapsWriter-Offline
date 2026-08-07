#pragma once

#include <string>

namespace caps_writer::tsf {

inline constexpr wchar_t kContextSnapshotPrefix[] = L"CWCTX1\n";

inline std::wstring EncodeContextSnapshot(
    const std::wstring& prefix,
    const std::wstring& selection,
    const std::wstring& suffix,
    unsigned long active_end) {
    std::wstring payload = kContextSnapshotPrefix;
    payload.reserve(prefix.size() + selection.size() + suffix.size() + 48);
    payload += std::to_wstring(prefix.size());
    payload += L'\n';
    payload += std::to_wstring(selection.size());
    payload += L'\n';
    payload += std::to_wstring(active_end);
    payload += L'\n';
    payload += prefix;
    payload += selection;
    payload += suffix;
    return payload;
}

}  // namespace caps_writer::tsf
