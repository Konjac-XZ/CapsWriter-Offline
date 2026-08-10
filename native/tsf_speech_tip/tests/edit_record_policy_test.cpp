#include <windows.h>
#include <msctf.h>

#include <atomic>
#include <cstdlib>
#include <iostream>

#include "edit_record_policy.h"

namespace {

class Range final : public ITfRange {
public:
    Range(LONG start, LONG end) : start_(start), end_(end) {}

    STDMETHODIMP QueryInterface(REFIID, void** object) override {
        if (object == nullptr) {
            return E_INVALIDARG;
        }
        *object = static_cast<ITfRange*>(this);
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
    STDMETHODIMP GetText(TfEditCookie, DWORD, WCHAR*, ULONG, ULONG*) override {
        return E_NOTIMPL;
    }
    STDMETHODIMP SetText(TfEditCookie, DWORD, const WCHAR*, LONG) override {
        return E_NOTIMPL;
    }
    STDMETHODIMP GetFormattedText(TfEditCookie, IDataObject**) override {
        return E_NOTIMPL;
    }
    STDMETHODIMP GetEmbedded(TfEditCookie, REFGUID, REFIID, IUnknown**) override {
        return E_NOTIMPL;
    }
    STDMETHODIMP InsertEmbedded(TfEditCookie, DWORD, IDataObject*) override {
        return E_NOTIMPL;
    }
    STDMETHODIMP ShiftStart(TfEditCookie, LONG, LONG*, const TF_HALTCOND*) override {
        return E_NOTIMPL;
    }
    STDMETHODIMP ShiftEnd(TfEditCookie, LONG, LONG*, const TF_HALTCOND*) override {
        return E_NOTIMPL;
    }
    STDMETHODIMP ShiftStartToRange(TfEditCookie, ITfRange*, TfAnchor) override {
        return E_NOTIMPL;
    }
    STDMETHODIMP ShiftEndToRange(TfEditCookie, ITfRange*, TfAnchor) override {
        return E_NOTIMPL;
    }
    STDMETHODIMP ShiftStartRegion(TfEditCookie, TfShiftDir, BOOL*) override {
        return E_NOTIMPL;
    }
    STDMETHODIMP ShiftEndRegion(TfEditCookie, TfShiftDir, BOOL*) override {
        return E_NOTIMPL;
    }
    STDMETHODIMP IsEmpty(TfEditCookie, BOOL*) override { return E_NOTIMPL; }
    STDMETHODIMP Collapse(TfEditCookie, TfAnchor) override { return E_NOTIMPL; }
    STDMETHODIMP IsEqualStart(TfEditCookie, ITfRange*, TfAnchor, BOOL*) override {
        return E_NOTIMPL;
    }
    STDMETHODIMP IsEqualEnd(TfEditCookie, ITfRange*, TfAnchor, BOOL*) override {
        return E_NOTIMPL;
    }
    STDMETHODIMP CompareStart(
        TfEditCookie,
        ITfRange* other,
        TfAnchor anchor,
        LONG* result) override {
        return Compare(start_, other, anchor, result);
    }
    STDMETHODIMP CompareEnd(
        TfEditCookie,
        ITfRange* other,
        TfAnchor anchor,
        LONG* result) override {
        return Compare(end_, other, anchor, result);
    }
    STDMETHODIMP AdjustForInsert(TfEditCookie, ULONG, BOOL*) override {
        return E_NOTIMPL;
    }
    STDMETHODIMP GetGravity(TfGravity*, TfGravity*) override { return E_NOTIMPL; }
    STDMETHODIMP SetGravity(TfEditCookie, TfGravity, TfGravity) override {
        return E_NOTIMPL;
    }
    STDMETHODIMP Clone(ITfRange**) override { return E_NOTIMPL; }
    STDMETHODIMP GetContext(ITfContext**) override { return E_NOTIMPL; }

private:
    HRESULT Compare(
        LONG position,
        ITfRange* other,
        TfAnchor anchor,
        LONG* result) const {
        if (other == nullptr || result == nullptr) {
            return E_INVALIDARG;
        }
        const auto* range = dynamic_cast<Range*>(other);
        if (range == nullptr) {
            return E_FAIL;
        }
        const LONG other_position = anchor == TF_ANCHOR_START
            ? range->start_
            : range->end_;
        *result = position < other_position ? -1L : position > other_position ? 1L : 0L;
        return S_OK;
    }

    std::atomic<ULONG> references_{1};
    LONG start_;
    LONG end_;
};

class RangeEnumerator final : public IEnumTfRanges {
public:
    RangeEnumerator(HRESULT next_result, ITfRange* range)
        : next_result_(next_result), range_(range) {
        if (range_ != nullptr) {
            range_->AddRef();
        }
    }

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
            if (range_ != nullptr) {
                range_->Release();
            }
            delete this;
        }
        return remaining;
    }
    STDMETHODIMP Clone(IEnumTfRanges**) override { return E_NOTIMPL; }
    STDMETHODIMP Next(ULONG count, ITfRange** range, ULONG* fetched) override {
        if (count != 1 || range == nullptr || fetched == nullptr) {
            return E_INVALIDARG;
        }
        *range = nullptr;
        *fetched = 0;
        if (delivered_) {
            return S_FALSE;
        }
        delivered_ = true;
        if (SUCCEEDED(next_result_) && next_result_ != S_FALSE && range_ != nullptr) {
            range_->AddRef();
            *range = range_;
            *fetched = 1;
        }
        return next_result_;
    }
    STDMETHODIMP Reset() override { return E_NOTIMPL; }
    STDMETHODIMP Skip(ULONG) override { return E_NOTIMPL; }

private:
    std::atomic<ULONG> references_{1};
    HRESULT next_result_;
    ITfRange* range_;
    bool delivered_ = false;
};

class EditRecord final : public ITfEditRecord {
public:
    EditRecord(HRESULT query_result, HRESULT next_result, ITfRange* range = nullptr)
        : query_result_(query_result), next_result_(next_result), range_(range) {}

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
        *ranges = new RangeEnumerator(next_result_, range_);
        return S_OK;
    }

private:
    std::atomic<ULONG> references_{1};
    HRESULT query_result_;
    HRESULT next_result_;
    ITfRange* range_;
};

void Check(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << '\n';
        std::exit(1);
    }
}

}  // namespace

int main() {
    using caps_writer::tsf::ClassifyRangeRelation;
    using caps_writer::tsf::InspectTextUpdates;
    using caps_writer::tsf::IntersectsTrackedInterior;
    using caps_writer::tsf::MayAffectTrackedRange;
    using caps_writer::tsf::RangeAnchorComparisons;
    using caps_writer::tsf::RangeRelation;
    using caps_writer::tsf::TextUpdateRelevance;

    const auto comparisons = [](LONG changed_start, LONG changed_end) {
        constexpr LONG tracked_start = 10;
        constexpr LONG tracked_end = 20;
        const auto compare = [](LONG left, LONG right) {
            return left < right ? -1L : left > right ? 1L : 0L;
        };
        return RangeAnchorComparisons{
            compare(changed_start, tracked_start),
            compare(changed_start, tracked_end),
            compare(changed_end, tracked_start),
            compare(changed_end, tracked_end),
        };
    };

    Check(
        ClassifyRangeRelation(comparisons(1, 9)) == RangeRelation::Before,
        "a preceding edit must be classified as before");
    Check(
        ClassifyRangeRelation(comparisons(1, 10)) == RangeRelation::TouchesStart,
        "an edit ending at the start anchor must touch the start");
    Check(
        ClassifyRangeRelation(comparisons(1, 11)) == RangeRelation::OverlapsStart,
        "an edit crossing the start anchor must overlap the start");
    Check(
        ClassifyRangeRelation(comparisons(10, 20)) == RangeRelation::Inside,
        "an equal range is an internal edit");
    Check(
        ClassifyRangeRelation(comparisons(11, 19)) == RangeRelation::Inside,
        "a contained edit must be classified as inside");
    Check(
        ClassifyRangeRelation(comparisons(9, 21)) == RangeRelation::Covers,
        "an edit surrounding the tracker must cover it");
    Check(
        ClassifyRangeRelation(comparisons(19, 21)) == RangeRelation::OverlapsEnd,
        "an edit crossing the end anchor must overlap the end");
    Check(
        ClassifyRangeRelation(comparisons(20, 21)) == RangeRelation::TouchesEnd,
        "an edit beginning at the end anchor must touch the end");
    Check(
        ClassifyRangeRelation(comparisons(21, 30)) == RangeRelation::After,
        "a following edit must be classified as after");
    Check(
        !MayAffectTrackedRange(RangeRelation::Before),
        "preceding edits must not wake the tracker");
    Check(
        !MayAffectTrackedRange(RangeRelation::After),
        "following edits must not wake the tracker");
    Check(
        MayAffectTrackedRange(RangeRelation::TouchesStart),
        "start-boundary edits require a conservative text check");
    Check(
        MayAffectTrackedRange(RangeRelation::TouchesEnd),
        "end-boundary edits require a conservative text check");
    Check(
        MayAffectTrackedRange(RangeRelation::Unknown),
        "unknown range relationships must be processed conservatively");
    Check(
        !IntersectsTrackedInterior(RangeRelation::TouchesStart),
        "a composition touching only the start is outside the tracked text");
    Check(
        !IntersectsTrackedInterior(RangeRelation::TouchesEnd),
        "a composition touching only the end is outside the tracked text");
    Check(
        IntersectsTrackedInterior(RangeRelation::Inside),
        "a composition inside the tracker must pause tracking");
    Check(
        IntersectsTrackedInterior(RangeRelation::OverlapsEnd),
        "a composition crossing the end must pause tracking");
    Check(
        IntersectsTrackedInterior(RangeRelation::Unknown),
        "unknown composition relationships must be handled conservatively");

    auto* tracked = new Range(10, 20);
    auto* preceding_range = new Range(1, 9);
    auto* preceding = new EditRecord(S_OK, S_OK, preceding_range);
    Check(
        InspectTextUpdates(preceding, tracked, 1) ==
            TextUpdateRelevance::Unrelated,
        "a concrete preceding text update must be skipped");
    preceding->Release();
    preceding_range->Release();

    auto* following_range = new Range(21, 30);
    auto* following = new EditRecord(S_OK, S_OK, following_range);
    Check(
        InspectTextUpdates(following, tracked, 1) ==
            TextUpdateRelevance::Unrelated,
        "a concrete following text update must be skipped");
    following->Release();
    following_range->Release();

    auto* internal_range = new Range(12, 13);
    auto* internal = new EditRecord(S_OK, S_OK, internal_range);
    Check(
        InspectTextUpdates(internal, tracked, 1) == TextUpdateRelevance::Related,
        "a concrete internal text update must wake the tracker");
    internal->Release();
    internal_range->Release();

    auto* end_boundary_range = new Range(20, 21);
    auto* end_boundary = new EditRecord(S_OK, S_OK, end_boundary_range);
    Check(
        InspectTextUpdates(end_boundary, tracked, 1) ==
            TextUpdateRelevance::Related,
        "a concrete boundary update must trigger a conservative text check");
    end_boundary->Release();
    end_boundary_range->Release();
    tracked->Release();

    Check(
        InspectTextUpdates(nullptr, nullptr, 0) == TextUpdateRelevance::Unknown,
        "missing edit records must be processed conservatively");

    auto* empty = new EditRecord(S_OK, S_FALSE);
    Check(
        InspectTextUpdates(empty, reinterpret_cast<ITfRange*>(1), 0) ==
            TextUpdateRelevance::None,
        "property-only edits should be skipped");
    empty->Release();

    auto* broken_enumerator = new EditRecord(S_OK, E_NOTIMPL);
    Check(
        InspectTextUpdates(
            broken_enumerator, reinterpret_cast<ITfRange*>(1), 0) ==
            TextUpdateRelevance::Unknown,
        "failed range enumeration must be processed conservatively");
    broken_enumerator->Release();

    auto* failed = new EditRecord(E_FAIL, S_FALSE);
    Check(
        InspectTextUpdates(failed, reinterpret_cast<ITfRange*>(1), 0) ==
            TextUpdateRelevance::Unknown,
        "failed edit records must be processed conservatively");
    failed->Release();
    return 0;
}
