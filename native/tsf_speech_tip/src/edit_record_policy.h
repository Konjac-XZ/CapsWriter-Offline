#pragma once

#include <msctf.h>

namespace caps_writer::tsf {

enum class RangeRelation {
    Before,
    TouchesStart,
    OverlapsStart,
    Inside,
    Covers,
    OverlapsEnd,
    TouchesEnd,
    After,
    Unknown,
};

enum class TextUpdateRelevance {
    None,
    Unrelated,
    Related,
    Unknown,
};

struct RangeAnchorComparisons {
    LONG start_to_tracked_start = 0;
    LONG start_to_tracked_end = 0;
    LONG end_to_tracked_start = 0;
    LONG end_to_tracked_end = 0;
};

constexpr RangeRelation ClassifyRangeRelation(
    const RangeAnchorComparisons& comparisons) noexcept {
    if (comparisons.end_to_tracked_start < 0) {
        return RangeRelation::Before;
    }
    if (comparisons.end_to_tracked_start == 0) {
        return RangeRelation::TouchesStart;
    }
    if (comparisons.start_to_tracked_end > 0) {
        return RangeRelation::After;
    }
    if (comparisons.start_to_tracked_end == 0) {
        return RangeRelation::TouchesEnd;
    }
    if (comparisons.start_to_tracked_start < 0) {
        return comparisons.end_to_tracked_end > 0
            ? RangeRelation::Covers
            : RangeRelation::OverlapsStart;
    }
    if (comparisons.end_to_tracked_end > 0) {
        return comparisons.start_to_tracked_start == 0
            ? RangeRelation::Covers
            : RangeRelation::OverlapsEnd;
    }
    return RangeRelation::Inside;
}

constexpr bool MayAffectTrackedRange(RangeRelation relation) noexcept {
    return relation != RangeRelation::Before &&
        relation != RangeRelation::After;
}

constexpr bool IntersectsTrackedInterior(RangeRelation relation) noexcept {
    return relation == RangeRelation::OverlapsStart ||
        relation == RangeRelation::Inside ||
        relation == RangeRelation::Covers ||
        relation == RangeRelation::OverlapsEnd ||
        relation == RangeRelation::Unknown;
}

inline RangeRelation CompareRangeToTracked(
    ITfRange* changed,
    ITfRange* tracked,
    TfEditCookie edit_cookie) noexcept {
    if (changed == nullptr || tracked == nullptr) {
        return RangeRelation::Unknown;
    }

    RangeAnchorComparisons comparisons{};
    if (FAILED(changed->CompareStart(
            edit_cookie,
            tracked,
            TF_ANCHOR_START,
            &comparisons.start_to_tracked_start)) ||
        FAILED(changed->CompareStart(
            edit_cookie,
            tracked,
            TF_ANCHOR_END,
            &comparisons.start_to_tracked_end)) ||
        FAILED(changed->CompareEnd(
            edit_cookie,
            tracked,
            TF_ANCHOR_START,
            &comparisons.end_to_tracked_start)) ||
        FAILED(changed->CompareEnd(
            edit_cookie,
            tracked,
            TF_ANCHOR_END,
            &comparisons.end_to_tracked_end))) {
        return RangeRelation::Unknown;
    }
    return ClassifyRangeRelation(comparisons);
}

// ITfTextEditSink is also notified for selection and property-only edits.
// Preserve the changed ranges long enough to decide whether this transaction
// can affect the committed text. Unknown host behavior is processed
// conservatively so a partial TSF implementation cannot disable tracking.
inline TextUpdateRelevance InspectTextUpdates(
    ITfEditRecord* edit_record,
    ITfRange* tracked_range,
    TfEditCookie edit_cookie) noexcept {
    if (edit_record == nullptr || tracked_range == nullptr) {
        return TextUpdateRelevance::Unknown;
    }

    IEnumTfRanges* ranges = nullptr;
    const HRESULT result = edit_record->GetTextAndPropertyUpdates(
        TF_GTP_INCL_TEXT, nullptr, 0, &ranges);
    if (FAILED(result) || ranges == nullptr) {
        if (ranges != nullptr) {
            ranges->Release();
        }
        return TextUpdateRelevance::Unknown;
    }

    bool found_text_update = false;
    TextUpdateRelevance relevance = TextUpdateRelevance::Unrelated;
    while (true) {
        ITfRange* changed = nullptr;
        ULONG fetched = 0;
        const HRESULT next_result = ranges->Next(1, &changed, &fetched);
        if (FAILED(next_result) || changed == nullptr) {
            if (changed != nullptr) {
                changed->Release();
            }
            if (next_result == S_FALSE ||
                (SUCCEEDED(next_result) && fetched == 0)) {
                break;
            }
            relevance = TextUpdateRelevance::Unknown;
            break;
        }

        found_text_update = true;
        const RangeRelation relation = CompareRangeToTracked(
            changed, tracked_range, edit_cookie);
        changed->Release();
        if (relation == RangeRelation::Unknown) {
            relevance = TextUpdateRelevance::Unknown;
            break;
        }
        if (MayAffectTrackedRange(relation)) {
            relevance = TextUpdateRelevance::Related;
            break;
        }
    }
    ranges->Release();
    if (relevance == TextUpdateRelevance::Unknown) {
        return relevance;
    }
    return found_text_update ? relevance : TextUpdateRelevance::None;
}

}  // namespace caps_writer::tsf
