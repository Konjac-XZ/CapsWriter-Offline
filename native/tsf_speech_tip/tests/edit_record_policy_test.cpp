#include <windows.h>
#include <msctf.h>

#include <atomic>
#include <cstdlib>
#include <iostream>

#include "edit_record_policy.h"

namespace {

class RangeEnumerator final : public IEnumTfRanges {
public:
    explicit RangeEnumerator(HRESULT skip_result) : skip_result_(skip_result) {}

    STDMETHODIMP QueryInterface(REFIID, void** object) override {
        if (object == nullptr) {
            return E_INVALIDARG;
        }
        *object = static_cast<IEnumTfRanges*>(this);
        AddRef();
        return S_OK;
    }
    STDMETHODIMP_(ULONG) AddRef() override { return ++references_; }
    STDMETHODIMP_(ULONG) Release() override {
        const ULONG remaining = --references_;
        if (remaining == 0) {
            delete this;
        }
        return remaining;
    }
    STDMETHODIMP Clone(IEnumTfRanges**) override { return E_NOTIMPL; }
    STDMETHODIMP Next(ULONG, ITfRange**, ULONG*) override { return E_NOTIMPL; }
    STDMETHODIMP Reset() override { return E_NOTIMPL; }
    STDMETHODIMP Skip(ULONG count) override {
        return count == 1 ? skip_result_ : E_INVALIDARG;
    }

private:
    std::atomic<ULONG> references_{1};
    HRESULT skip_result_;
};

class EditRecord final : public ITfEditRecord {
public:
    EditRecord(HRESULT query_result, HRESULT skip_result)
        : query_result_(query_result), skip_result_(skip_result) {}

    STDMETHODIMP QueryInterface(REFIID, void** object) override {
        if (object == nullptr) {
            return E_INVALIDARG;
        }
        *object = static_cast<ITfEditRecord*>(this);
        AddRef();
        return S_OK;
    }
    STDMETHODIMP_(ULONG) AddRef() override { return ++references_; }
    STDMETHODIMP_(ULONG) Release() override {
        const ULONG remaining = --references_;
        if (remaining == 0) {
            delete this;
        }
        return remaining;
    }
    STDMETHODIMP GetSelectionStatus(BOOL*) override { return E_NOTIMPL; }
    STDMETHODIMP GetTextAndPropertyUpdates(
        DWORD flags,
        const GUID**,
        ULONG property_count,
        IEnumTfRanges** ranges) override {
        if (ranges == nullptr || flags != TF_GTP_INCL_TEXT || property_count != 0) {
            return E_INVALIDARG;
        }
        *ranges = nullptr;
        if (FAILED(query_result_)) {
            return query_result_;
        }
        *ranges = new RangeEnumerator(skip_result_);
        return S_OK;
    }

private:
    std::atomic<ULONG> references_{1};
    HRESULT query_result_;
    HRESULT skip_result_;
};

void Check(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << '\n';
        std::exit(1);
    }
}

}  // namespace

int main() {
    Check(
        caps_writer::tsf::MayContainTextUpdates(nullptr),
        "missing edit records must be processed conservatively");

    auto* empty = new EditRecord(S_OK, S_FALSE);
    Check(
        !caps_writer::tsf::MayContainTextUpdates(empty),
        "property-only edits should be skipped");
    empty->Release();

    auto* changed = new EditRecord(S_OK, S_OK);
    Check(
        caps_writer::tsf::MayContainTextUpdates(changed),
        "text edits should be processed");
    changed->Release();

    auto* failed = new EditRecord(E_FAIL, S_FALSE);
    Check(
        caps_writer::tsf::MayContainTextUpdates(failed),
        "failed edit records must be processed conservatively");
    failed->Release();
    return 0;
}
