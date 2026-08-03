#pragma once

#include <windows.h>

namespace caps_writer::tsf {

// EndComposition is irreversible. A cancellation may cross that boundary only
// after both the replaced text and its original selection have been restored.
inline bool CanEndCancellation(
    HRESULT text_restore_result,
    HRESULT selection_restore_result) noexcept {
    return SUCCEEDED(text_restore_result) && SUCCEEDED(selection_restore_result);
}

}  // namespace caps_writer::tsf
