#pragma once

#include <msctf.h>

namespace caps_writer::tsf {

// ITfTextEditSink is also notified for selection and property-only edits.  Ask
// the edit record whether this transaction contains any text changes before
// entering the host text store.  Unknown or failed records are processed
// conservatively so a host-specific implementation cannot disable tracking.
inline bool MayContainTextUpdates(ITfEditRecord* edit_record) noexcept {
    if (edit_record == nullptr) {
        return true;
    }

    IEnumTfRanges* ranges = nullptr;
    const HRESULT result = edit_record->GetTextAndPropertyUpdates(
        TF_GTP_INCL_TEXT, nullptr, 0, &ranges);
    if (FAILED(result) || ranges == nullptr) {
        if (ranges != nullptr) {
            ranges->Release();
        }
        return true;
    }

    // Skip avoids materializing an ITfRange when only the existence of a text
    // update matters.  S_FALSE means the enumerator contained no ranges.
    const HRESULT skip_result = ranges->Skip(1);
    ranges->Release();
    return skip_result != S_FALSE;
}

}  // namespace caps_writer::tsf
