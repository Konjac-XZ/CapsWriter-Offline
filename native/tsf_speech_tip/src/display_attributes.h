#pragma once

#include <windows.h>
#include <msctf.h>

#include "protocol.h"

namespace caps_writer::tsf {

// {58B4E576-A03E-4CF0-B750-1437A88655E4}
inline constexpr GUID kTranscriptionDisplayAttributeGuid = {
    0x58b4e576,
    0xa03e,
    0x4cf0,
    {0xb7, 0x50, 0x14, 0x37, 0xa8, 0x86, 0x55, 0xe4},
};

// {07B451AE-028B-44A8-B680-ED9836A3A0F1}
inline constexpr GUID kPolishingDisplayAttributeGuid = {
    0x07b451ae,
    0x028b,
    0x44a8,
    {0xb6, 0x80, 0xed, 0x98, 0x36, 0xa3, 0xa0, 0xf1},
};

inline constexpr const GUID& DisplayAttributeGuid(CompositionStyle style) noexcept {
    return style == CompositionStyle::Polishing
        ? kPolishingDisplayAttributeGuid
        : kTranscriptionDisplayAttributeGuid;
}

inline TF_DISPLAYATTRIBUTE MakeDisplayAttribute(CompositionStyle style) noexcept {
    TF_DISPLAYATTRIBUTE attribute{};
    attribute.crText.type = TF_CT_NONE;
    attribute.crBk.type = TF_CT_NONE;
    attribute.lsStyle = style == CompositionStyle::Polishing
        ? TF_LS_SOLID
        : TF_LS_DASH;
    attribute.fBoldLine = FALSE;
    attribute.crLine.type = TF_CT_SYSCOLOR;
    attribute.crLine.nIndex = COLOR_HIGHLIGHT;
    attribute.bAttr = TF_ATTR_INPUT;
    return attribute;
}

inline CompositionStyle ParseCompositionStyle(std::uint32_t value) noexcept {
    return value == static_cast<std::uint32_t>(CompositionStyle::Polishing)
        ? CompositionStyle::Polishing
        : CompositionStyle::Transcription;
}

}  // namespace caps_writer::tsf
