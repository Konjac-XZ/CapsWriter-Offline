#include <iostream>
#include <string>

#include "context_snapshot.h"

namespace {

void Check(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << '\n';
        std::exit(1);
    }
}

}  // namespace

int main() {
    const std::wstring prefix = L"before";
    const std::wstring selection = L"selected";
    const std::wstring suffix = L"after\nline";
    const std::wstring payload = caps_writer::tsf::EncodeContextSnapshot(
        prefix, selection, suffix, 2);

    Check(payload.starts_with(L"CWCTX1\n6\n8\n2\n"), "metadata should be encoded");
    Check(
        payload.ends_with(prefix + selection + suffix),
        "text should follow metadata without escaping");
    return 0;
}
