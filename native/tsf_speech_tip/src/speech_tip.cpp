#include <windows.h>
#include <inputscope.h>
#include <msctf.h>
#include <oleauto.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <deque>
#include <memory>
#include <mutex>
#include <new>
#include <string>
#include <thread>
#include <unordered_map>
#include <utility>

#include "edit_session_queue.h"
#include "edit_session_policy.h"
#include "display_attributes.h"
#include "context_snapshot.h"
#include "protocol.h"

using caps_writer::tsf::CompositionStyle;
using caps_writer::tsf::DisplayAttributeGuid;
using caps_writer::tsf::Frame;
using caps_writer::tsf::FrameHeader;
using caps_writer::tsf::Operation;
using caps_writer::tsf::ParseCompositionStyle;
using caps_writer::tsf::SameSession;
using caps_writer::tsf::Status;

namespace {

// {B635F2D7-83A5-462D-A3CE-DA8284B49D93}
constexpr CLSID kTextServiceClsid = {
    0xb635f2d7,
    0x83a5,
    0x462d,
    {0xa3, 0xce, 0xda, 0x82, 0x84, 0xb4, 0x9d, 0x93},
};

// {68CE1F8D-F760-4D4D-A738-BC80C01E6726}
constexpr GUID kLanguageProfileGuid = {
    0x68ce1f8d,
    0xf760,
    0x4d4d,
    {0xa7, 0x38, 0xbc, 0x80, 0xc0, 0x1e, 0x67, 0x26},
};

// {1713DD5A-68E7-4A5B-9AF6-592A595C778D}
constexpr GUID kInputScopePropertyGuid = {
    0x1713dd5a,
    0x68e7,
    0x4a5b,
    {0x9a, 0xf6, 0x59, 0x2a, 0x59, 0x5c, 0x77, 0x8d},
};

constexpr wchar_t kProfileDescription[] = L"CapsWriter Speech Composition (Experimental)";
constexpr wchar_t kWindowClassName[] = L"CapsWriter.TsfSpeechTip.Dispatch.v1";
constexpr UINT kPipeFrameMessage = WM_APP + 0x341;
constexpr UINT kPumpEditQueueMessage = WM_APP + 0x342;
constexpr DWORD kDisconnectedRetryInitialMs = 100;
constexpr DWORD kDisconnectedRetryMaximumMs = 5000;
constexpr std::size_t kMaximumOutgoingFrames = 256;
constexpr LONG kContextSurroundingCharacters = 4096;
constexpr std::size_t kContextSelectionCharacters = 4096;

HINSTANCE g_instance = nullptr;
std::atomic<long> g_object_count{0};
std::atomic<long> g_lock_count{0};

template <typename T>
void SafeRelease(T*& value) noexcept {
    if (value != nullptr) {
        value->Release();
        value = nullptr;
    }
}

bool WriteExact(HANDLE pipe, const void* source, DWORD byte_count) {
    HANDLE write_event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (write_event == nullptr) {
        return false;
    }
    const auto* cursor = static_cast<const std::uint8_t*>(source);
    DWORD total = 0;
    bool success = true;
    while (total < byte_count) {
        ResetEvent(write_event);
        OVERLAPPED overlapped{};
        overlapped.hEvent = write_event;
        DWORD written = 0;
        if (!WriteFile(
                pipe,
                cursor + total,
                byte_count - total,
                &written,
                &overlapped)) {
            if (GetLastError() != ERROR_IO_PENDING ||
                WaitForSingleObject(write_event, INFINITE) != WAIT_OBJECT_0 ||
                !GetOverlappedResult(pipe, &overlapped, &written, FALSE)) {
                success = false;
                break;
            }
        }
        if (written == 0) {
            success = false;
            break;
        }
        total += written;
    }
    CloseHandle(write_event);
    return success;
}

class TextService;

class DisplayAttributeInfo final : public ITfDisplayAttributeInfo {
public:
    explicit DisplayAttributeInfo(CompositionStyle style) noexcept
        : style_(style), attribute_(caps_writer::tsf::MakeDisplayAttribute(style)) {
        ++g_object_count;
    }

    STDMETHODIMP QueryInterface(REFIID iid, void** object) override {
        if (object == nullptr) {
            return E_INVALIDARG;
        }
        *object = nullptr;
        if (iid != IID_IUnknown && iid != IID_ITfDisplayAttributeInfo) {
            return E_NOINTERFACE;
        }
        *object = static_cast<ITfDisplayAttributeInfo*>(this);
        AddRef();
        return S_OK;
    }

    STDMETHODIMP_(ULONG) AddRef() override { return ++ref_count_; }

    STDMETHODIMP_(ULONG) Release() override {
        const ULONG remaining = --ref_count_;
        if (remaining == 0) {
            delete this;
        }
        return remaining;
    }

    STDMETHODIMP GetGUID(GUID* guid) override {
        if (guid == nullptr) {
            return E_INVALIDARG;
        }
        *guid = DisplayAttributeGuid(style_);
        return S_OK;
    }

    STDMETHODIMP GetDescription(BSTR* description) override {
        if (description == nullptr) {
            return E_INVALIDARG;
        }
        *description = SysAllocString(
            style_ == CompositionStyle::Polishing
                ? L"CapsWriter polishing composition"
                : L"CapsWriter transcription composition");
        return *description != nullptr ? S_OK : E_OUTOFMEMORY;
    }

    STDMETHODIMP GetAttributeInfo(TF_DISPLAYATTRIBUTE* attribute) override {
        if (attribute == nullptr) {
            return E_INVALIDARG;
        }
        *attribute = attribute_;
        return S_OK;
    }

    STDMETHODIMP SetAttributeInfo(const TF_DISPLAYATTRIBUTE* attribute) override {
        if (attribute == nullptr) {
            return E_INVALIDARG;
        }
        return E_NOTIMPL;
    }

    STDMETHODIMP Reset() override {
        attribute_ = caps_writer::tsf::MakeDisplayAttribute(style_);
        return S_OK;
    }

private:
    ~DisplayAttributeInfo() { --g_object_count; }

    std::atomic<ULONG> ref_count_{1};
    CompositionStyle style_;
    TF_DISPLAYATTRIBUTE attribute_{};
};

HRESULT CreateDisplayAttributeInfo(
    CompositionStyle style,
    ITfDisplayAttributeInfo** info) {
    if (info == nullptr) {
        return E_INVALIDARG;
    }
    *info = new (std::nothrow) DisplayAttributeInfo(style);
    return *info != nullptr ? S_OK : E_OUTOFMEMORY;
}

class DisplayAttributeEnumerator final : public IEnumTfDisplayAttributeInfo {
public:
    explicit DisplayAttributeEnumerator(ULONG position = 0) noexcept
        : position_(position) {
        ++g_object_count;
    }

    STDMETHODIMP QueryInterface(REFIID iid, void** object) override {
        if (object == nullptr) {
            return E_INVALIDARG;
        }
        *object = nullptr;
        if (iid != IID_IUnknown && iid != IID_IEnumTfDisplayAttributeInfo) {
            return E_NOINTERFACE;
        }
        *object = static_cast<IEnumTfDisplayAttributeInfo*>(this);
        AddRef();
        return S_OK;
    }

    STDMETHODIMP_(ULONG) AddRef() override { return ++ref_count_; }

    STDMETHODIMP_(ULONG) Release() override {
        const ULONG remaining = --ref_count_;
        if (remaining == 0) {
            delete this;
        }
        return remaining;
    }

    STDMETHODIMP Clone(IEnumTfDisplayAttributeInfo** enumeration) override {
        if (enumeration == nullptr) {
            return E_INVALIDARG;
        }
        *enumeration = new (std::nothrow) DisplayAttributeEnumerator(position_);
        return *enumeration != nullptr ? S_OK : E_OUTOFMEMORY;
    }

    STDMETHODIMP Next(
        ULONG count,
        ITfDisplayAttributeInfo** info,
        ULONG* fetched) override {
        if (info == nullptr) {
            return E_INVALIDARG;
        }
        ULONG fetched_count = 0;
        while (fetched_count < count && position_ < 2) {
            const CompositionStyle style = position_ == 0
                ? CompositionStyle::Transcription
                : CompositionStyle::Polishing;
            const HRESULT result = CreateDisplayAttributeInfo(
                style, &info[fetched_count]);
            if (FAILED(result)) {
                if (fetched != nullptr) {
                    *fetched = fetched_count;
                }
                return result;
            }
            ++position_;
            ++fetched_count;
        }
        if (fetched != nullptr) {
            *fetched = fetched_count;
        }
        return fetched_count == count ? S_OK : S_FALSE;
    }

    STDMETHODIMP Reset() override {
        position_ = 0;
        return S_OK;
    }

    STDMETHODIMP Skip(ULONG count) override {
        const ULONG remaining = 2 - std::min(position_, 2UL);
        const ULONG skipped = std::min(count, remaining);
        position_ += skipped;
        return skipped == count ? S_OK : S_FALSE;
    }

private:
    ~DisplayAttributeEnumerator() { --g_object_count; }

    std::atomic<ULONG> ref_count_{1};
    ULONG position_ = 0;
};

class EditSession final : public ITfEditSession {
public:
    EditSession(TextService* service, ITfContext* context, Frame frame) noexcept;

    STDMETHODIMP QueryInterface(REFIID iid, void** object) override;
    STDMETHODIMP_(ULONG) AddRef() override;
    STDMETHODIMP_(ULONG) Release() override;
    STDMETHODIMP DoEditSession(TfEditCookie edit_cookie) override;

private:
    ~EditSession();

    std::atomic<ULONG> ref_count_{1};
    TextService* service_;
    ITfContext* context_;
    Frame frame_;
};

class TextService final
    : public ITfTextInputProcessorEx,
      public ITfCompositionSink,
      public ITfDisplayAttributeProvider {
public:
    TextService() noexcept { ++g_object_count; }

    STDMETHODIMP QueryInterface(REFIID iid, void** object) override {
        if (object == nullptr) {
            return E_INVALIDARG;
        }
        *object = nullptr;
        if (iid == IID_IUnknown || iid == IID_ITfTextInputProcessor ||
            iid == IID_ITfTextInputProcessorEx) {
            *object = static_cast<ITfTextInputProcessorEx*>(this);
        } else if (iid == IID_ITfCompositionSink) {
            *object = static_cast<ITfCompositionSink*>(this);
        } else if (iid == IID_ITfDisplayAttributeProvider) {
            *object = static_cast<ITfDisplayAttributeProvider*>(this);
        } else {
            return E_NOINTERFACE;
        }
        AddRef();
        return S_OK;
    }

    STDMETHODIMP_(ULONG) AddRef() override { return ++ref_count_; }

    STDMETHODIMP_(ULONG) Release() override {
        const ULONG remaining = --ref_count_;
        if (remaining == 0) {
            delete this;
        }
        return remaining;
    }

    STDMETHODIMP EnumDisplayAttributeInfo(
        IEnumTfDisplayAttributeInfo** enumeration) override {
        if (enumeration == nullptr) {
            return E_INVALIDARG;
        }
        *enumeration = new (std::nothrow) DisplayAttributeEnumerator();
        return *enumeration != nullptr ? S_OK : E_OUTOFMEMORY;
    }

    STDMETHODIMP GetDisplayAttributeInfo(
        REFGUID guid,
        ITfDisplayAttributeInfo** info) override {
        if (IsEqualGUID(guid, caps_writer::tsf::kTranscriptionDisplayAttributeGuid)) {
            return CreateDisplayAttributeInfo(CompositionStyle::Transcription, info);
        }
        if (IsEqualGUID(guid, caps_writer::tsf::kPolishingDisplayAttributeGuid)) {
            return CreateDisplayAttributeInfo(CompositionStyle::Polishing, info);
        }
        if (info != nullptr) {
            *info = nullptr;
        }
        return E_INVALIDARG;
    }

    STDMETHODIMP Activate(ITfThreadMgr* thread_manager, TfClientId client_id) override {
        return ActivateEx(thread_manager, client_id, 0);
    }

    STDMETHODIMP ActivateEx(
        ITfThreadMgr* thread_manager,
        TfClientId client_id,
        DWORD /*flags*/) override {
        if (thread_manager == nullptr || thread_manager_ != nullptr) {
            return E_INVALIDARG;
        }
        thread_manager_ = thread_manager;
        thread_manager_->AddRef();
        client_id_ = client_id;
        activation_thread_id_ = GetCurrentThreadId();
        const HRESULT window_result = CreateDispatchWindow();
        if (FAILED(window_result)) {
            SafeRelease(thread_manager_);
            client_id_ = TF_CLIENTID_NULL;
            return window_result;
        }
        stop_pipe_.store(false);
        pipe_wake_event_ = CreateEventW(nullptr, FALSE, FALSE, nullptr);
        if (pipe_wake_event_ == nullptr) {
            const HRESULT result = HRESULT_FROM_WIN32(GetLastError());
            DestroyWindow(dispatch_window_);
            dispatch_window_ = nullptr;
            SafeRelease(thread_manager_);
            client_id_ = TF_CLIENTID_NULL;
            activation_thread_id_ = 0;
            return result;
        }
        foreground_hook_ = SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND,
            EVENT_SYSTEM_FOREGROUND,
            nullptr,
            &TextService::ForegroundEventProc,
            0,
            0,
            WINEVENT_OUTOFCONTEXT);
        if (foreground_hook_ == nullptr) {
            const DWORD error = GetLastError();
            const HRESULT result = error == ERROR_SUCCESS
                ? E_FAIL
                : HRESULT_FROM_WIN32(error);
            CloseHandle(pipe_wake_event_);
            pipe_wake_event_ = nullptr;
            DestroyWindow(dispatch_window_);
            dispatch_window_ = nullptr;
            SafeRelease(thread_manager_);
            client_id_ = TF_CLIENTID_NULL;
            activation_thread_id_ = 0;
            return result;
        }
        {
            std::scoped_lock lock(foreground_hooks_mutex_);
            foreground_hooks_[foreground_hook_] = this;
        }
        try {
            pipe_thread_ = std::thread([this] { PipeLoop(); });
        } catch (...) {
            UnregisterForegroundHook();
            CloseHandle(pipe_wake_event_);
            pipe_wake_event_ = nullptr;
            DestroyWindow(dispatch_window_);
            dispatch_window_ = nullptr;
            SafeRelease(thread_manager_);
            client_id_ = TF_CLIENTID_NULL;
            activation_thread_id_ = 0;
            return E_OUTOFMEMORY;
        }
        InitializeDisplayAttributes();
        return S_OK;
    }

    STDMETHODIMP Deactivate() override {
        UnregisterForegroundHook();
        stop_pipe_.store(true);
        if (pipe_wake_event_ != nullptr) {
            SetEvent(pipe_wake_event_);
        }
        if (pipe_thread_.joinable()) {
            pipe_thread_.join();
        }
        if (pipe_wake_event_ != nullptr) {
            CloseHandle(pipe_wake_event_);
            pipe_wake_event_ = nullptr;
        }
        if (dispatch_window_ != nullptr) {
            MSG message{};
            while (PeekMessageW(
                &message,
                dispatch_window_,
                kPipeFrameMessage,
                kPipeFrameMessage,
                PM_REMOVE)) {
                delete reinterpret_cast<Frame*>(message.lParam);
            }
            DestroyWindow(dispatch_window_);
            dispatch_window_ = nullptr;
        }
        edit_queue_.Reset();
        ClearCompositionState();
        SafeRelease(category_manager_);
        display_attribute_atoms_.fill(TF_INVALID_GUIDATOM);
        SafeRelease(thread_manager_);
        client_id_ = TF_CLIENTID_NULL;
        activation_thread_id_ = 0;
        return S_OK;
    }

    STDMETHODIMP OnCompositionTerminated(
        TfEditCookie edit_cookie,
        ITfComposition* composition) override {
        if (composition_ == composition) {
            if (ending_composition_) {
                return S_OK;
            }
            const auto terminated_session = active_session_;
            const auto terminated_revision = last_revision_;
            Status rollback_status = Status::EditSessionFailed;
            ITfRange* range = nullptr;
            if (context_ != nullptr &&
                SUCCEEDED(composition->GetRange(&range)) &&
                range != nullptr) {
                const HRESULT text_result = range->SetText(
                    edit_cookie,
                    0,
                    original_selection_text_.data(),
                    static_cast<LONG>(original_selection_text_.size()));
                HRESULT selection_result = E_FAIL;
                if (SUCCEEDED(text_result)) {
                    TF_SELECTION selection{};
                    selection.range = range;
                    selection.style = original_selection_style_;
                    selection_result = context_->SetSelection(
                        edit_cookie, 1, &selection);
                }
                if (caps_writer::tsf::CanEndCancellation(
                        text_result, selection_result)) {
                    ClearDisplayAttribute(context_, range, edit_cookie);
                    rollback_status = Status::Applied;
                }
                range->Release();
            }
            SendCompositionTerminated(
                terminated_session, terminated_revision, rollback_status);
            ClearCompositionState();
        }
        return S_OK;
    }

    HRESULT ApplyEdit(ITfContext* context, const Frame& frame, TfEditCookie edit_cookie) {
        const auto operation = static_cast<Operation>(frame.header.operation);
        Status status = Status::EditSessionFailed;
        std::wstring response_text;
        switch (operation) {
            case Operation::Begin:
                status = ApplyBegin(context, frame, edit_cookie);
                break;
            case Operation::Revise:
                status = ApplyRevision(context, frame, edit_cookie);
                break;
            case Operation::Commit:
                status = ApplyCommit(context, frame, edit_cookie);
                break;
            case Operation::Cancel:
                status = ApplyCancel(context, frame, edit_cookie);
                break;
            case Operation::QueryContext:
                status = ApplyQueryContext(context, edit_cookie, &response_text);
                break;
            default:
                status = Status::InvalidFrame;
                break;
        }
        SendAck(frame, status, response_text);
        return status == Status::Applied ? S_OK : S_FALSE;
    }

    void CompleteEditSession() {
        edit_queue_.Complete();
        // Do not request the next asynchronous edit session recursively from
        // inside DoEditSession. Pump it after TSF has unwound this callback.
        if (dispatch_window_ != nullptr) {
            PostMessageW(dispatch_window_, kPumpEditQueueMessage, 0, 0);
        }
    }

private:
    ~TextService() {
        if (thread_manager_ != nullptr) {
            Deactivate();
        }
        --g_object_count;
    }

    HRESULT CreateDispatchWindow() {
        WNDCLASSEXW window_class{};
        window_class.cbSize = sizeof(window_class);
        window_class.lpfnWndProc = &TextService::WindowProc;
        window_class.hInstance = g_instance;
        window_class.lpszClassName = kWindowClassName;
        if (RegisterClassExW(&window_class) == 0 && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) {
            return HRESULT_FROM_WIN32(GetLastError());
        }
        dispatch_window_ = CreateWindowExW(
            0,
            kWindowClassName,
            L"",
            0,
            0,
            0,
            0,
            0,
            HWND_MESSAGE,
            nullptr,
            g_instance,
            this);
        return dispatch_window_ != nullptr ? S_OK : HRESULT_FROM_WIN32(GetLastError());
    }

    static LRESULT CALLBACK WindowProc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
        TextService* service = nullptr;
        if (message == WM_NCCREATE) {
            const auto* create = reinterpret_cast<CREATESTRUCTW*>(lparam);
            service = static_cast<TextService*>(create->lpCreateParams);
            SetWindowLongPtrW(window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(service));
        } else {
            service = reinterpret_cast<TextService*>(GetWindowLongPtrW(window, GWLP_USERDATA));
        }
        if (message == kPipeFrameMessage) {
            std::unique_ptr<Frame> frame(reinterpret_cast<Frame*>(lparam));
            if (service != nullptr && frame != nullptr) {
                service->QueueEdit(std::move(*frame));
            }
            return 0;
        }
        if (message == kPumpEditQueueMessage) {
            if (service != nullptr) {
                service->DispatchNextEdit();
            }
            return 0;
        }
        return DefWindowProcW(window, message, wparam, lparam);
    }

    static void CALLBACK ForegroundEventProc(
        HWINEVENTHOOK hook,
        DWORD /*event*/,
        HWND /*window*/,
        LONG /*object_id*/,
        LONG /*child_id*/,
        DWORD /*event_thread*/,
        DWORD /*event_time*/) {
        std::scoped_lock lock(foreground_hooks_mutex_);
        const auto found = foreground_hooks_.find(hook);
        if (found != foreground_hooks_.end() &&
            found->second->pipe_wake_event_ != nullptr) {
            SetEvent(found->second->pipe_wake_event_);
        }
    }

    void UnregisterForegroundHook() noexcept {
        if (foreground_hook_ == nullptr) {
            return;
        }
        {
            std::scoped_lock lock(foreground_hooks_mutex_);
            foreground_hooks_.erase(foreground_hook_);
        }
        UnhookWinEvent(foreground_hook_);
        foreground_hook_ = nullptr;
    }

    void InitializeDisplayAttributes() noexcept {
        display_attribute_atoms_.fill(TF_INVALID_GUIDATOM);
        HRESULT result = CoCreateInstance(
            CLSID_TF_CategoryMgr,
            nullptr,
            CLSCTX_INPROC_SERVER,
            IID_PPV_ARGS(&category_manager_));
        if (FAILED(result) || category_manager_ == nullptr) {
            SafeRelease(category_manager_);
            return;
        }
        result = category_manager_->RegisterGUID(
            caps_writer::tsf::kTranscriptionDisplayAttributeGuid,
            &display_attribute_atoms_[0]);
        if (SUCCEEDED(result)) {
            result = category_manager_->RegisterGUID(
                caps_writer::tsf::kPolishingDisplayAttributeGuid,
                &display_attribute_atoms_[1]);
        }
        if (FAILED(result)) {
            SafeRelease(category_manager_);
            display_attribute_atoms_.fill(TF_INVALID_GUIDATOM);
        }
    }

    void ApplyDisplayAttribute(
        ITfContext* context,
        ITfRange* range,
        TfEditCookie edit_cookie,
        CompositionStyle style) const noexcept {
        const std::size_t index = style == CompositionStyle::Polishing ? 1 : 0;
        const TfGuidAtom atom = display_attribute_atoms_[index];
        if (context == nullptr || range == nullptr || atom == TF_INVALID_GUIDATOM) {
            return;
        }
        ITfProperty* property = nullptr;
        if (SUCCEEDED(context->GetProperty(GUID_PROP_ATTRIBUTE, &property)) &&
            property != nullptr) {
            VARIANT value{};
            value.vt = VT_I4;
            value.lVal = atom;
            property->SetValue(edit_cookie, range, &value);
            property->Release();
        }
    }

    static void ClearDisplayAttribute(
        ITfContext* context,
        ITfRange* range,
        TfEditCookie edit_cookie) noexcept {
        if (context == nullptr || range == nullptr) {
            return;
        }
        ITfProperty* property = nullptr;
        if (SUCCEEDED(context->GetProperty(GUID_PROP_ATTRIBUTE, &property)) &&
            property != nullptr) {
            property->Clear(edit_cookie, range);
            property->Release();
        }
    }

    bool IsForegroundProcess() const noexcept {
        HWND foreground = GetForegroundWindow();
        if (foreground == nullptr) {
            return false;
        }
        DWORD foreground_process = 0;
        GetWindowThreadProcessId(foreground, &foreground_process);
        return foreground_process == GetCurrentProcessId();
    }

    void QueueEdit(Frame frame) {
        const auto operation = static_cast<Operation>(frame.header.operation);
        if (operation == Operation::Ping) {
            SendAck(frame, Status::Applied);
            return;
        }
        if ((operation == Operation::Begin || operation == Operation::QueryContext) &&
            !IsForegroundProcess()) {
            SendAck(frame, Status::IgnoredNotForeground);
            return;
        }

        auto superseded = edit_queue_.Push(std::move(frame));
        if (superseded.has_value()) {
            SendAck(*superseded, Status::StaleRevision);
        }
        DispatchNextEdit();
    }

    void DispatchNextEdit() {
        while (auto next = edit_queue_.StartNext()) {
            Frame frame = std::move(*next);
            const auto operation = static_cast<Operation>(frame.header.operation);
            ITfContext* context = nullptr;
            if (operation == Operation::QueryContext &&
                composition_ != nullptr && context_ != nullptr) {
                context = context_;
                context->AddRef();
            } else if (operation == Operation::Begin ||
                       operation == Operation::QueryContext) {
                ITfDocumentMgr* document_manager = nullptr;
                HRESULT result = thread_manager_->GetFocus(&document_manager);
                if (SUCCEEDED(result) && document_manager != nullptr) {
                    result = document_manager->GetTop(&context);
                    document_manager->Release();
                }
                if (FAILED(result) || context == nullptr) {
                    SendAck(frame, Status::NoContext);
                    edit_queue_.Complete();
                    continue;
                }
            } else {
                if (context_ == nullptr ||
                    !SameSession(active_session_, frame.header.session_id)) {
                    SendAck(frame, Status::InactiveSession);
                    edit_queue_.Complete();
                    continue;
                }
                context = context_;
                context->AddRef();
            }

            Frame request_frame = frame;
            auto* edit_session = new (std::nothrow) EditSession(
                this, context, std::move(frame));
            if (edit_session == nullptr) {
                context->Release();
                SendAck(request_frame, Status::EditSessionFailed);
                edit_queue_.Complete();
                continue;
            }
            HRESULT session_result = E_FAIL;
            const DWORD edit_flags = operation == Operation::QueryContext
                ? TF_ES_ASYNC | TF_ES_READ
                : TF_ES_ASYNC | TF_ES_READWRITE;
            const HRESULT request_result = context->RequestEditSession(
                client_id_,
                edit_session,
                edit_flags,
                &session_result);
            context->Release();
            edit_session->Release();
            if (FAILED(request_result) || FAILED(session_result)) {
                SendAck(request_frame, Status::EditSessionFailed);
                edit_queue_.Complete();
                continue;
            }
            return;
        }
    }

    Status ApplyBegin(ITfContext* context, const Frame& frame, TfEditCookie edit_cookie) {
        if (composition_ != nullptr) {
            return SameSession(active_session_, frame.header.session_id)
                ? Status::StaleRevision
                : Status::InactiveSession;
        }

        TF_SELECTION selection{};
        ULONG fetched = 0;
        HRESULT result = context->GetSelection(
            edit_cookie, TF_DEFAULT_SELECTION, 1, &selection, &fetched);
        if (FAILED(result) || fetched != 1 || selection.range == nullptr) {
            return Status::NoContext;
        }
        std::wstring original_selection_text;
        result = ReadRangeText(selection.range, edit_cookie, &original_selection_text);
        ITfContextComposition* context_composition = nullptr;
        if (SUCCEEDED(result)) {
            result = context->QueryInterface(IID_PPV_ARGS(&context_composition));
        }
        if (SUCCEEDED(result)) {
            result = context_composition->StartComposition(
                edit_cookie,
                selection.range,
                this,
                &composition_);
            context_composition->Release();
            if (SUCCEEDED(result) && composition_ == nullptr) {
                result = E_FAIL;
            }
        }
        if (SUCCEEDED(result)) {
            result = selection.range->SetText(
                edit_cookie,
                0,
                frame.text.data(),
                static_cast<LONG>(frame.text.size()));
            if (FAILED(result) && composition_ != nullptr) {
                composition_->EndComposition(edit_cookie);
                SafeRelease(composition_);
            }
        }
        if (SUCCEEDED(result)) {
            ApplyDisplayAttribute(
                context,
                selection.range,
                edit_cookie,
                ParseCompositionStyle(frame.header.status));
            SetCaretAtEnd(context, selection.range, edit_cookie);
            context_ = context;
            context_->AddRef();
            active_session_ = frame.header.session_id;
            last_revision_ = frame.header.revision;
            original_selection_text_ = std::move(original_selection_text);
            original_selection_style_ = selection.style;
            composition_active_.store(true);
        }
        selection.range->Release();
        return SUCCEEDED(result) ? Status::Applied : Status::EditSessionFailed;
    }

    Status ApplyRevision(ITfContext* context, const Frame& frame, TfEditCookie edit_cookie) {
        if (composition_ == nullptr || !SameSession(active_session_, frame.header.session_id)) {
            return Status::InactiveSession;
        }
        if (frame.header.revision <= last_revision_) {
            return Status::StaleRevision;
        }
        ITfRange* range = nullptr;
        HRESULT result = composition_->GetRange(&range);
        if (SUCCEEDED(result) && range != nullptr) {
            result = range->SetText(
                edit_cookie,
                0,
                frame.text.data(),
                static_cast<LONG>(frame.text.size()));
            if (SUCCEEDED(result)) {
                ApplyDisplayAttribute(
                    context,
                    range,
                    edit_cookie,
                    ParseCompositionStyle(frame.header.status));
                SetCaretAtEnd(context, range, edit_cookie);
                last_revision_ = frame.header.revision;
            }
            range->Release();
        }
        return SUCCEEDED(result) ? Status::Applied : Status::EditSessionFailed;
    }

    Status ApplyCommit(ITfContext* context, const Frame& frame, TfEditCookie edit_cookie) {
        if (composition_ == nullptr || !SameSession(active_session_, frame.header.session_id)) {
            return Status::InactiveSession;
        }
        if (frame.header.revision <= last_revision_) {
            return Status::StaleRevision;
        }
        ITfComposition* composition = composition_;
        composition->AddRef();
        ITfRange* range = nullptr;
        composition->GetRange(&range);
        ending_composition_ = true;
        const HRESULT result = composition->EndComposition(edit_cookie);
        ending_composition_ = false;
        composition->Release();
        if (SUCCEEDED(result)) {
            ClearDisplayAttribute(context, range, edit_cookie);
            ClearCompositionState();
        }
        SafeRelease(range);
        return SUCCEEDED(result) ? Status::Applied : Status::EditSessionFailed;
    }

    Status ApplyCancel(ITfContext* context, const Frame& frame, TfEditCookie edit_cookie) {
        if (composition_ == nullptr || !SameSession(active_session_, frame.header.session_id)) {
            return Status::InactiveSession;
        }
        ITfRange* range = nullptr;
        const HRESULT range_result = composition_->GetRange(&range);
        if (FAILED(range_result) || range == nullptr) {
            SafeRelease(range);
            return Status::EditSessionFailed;
        }
        const HRESULT text_restore_result = range->SetText(
            edit_cookie,
            0,
            original_selection_text_.data(),
            static_cast<LONG>(original_selection_text_.size()));
        const TF_SELECTIONSTYLE original_selection_style = original_selection_style_;
        HRESULT selection_restore_result = E_FAIL;
        if (SUCCEEDED(text_restore_result)) {
            TF_SELECTION selection{};
            selection.range = range;
            selection.style = original_selection_style;
            selection_restore_result = context->SetSelection(
                edit_cookie, 1, &selection);
        }
        if (!caps_writer::tsf::CanEndCancellation(
                text_restore_result, selection_restore_result)) {
            range->Release();
            return Status::EditSessionFailed;
        }

        ITfComposition* composition = composition_;
        if (composition == nullptr) {
            range->Release();
            return Status::InactiveSession;
        }
        composition->AddRef();
        ending_composition_ = true;
        const HRESULT end_result = composition->EndComposition(edit_cookie);
        ending_composition_ = false;
        composition->Release();
        if (SUCCEEDED(end_result)) {
            ClearDisplayAttribute(context, range, edit_cookie);
            ClearCompositionState();
        }
        range->Release();
        return SUCCEEDED(end_result) ? Status::Applied : Status::EditSessionFailed;
    }

    Status ApplyQueryContext(
        ITfContext* context,
        TfEditCookie edit_cookie,
        std::wstring* payload) {
        if (context == nullptr || payload == nullptr) {
            return Status::NoContext;
        }

        ITfRange* range = nullptr;
        TF_SELECTIONSTYLE selection_style{};
        std::wstring selection_text;
        HRESULT result = E_FAIL;
        if (composition_ != nullptr && context == context_) {
            result = composition_->GetRange(&range);
            selection_style = original_selection_style_;
            selection_text = original_selection_text_;
        } else {
            TF_SELECTION selection{};
            ULONG fetched = 0;
            result = context->GetSelection(
                edit_cookie, TF_DEFAULT_SELECTION, 1, &selection, &fetched);
            if (SUCCEEDED(result) && fetched == 1 && selection.range != nullptr) {
                range = selection.range;
                selection_style = selection.style;
            } else {
                SafeRelease(selection.range);
                return Status::NoContext;
            }
        }
        if (FAILED(result) || range == nullptr) {
            SafeRelease(range);
            return Status::NoContext;
        }
        if (IsSensitiveInputScope(context, range, edit_cookie)) {
            range->Release();
            *payload = caps_writer::tsf::EncodeContextSnapshot(
                L"", L"", L"", static_cast<unsigned long>(TF_AE_NONE));
            return Status::Applied;
        }
        if (composition_ == nullptr || context != context_) {
            result = ReadRangeTextLimited(
                range,
                edit_cookie,
                kContextSelectionCharacters,
                &selection_text);
            if (FAILED(result)) {
                range->Release();
                return Status::EditSessionFailed;
            }
        }

        std::wstring prefix;
        std::wstring suffix;
        result = ReadSurroundingText(
            range,
            edit_cookie,
            kContextSurroundingCharacters,
            &prefix,
            &suffix);
        range->Release();
        if (FAILED(result)) {
            return Status::EditSessionFailed;
        }

        *payload = caps_writer::tsf::EncodeContextSnapshot(
            prefix,
            selection_text,
            suffix,
            static_cast<unsigned long>(selection_style.ase));
        return Status::Applied;
    }

    static HRESULT ReadRangeText(
        ITfRange* source,
        TfEditCookie edit_cookie,
        std::wstring* text) {
        if (source == nullptr || text == nullptr) {
            return E_INVALIDARG;
        }
        ITfRange* reader = nullptr;
        HRESULT result = source->Clone(&reader);
        if (FAILED(result) || reader == nullptr) {
            return FAILED(result) ? result : E_FAIL;
        }
        constexpr ULONG kBufferCharacters = 1024;
        wchar_t buffer[kBufferCharacters];
        while (SUCCEEDED(result)) {
            ULONG fetched = 0;
            result = reader->GetText(
                edit_cookie,
                TF_TF_MOVESTART,
                buffer,
                kBufferCharacters,
                &fetched);
            if (FAILED(result) || fetched == 0) {
                break;
            }
            text->append(buffer, fetched);
        }
        reader->Release();
        return result;
    }

    static HRESULT ReadRangeTextLimited(
        ITfRange* source,
        TfEditCookie edit_cookie,
        std::size_t maximum_characters,
        std::wstring* text) {
        if (source == nullptr || text == nullptr) {
            return E_INVALIDARG;
        }
        ITfRange* reader = nullptr;
        HRESULT result = source->Clone(&reader);
        if (FAILED(result) || reader == nullptr) {
            return FAILED(result) ? result : E_FAIL;
        }
        constexpr ULONG kBufferCharacters = 1024;
        wchar_t buffer[kBufferCharacters];
        while (SUCCEEDED(result) && text->size() < maximum_characters) {
            const auto remaining = maximum_characters - text->size();
            const ULONG requested = static_cast<ULONG>(
                std::min<std::size_t>(kBufferCharacters, remaining));
            ULONG fetched = 0;
            result = reader->GetText(
                edit_cookie,
                TF_TF_MOVESTART,
                buffer,
                requested,
                &fetched);
            if (FAILED(result) || fetched == 0) {
                break;
            }
            text->append(buffer, fetched);
        }
        reader->Release();
        return result;
    }

    static HRESULT ReadSurroundingText(
        ITfRange* source,
        TfEditCookie edit_cookie,
        LONG maximum_characters,
        std::wstring* prefix,
        std::wstring* suffix) {
        if (source == nullptr || prefix == nullptr || suffix == nullptr) {
            return E_INVALIDARG;
        }

        ITfRange* before = nullptr;
        HRESULT result = source->Clone(&before);
        if (SUCCEEDED(result) && before != nullptr) {
            result = before->Collapse(edit_cookie, TF_ANCHOR_START);
        }
        LONG shifted = 0;
        if (SUCCEEDED(result)) {
            result = before->ShiftStart(
                edit_cookie, -maximum_characters, &shifted, nullptr);
        }
        if (SUCCEEDED(result)) {
            result = ReadRangeTextLimited(
                before,
                edit_cookie,
                static_cast<std::size_t>(maximum_characters),
                prefix);
        }
        SafeRelease(before);
        if (FAILED(result)) {
            return result;
        }

        ITfRange* after = nullptr;
        result = source->Clone(&after);
        if (SUCCEEDED(result) && after != nullptr) {
            result = after->Collapse(edit_cookie, TF_ANCHOR_END);
        }
        shifted = 0;
        if (SUCCEEDED(result)) {
            result = after->ShiftEnd(
                edit_cookie, maximum_characters, &shifted, nullptr);
        }
        if (SUCCEEDED(result)) {
            result = ReadRangeTextLimited(
                after,
                edit_cookie,
                static_cast<std::size_t>(maximum_characters),
                suffix);
        }
        SafeRelease(after);
        return result;
    }

    static bool IsSensitiveInputScope(
        ITfContext* context,
        ITfRange* range,
        TfEditCookie edit_cookie) {
        ITfReadOnlyProperty* property = nullptr;
        if (FAILED(context->GetAppProperty(kInputScopePropertyGuid, &property)) ||
            property == nullptr) {
            return false;
        }

        VARIANT value;
        VariantInit(&value);
        const HRESULT value_result = property->GetValue(edit_cookie, range, &value);
        property->Release();
        if (FAILED(value_result) || value.vt != VT_UNKNOWN || value.punkVal == nullptr) {
            VariantClear(&value);
            return false;
        }

        ITfInputScope* input_scope = nullptr;
        const HRESULT scope_result = value.punkVal->QueryInterface(
            IID_PPV_ARGS(&input_scope));
        VariantClear(&value);
        if (FAILED(scope_result) || input_scope == nullptr) {
            return false;
        }

        InputScope* scopes = nullptr;
        UINT count = 0;
        bool sensitive = false;
        if (SUCCEEDED(input_scope->GetInputScopes(&scopes, &count))) {
            for (UINT index = 0; index < count; ++index) {
                switch (scopes[index]) {
                    case IS_PASSWORD:
                    case IS_PRIVATE:
                    case IS_NUMERIC_PASSWORD:
                    case IS_NUMERIC_PIN:
                    case IS_ALPHANUMERIC_PIN:
                    case IS_ALPHANUMERIC_PIN_SET:
                        sensitive = true;
                        break;
                    default:
                        break;
                }
                if (sensitive) {
                    break;
                }
            }
        }
        CoTaskMemFree(scopes);
        input_scope->Release();
        return sensitive;
    }

    static void SetCaretAtEnd(ITfContext* context, ITfRange* source, TfEditCookie edit_cookie) {
        ITfRange* caret = nullptr;
        if (SUCCEEDED(source->Clone(&caret)) && caret != nullptr) {
            if (SUCCEEDED(caret->Collapse(edit_cookie, TF_ANCHOR_END))) {
                TF_SELECTION selection{};
                selection.range = caret;
                selection.style.ase = TF_AE_NONE;
                selection.style.fInterimChar = FALSE;
                context->SetSelection(edit_cookie, 1, &selection);
            }
            caret->Release();
        }
    }

    void ClearCompositionState() noexcept {
        composition_active_.store(false);
        SafeRelease(composition_);
        SafeRelease(context_);
        active_session_.fill(0);
        last_revision_ = 0;
        original_selection_text_.clear();
        original_selection_style_ = {};
        if (pipe_wake_event_ != nullptr) {
            SetEvent(pipe_wake_event_);
        }
    }

    bool ShouldDisconnectPipe() const noexcept {
        return !IsForegroundProcess() && !composition_active_.load();
    }

    bool ReadExactWithWake(
        HANDLE pipe,
        HANDLE read_event,
        void* target,
        DWORD byte_count) {
        auto* cursor = static_cast<std::uint8_t*>(target);
        DWORD total = 0;
        while (total < byte_count) {
            ResetEvent(read_event);
            OVERLAPPED overlapped{};
            overlapped.hEvent = read_event;
            DWORD read = 0;
            if (ReadFile(
                    pipe,
                    cursor + total,
                    byte_count - total,
                    &read,
                    &overlapped)) {
                if (read == 0) {
                    return false;
                }
                total += read;
                continue;
            }
            if (GetLastError() != ERROR_IO_PENDING) {
                return false;
            }

            HANDLE waits[] = {pipe_wake_event_, read_event};
            while (true) {
                const DWORD wait_result = WaitForMultipleObjects(
                    static_cast<DWORD>(std::size(waits)), waits, FALSE, INFINITE);
                if (wait_result == WAIT_OBJECT_0) {
                    if (stop_pipe_.load()) {
                        CancelIoEx(pipe, &overlapped);
                        WaitForSingleObject(read_event, INFINITE);
                        GetOverlappedResult(pipe, &overlapped, &read, FALSE);
                        return false;
                    }
                    if (!FlushOutgoing(pipe)) {
                        CancelIoEx(pipe, &overlapped);
                        WaitForSingleObject(read_event, INFINITE);
                        GetOverlappedResult(pipe, &overlapped, &read, FALSE);
                        return false;
                    }
                    if (ShouldDisconnectPipe()) {
                        CancelIoEx(pipe, &overlapped);
                        WaitForSingleObject(read_event, INFINITE);
                        GetOverlappedResult(pipe, &overlapped, &read, FALSE);
                        return false;
                    }
                    continue;
                }
                if (wait_result != WAIT_OBJECT_0 + 1 ||
                    !GetOverlappedResult(pipe, &overlapped, &read, FALSE) ||
                    read == 0) {
                    return false;
                }
                total += read;
                break;
            }
        }
        return true;
    }

    void PipeLoop() {
        DWORD retry_delay_ms = kDisconnectedRetryInitialMs;
        while (!stop_pipe_.load()) {
            if (!IsForegroundProcess()) {
                WaitForPipeWake(INFINITE);
                continue;
            }
            HANDLE connected_pipe = CreateFileW(
                caps_writer::tsf::kPipeName,
                GENERIC_READ | GENERIC_WRITE,
                0,
                nullptr,
                OPEN_EXISTING,
                FILE_FLAG_OVERLAPPED |
                    SECURITY_SQOS_PRESENT |
                    SECURITY_IDENTIFICATION,
                nullptr);
            if (connected_pipe == INVALID_HANDLE_VALUE) {
                WaitForPipeWake(retry_delay_ms);
                retry_delay_ms = std::min(
                    retry_delay_ms * 2, kDisconnectedRetryMaximumMs);
                continue;
            }
            retry_delay_ms = kDisconnectedRetryInitialMs;
            connected_.store(true);
            HANDLE read_event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
            if (read_event == nullptr || !SendHello(connected_pipe)) {
                connected_.store(false);
                if (read_event != nullptr) {
                    CloseHandle(read_event);
                }
                CloseHandle(connected_pipe);
                WaitForPipeWake(retry_delay_ms);
                continue;
            }
            while (!stop_pipe_.load()) {
                if (!FlushOutgoing(connected_pipe)) {
                    break;
                }
                if (ShouldDisconnectPipe()) {
                    break;
                }
                auto frame = std::make_unique<Frame>();
                if (!ReadExactWithWake(
                        connected_pipe,
                        read_event,
                        &frame->header,
                        sizeof(FrameHeader))) {
                    break;
                }
                if (frame->header.magic != caps_writer::tsf::kMagic ||
                    frame->header.version != caps_writer::tsf::kVersion ||
                    frame->header.text_bytes > caps_writer::tsf::kMaxTextBytes ||
                    (frame->header.text_bytes % sizeof(wchar_t)) != 0) {
                    break;
                }
                if (frame->header.text_bytes > 0) {
                    frame->text.resize(frame->header.text_bytes / sizeof(wchar_t));
                    if (!ReadExactWithWake(
                        connected_pipe,
                        read_event,
                        frame->text.data(),
                        frame->header.text_bytes)) {
                        break;
                    }
                }
                Frame* posted = frame.release();
                if (!PostMessageW(dispatch_window_, kPipeFrameMessage, 0, reinterpret_cast<LPARAM>(posted))) {
                    delete posted;
                    break;
                }
            }
            connected_.store(false);
            CancelIoEx(connected_pipe, nullptr);
            CloseHandle(read_event);
            CloseHandle(connected_pipe);
            ClearOutgoing();
            WaitForPipeWake(retry_delay_ms);
        }
        connected_.store(false);
        ClearOutgoing();
    }

    static bool SendHello(HANDLE pipe) {
        FrameHeader hello{};
        hello.magic = caps_writer::tsf::kMagic;
        hello.version = caps_writer::tsf::kVersion;
        hello.operation = static_cast<std::uint16_t>(Operation::Hello);
        hello.status = GetCurrentProcessId();
        return WriteExact(pipe, &hello, sizeof(hello));
    }

    void SendAck(
        const Frame& request,
        Status status,
        const std::wstring& response_text = {}) {
        if (!connected_.load()) {
            return;
        }
        Frame response{};
        response.header = request.header;
        response.header.operation = static_cast<std::uint16_t>(
            request.header.operation | static_cast<std::uint16_t>(Operation::AckFlag));
        response.text = response_text;
        response.header.text_bytes = static_cast<std::uint32_t>(
            response.text.size() * sizeof(wchar_t));
        response.header.status = static_cast<std::uint32_t>(status);
        {
            std::scoped_lock lock(outgoing_mutex_);
            if (outgoing_.size() >= kMaximumOutgoingFrames) {
                outgoing_.pop_front();
            }
            outgoing_.push_back(response);
        }
        if (pipe_wake_event_ != nullptr) {
            SetEvent(pipe_wake_event_);
        }
    }

    void SendCompositionTerminated(
        const std::array<std::uint8_t, 16>& session_id,
        std::uint64_t revision,
        Status rollback_status) {
        if (!connected_.load()) {
            return;
        }
        Frame event{};
        event.header.magic = caps_writer::tsf::kMagic;
        event.header.version = caps_writer::tsf::kVersion;
        event.header.operation = static_cast<std::uint16_t>(
            Operation::CompositionTerminated);
        event.header.revision = revision;
        event.header.session_id = session_id;
        event.header.status = static_cast<std::uint32_t>(rollback_status);
        {
            std::scoped_lock lock(outgoing_mutex_);
            if (outgoing_.size() >= kMaximumOutgoingFrames) {
                outgoing_.pop_front();
            }
            outgoing_.push_back(event);
        }
        if (pipe_wake_event_ != nullptr) {
            SetEvent(pipe_wake_event_);
        }
    }

    bool FlushOutgoing(HANDLE pipe) {
        while (!stop_pipe_.load()) {
            Frame response{};
            {
                std::scoped_lock lock(outgoing_mutex_);
                if (outgoing_.empty()) {
                    return true;
                }
                response = outgoing_.front();
                outgoing_.pop_front();
            }
            if (!WriteExact(pipe, &response.header, sizeof(response.header))) {
                return false;
            }
            if (!response.text.empty() &&
                !WriteExact(
                    pipe,
                    response.text.data(),
                    static_cast<DWORD>(response.header.text_bytes))) {
                return false;
            }
        }
        return false;
    }

    void ClearOutgoing() noexcept {
        std::scoped_lock lock(outgoing_mutex_);
        outgoing_.clear();
    }

    void WaitForPipeWake(DWORD timeout_ms) const noexcept {
        if (pipe_wake_event_ != nullptr && !stop_pipe_.load()) {
            WaitForSingleObject(pipe_wake_event_, timeout_ms);
        }
    }

    std::atomic<ULONG> ref_count_{1};
    inline static std::mutex foreground_hooks_mutex_;
    inline static std::unordered_map<HWINEVENTHOOK, TextService*> foreground_hooks_;
    ITfThreadMgr* thread_manager_ = nullptr;
    TfClientId client_id_ = TF_CLIENTID_NULL;
    ITfCategoryMgr* category_manager_ = nullptr;
    std::array<TfGuidAtom, 2> display_attribute_atoms_{
        TF_INVALID_GUIDATOM,
        TF_INVALID_GUIDATOM,
    };
    DWORD activation_thread_id_ = 0;
    HWND dispatch_window_ = nullptr;
    std::thread pipe_thread_;
    std::atomic<bool> stop_pipe_{false};
    std::atomic<bool> connected_{false};
    HANDLE pipe_wake_event_ = nullptr;
    HWINEVENTHOOK foreground_hook_ = nullptr;
    std::mutex outgoing_mutex_;
    std::deque<Frame> outgoing_;

    ITfContext* context_ = nullptr;
    ITfComposition* composition_ = nullptr;
    std::atomic<bool> composition_active_{false};
    bool ending_composition_ = false;
    std::array<std::uint8_t, 16> active_session_{};
    std::uint64_t last_revision_ = 0;
    caps_writer::tsf::EditSessionQueue edit_queue_;
    std::wstring original_selection_text_;
    TF_SELECTIONSTYLE original_selection_style_{};

};

EditSession::EditSession(TextService* service, ITfContext* context, Frame frame) noexcept
    : service_(service), context_(context), frame_(std::move(frame)) {
    service_->AddRef();
    context_->AddRef();
}

EditSession::~EditSession() {
    context_->Release();
    service_->Release();
}

STDMETHODIMP EditSession::QueryInterface(REFIID iid, void** object) {
    if (object == nullptr) {
        return E_INVALIDARG;
    }
    *object = nullptr;
    if (iid != IID_IUnknown && iid != IID_ITfEditSession) {
        return E_NOINTERFACE;
    }
    *object = static_cast<ITfEditSession*>(this);
    AddRef();
    return S_OK;
}

STDMETHODIMP_(ULONG) EditSession::AddRef() { return ++ref_count_; }

STDMETHODIMP_(ULONG) EditSession::Release() {
    const ULONG remaining = --ref_count_;
    if (remaining == 0) {
        delete this;
    }
    return remaining;
}

STDMETHODIMP EditSession::DoEditSession(TfEditCookie edit_cookie) {
    const HRESULT result = service_->ApplyEdit(context_, frame_, edit_cookie);
    service_->CompleteEditSession();
    return result;
}

class ClassFactory final : public IClassFactory {
public:
    ClassFactory() noexcept { ++g_object_count; }

    STDMETHODIMP QueryInterface(REFIID iid, void** object) override {
        if (object == nullptr) {
            return E_INVALIDARG;
        }
        *object = nullptr;
        if (iid != IID_IUnknown && iid != IID_IClassFactory) {
            return E_NOINTERFACE;
        }
        *object = static_cast<IClassFactory*>(this);
        AddRef();
        return S_OK;
    }
    STDMETHODIMP_(ULONG) AddRef() override { return ++ref_count_; }
    STDMETHODIMP_(ULONG) Release() override {
        const ULONG remaining = --ref_count_;
        if (remaining == 0) {
            delete this;
        }
        return remaining;
    }
    STDMETHODIMP CreateInstance(IUnknown* outer, REFIID iid, void** object) override {
        if (outer != nullptr) {
            return CLASS_E_NOAGGREGATION;
        }
        auto* service = new (std::nothrow) TextService();
        if (service == nullptr) {
            return E_OUTOFMEMORY;
        }
        const HRESULT result = service->QueryInterface(iid, object);
        service->Release();
        return result;
    }
    STDMETHODIMP LockServer(BOOL lock) override {
        if (lock) {
            ++g_lock_count;
        } else {
            --g_lock_count;
        }
        return S_OK;
    }

private:
    ~ClassFactory() { --g_object_count; }
    std::atomic<ULONG> ref_count_{1};
};

HRESULT SetComRegistration(bool install) {
    wchar_t clsid_text[64]{};
    if (StringFromGUID2(kTextServiceClsid, clsid_text, ARRAYSIZE(clsid_text)) == 0) {
        return E_FAIL;
    }
    const std::wstring key_path =
        std::wstring(L"Software\\Classes\\CLSID\\") + clsid_text;
    if (!install) {
        const LSTATUS status = RegDeleteTreeW(HKEY_CURRENT_USER, key_path.c_str());
        return status == ERROR_SUCCESS || status == ERROR_FILE_NOT_FOUND
            ? S_OK
            : HRESULT_FROM_WIN32(status);
    }

    wchar_t module_path[MAX_PATH]{};
    const DWORD length = GetModuleFileNameW(g_instance, module_path, ARRAYSIZE(module_path));
    if (length == 0 || length == ARRAYSIZE(module_path)) {
        return HRESULT_FROM_WIN32(GetLastError());
    }
    HKEY key = nullptr;
    LSTATUS status = RegCreateKeyExW(
        HKEY_CURRENT_USER,
        (key_path + L"\\InprocServer32").c_str(),
        0,
        nullptr,
        0,
        KEY_SET_VALUE,
        nullptr,
        &key,
        nullptr);
    if (status != ERROR_SUCCESS) {
        return HRESULT_FROM_WIN32(status);
    }
    status = RegSetValueExW(
        key,
        nullptr,
        0,
        REG_SZ,
        reinterpret_cast<const BYTE*>(module_path),
        static_cast<DWORD>((length + 1) * sizeof(wchar_t)));
    if (status == ERROR_SUCCESS) {
        constexpr wchar_t threading_model[] = L"Apartment";
        status = RegSetValueExW(
            key,
            L"ThreadingModel",
            0,
            REG_SZ,
            reinterpret_cast<const BYTE*>(threading_model),
            sizeof(threading_model));
    }
    RegCloseKey(key);
    return status == ERROR_SUCCESS ? S_OK : HRESULT_FROM_WIN32(status);
}

HRESULT SetTsfRegistration(bool install) {
    ITfInputProcessorProfiles* profiles = nullptr;
    HRESULT result = CoCreateInstance(
        CLSID_TF_InputProcessorProfiles,
        nullptr,
        CLSCTX_INPROC_SERVER,
        IID_PPV_ARGS(&profiles));
    if (FAILED(result)) {
        return result;
    }
    constexpr LANGID language = MAKELANGID(LANG_CHINESE, SUBLANG_CHINESE_SIMPLIFIED);
    if (install) {
        result = profiles->Register(kTextServiceClsid);
        if (SUCCEEDED(result)) {
            result = profiles->AddLanguageProfile(
                kTextServiceClsid,
                language,
                kLanguageProfileGuid,
                kProfileDescription,
                static_cast<ULONG>(std::size(kProfileDescription) - 1),
                nullptr,
                0,
                0);
        }
        if (SUCCEEDED(result)) {
            result = profiles->EnableLanguageProfile(
                kTextServiceClsid, language, kLanguageProfileGuid, TRUE);
        }
        profiles->Release();
        if (FAILED(result)) {
            return result;
        }
    } else {
        // TSF cleanup APIs commonly return E_FAIL when registration is already
        // absent or only partially present. Unregistration must remain
        // repeatable, so attempt every cleanup operation and treat missing
        // state as success. The COM class removal remains the authoritative
        // activation barrier.
        profiles->EnableLanguageProfile(
            kTextServiceClsid, language, kLanguageProfileGuid, FALSE);
        profiles->RemoveLanguageProfile(
            kTextServiceClsid, language, kLanguageProfileGuid);
        profiles->Unregister(kTextServiceClsid);
        profiles->Release();
    }

    ITfCategoryMgr* category_manager = nullptr;
    result = CoCreateInstance(
        CLSID_TF_CategoryMgr,
        nullptr,
        CLSCTX_INPROC_SERVER,
        IID_PPV_ARGS(&category_manager));
    if (FAILED(result)) {
        return result;
    }
    if (install) {
        result = category_manager->RegisterCategory(
            kTextServiceClsid, GUID_TFCAT_TIP_SPEECH, kTextServiceClsid);
        if (SUCCEEDED(result)) {
            result = category_manager->RegisterCategory(
                kTextServiceClsid,
                GUID_TFCAT_DISPLAYATTRIBUTEPROVIDER,
                kTextServiceClsid);
        }
        if (FAILED(result)) {
            category_manager->UnregisterCategory(
                kTextServiceClsid, GUID_TFCAT_TIP_SPEECH, kTextServiceClsid);
        }
    } else {
        category_manager->UnregisterCategory(
            kTextServiceClsid,
            GUID_TFCAT_DISPLAYATTRIBUTEPROVIDER,
            kTextServiceClsid);
        category_manager->UnregisterCategory(
            kTextServiceClsid, GUID_TFCAT_TIP_SPEECH, kTextServiceClsid);
        result = S_OK;
    }
    category_manager->Release();
    return result;
}

}  // namespace

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, void*) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_instance = instance;
        DisableThreadLibraryCalls(instance);
    }
    return TRUE;
}

STDAPI DllCanUnloadNow() {
    return g_object_count.load() == 0 && g_lock_count.load() == 0 ? S_OK : S_FALSE;
}

STDAPI DllGetClassObject(
    REFCLSID clsid,
    REFIID iid,
    void** object) {
    if (clsid != kTextServiceClsid) {
        return CLASS_E_CLASSNOTAVAILABLE;
    }
    auto* factory = new (std::nothrow) ClassFactory();
    if (factory == nullptr) {
        return E_OUTOFMEMORY;
    }
    const HRESULT result = factory->QueryInterface(iid, object);
    factory->Release();
    return result;
}

STDAPI DllRegisterServer() {
    const HRESULT init_result = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    const bool uninitialize = SUCCEEDED(init_result);
    HRESULT result = SetComRegistration(true);
    if (SUCCEEDED(result)) {
        result = SetTsfRegistration(true);
    }
    if (FAILED(result)) {
        SetTsfRegistration(false);
        SetComRegistration(false);
    }
    if (uninitialize) {
        CoUninitialize();
    }
    return result;
}

STDAPI DllUnregisterServer() {
    const HRESULT init_result = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    const bool uninitialize = SUCCEEDED(init_result);
    const HRESULT tsf_result = SetTsfRegistration(false);
    const HRESULT com_result = SetComRegistration(false);
    if (uninitialize) {
        CoUninitialize();
    }
    return FAILED(tsf_result) ? tsf_result : com_result;
}
