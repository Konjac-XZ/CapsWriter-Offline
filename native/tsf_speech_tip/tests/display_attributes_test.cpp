#include <cstdlib>
#include <iostream>

#include "display_attributes.h"

namespace {

void Expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << '\n';
        std::exit(EXIT_FAILURE);
    }
}

}  // namespace

int main() {
    using caps_writer::tsf::CompositionStyle;
    using caps_writer::tsf::DisplayAttributeGuid;
    using caps_writer::tsf::MakeDisplayAttribute;
    using caps_writer::tsf::ParseCompositionStyle;

    const TF_DISPLAYATTRIBUTE transcription =
        MakeDisplayAttribute(CompositionStyle::Transcription);
    const TF_DISPLAYATTRIBUTE polishing =
        MakeDisplayAttribute(CompositionStyle::Polishing);

    Expect(transcription.lsStyle == TF_LS_DASH,
           "transcription must use a dashed underline");
    Expect(polishing.lsStyle == TF_LS_SOLID,
           "polishing must use a solid underline");
    Expect(transcription.fBoldLine == FALSE && polishing.fBoldLine == FALSE,
           "composition underlines must use normal weight");
    Expect(transcription.crLine.type == TF_CT_SYSCOLOR,
           "transcription underline must follow a system color");
    Expect(polishing.crLine.type == TF_CT_SYSCOLOR,
           "polishing underline must follow a system color");
    Expect(DisplayAttributeGuid(CompositionStyle::Transcription) !=
               DisplayAttributeGuid(CompositionStyle::Polishing),
           "composition states must use distinct display attribute GUIDs");
    Expect(ParseCompositionStyle(999) == CompositionStyle::Transcription,
           "unknown peers must safely default to transcription styling");
    return EXIT_SUCCESS;
}
