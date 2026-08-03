#include <windows.h>
#include <msctf.h>

#include <cstdlib>
#include <iostream>

#include "display_attributes.h"

namespace {

constexpr CLSID kTextServiceClsid = {
    0xb635f2d7,
    0x83a5,
    0x462d,
    {0xa3, 0xce, 0xda, 0x82, 0x84, 0xb4, 0x9d, 0x93},
};

using DllGetClassObjectFunction = HRESULT(STDAPICALLTYPE*)(
    REFCLSID,
    REFIID,
    void**);

void Expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << '\n';
        std::exit(EXIT_FAILURE);
    }
}

void VerifyAttribute(
    ITfDisplayAttributeProvider* provider,
    REFGUID guid,
    TF_DA_LINESTYLE expected_style) {
    ITfDisplayAttributeInfo* info = nullptr;
    Expect(
        SUCCEEDED(provider->GetDisplayAttributeInfo(guid, &info)) && info != nullptr,
        "provider did not return the requested display attribute");
    TF_DISPLAYATTRIBUTE attribute{};
    Expect(SUCCEEDED(info->GetAttributeInfo(&attribute)),
           "display attribute information could not be read");
    Expect(attribute.lsStyle == expected_style,
           "provider returned the wrong underline style");
    info->Release();
}

}  // namespace

int wmain(int argument_count, wchar_t** arguments) {
    Expect(argument_count == 2, "expected the TIP DLL path");
    HMODULE module = LoadLibraryW(arguments[1]);
    Expect(module != nullptr, "failed to load the TIP DLL");
    const auto get_class_object = reinterpret_cast<DllGetClassObjectFunction>(
        GetProcAddress(module, "DllGetClassObject"));
    Expect(get_class_object != nullptr, "DllGetClassObject is not exported");

    IClassFactory* factory = nullptr;
    Expect(
        SUCCEEDED(get_class_object(
            kTextServiceClsid, IID_IClassFactory, reinterpret_cast<void**>(&factory))) &&
            factory != nullptr,
        "failed to obtain the TIP class factory");
    ITfDisplayAttributeProvider* provider = nullptr;
    Expect(
        SUCCEEDED(factory->CreateInstance(
            nullptr,
            IID_ITfDisplayAttributeProvider,
            reinterpret_cast<void**>(&provider))) &&
            provider != nullptr,
        "TIP class factory does not expose ITfDisplayAttributeProvider");
    factory->Release();

    VerifyAttribute(
        provider,
        caps_writer::tsf::kTranscriptionDisplayAttributeGuid,
        TF_LS_DASH);
    VerifyAttribute(
        provider,
        caps_writer::tsf::kPolishingDisplayAttributeGuid,
        TF_LS_SOLID);

    IEnumTfDisplayAttributeInfo* enumeration = nullptr;
    Expect(
        SUCCEEDED(provider->EnumDisplayAttributeInfo(&enumeration)) &&
            enumeration != nullptr,
        "provider did not expose its display attribute enumerator");
    ITfDisplayAttributeInfo* entries[2]{};
    ULONG fetched = 0;
    Expect(SUCCEEDED(enumeration->Next(2, entries, &fetched)) && fetched == 2,
           "provider did not enumerate both display attributes");
    entries[0]->Release();
    entries[1]->Release();
    enumeration->Release();
    provider->Release();

    Expect(FreeLibrary(module) != FALSE, "failed to unload the TIP DLL");
    return EXIT_SUCCESS;
}
