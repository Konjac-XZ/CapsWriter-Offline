#include <windows.h>
#include <msctf.h>

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
#include <utility>

#include "edit_session_queue.h"
#include "protocol.h"

using caps_writer::tsf::Frame;
using caps_writer::tsf::FrameHeader;
using caps_writer::tsf::Operation;
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

constexpr wchar_t kProfileDescription[] = L"CapsWriter Speech Composition (Experimental)";
constexpr wchar_t kWindowClassName[] = L"CapsWriter.TsfSpeechTip.Dispatch.v1";
constexpr UINT kPipeFrameMessage = WM_APP + 0x341;
constexpr UINT kPumpEditQueueMessage = WM_APP + 0x342;
constexpr DWORD kDisconnectedRetryInitialMs = 100;
constexpr DWORD kDisconnectedRetryMaximumMs = 5000;
constexpr DWORD kBackgroundPollMs = 250;
constexpr DWORD kConnectedPollMs = 25;
constexpr std::size_t kMaximumOutgoingFrames = 256;

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

bool ReadExact(HANDLE pipe, void* target, DWORD byte_count) {
    auto* cursor = static_cast<std::uint8_t*>(target);
    DWORD total = 0;
    while (total < byte_count) {
        DWORD read = 0;
        if (!ReadFile(pipe, cursor + total, byte_count - total, &read, nullptr) || read == 0) {
            return false;
        }
        total += read;
    }
    return true;
}

bool WriteExact(HANDLE pipe, const void* source, DWORD byte_count) {
    const auto* cursor = static_cast<const std::uint8_t*>(source);
    DWORD total = 0;
    while (total < byte_count) {
        DWORD written = 0;
        if (!WriteFile(pipe, cursor + total, byte_count - total, &written, nullptr) || written == 0) {
            return false;
        }
        total += written;
    }
    return true;
}

class TextService;

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

class TextService final : public ITfTextInputProcessorEx, public ITfCompositionSink {
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
        try {
            pipe_thread_ = std::thread([this] { PipeLoop(); });
        } catch (...) {
            CloseHandle(pipe_wake_event_);
            pipe_wake_event_ = nullptr;
            DestroyWindow(dispatch_window_);
            dispatch_window_ = nullptr;
            SafeRelease(thread_manager_);
            client_id_ = TF_CLIENTID_NULL;
            activation_thread_id_ = 0;
            return E_OUTOFMEMORY;
        }
        return S_OK;
    }

    STDMETHODIMP Deactivate() override {
        stop_pipe_.store(true);
        if (pipe_wake_event_ != nullptr) {
            SetEvent(pipe_wake_event_);
        }
        if (pipe_thread_.joinable()) {
            CancelSynchronousIo(pipe_thread_.native_handle());
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
        SafeRelease(thread_manager_);
        client_id_ = TF_CLIENTID_NULL;
        activation_thread_id_ = 0;
        return S_OK;
    }

    STDMETHODIMP OnCompositionTerminated(
        TfEditCookie /*edit_cookie*/,
        ITfComposition* composition) override {
        if (composition_ == composition) {
            ClearCompositionState();
        }
        return S_OK;
    }

    HRESULT ApplyEdit(ITfContext* context, const Frame& frame, TfEditCookie edit_cookie) {
        const auto operation = static_cast<Operation>(frame.header.operation);
        Status status = Status::EditSessionFailed;
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
            default:
                status = Status::InvalidFrame;
                break;
        }
        SendAck(frame, status);
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
        if (operation == Operation::Begin && !IsForegroundProcess()) {
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
            if (operation == Operation::Begin) {
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
            const HRESULT request_result = context->RequestEditSession(
                client_id_,
                edit_session,
                TF_ES_ASYNC | TF_ES_READWRITE,
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
                SetCaretAtEnd(context, range, edit_cookie);
                last_revision_ = frame.header.revision;
            }
            range->Release();
        }
        return SUCCEEDED(result) ? Status::Applied : Status::EditSessionFailed;
    }

    Status ApplyCommit(ITfContext* /*context*/, const Frame& frame, TfEditCookie edit_cookie) {
        if (composition_ == nullptr || !SameSession(active_session_, frame.header.session_id)) {
            return Status::InactiveSession;
        }
        if (frame.header.revision <= last_revision_) {
            return Status::StaleRevision;
        }
        ITfComposition* composition = composition_;
        composition->AddRef();
        const HRESULT result = composition->EndComposition(edit_cookie);
        composition->Release();
        if (SUCCEEDED(result)) {
            ClearCompositionState();
        }
        return SUCCEEDED(result) ? Status::Applied : Status::EditSessionFailed;
    }

    Status ApplyCancel(ITfContext* context, const Frame& frame, TfEditCookie edit_cookie) {
        if (composition_ == nullptr || !SameSession(active_session_, frame.header.session_id)) {
            return Status::InactiveSession;
        }
        ITfRange* range = nullptr;
        HRESULT result = composition_->GetRange(&range);
        if (SUCCEEDED(result) && range != nullptr) {
            result = range->SetText(
                edit_cookie,
                0,
                original_selection_text_.data(),
                static_cast<LONG>(original_selection_text_.size()));
        }
        const TF_SELECTIONSTYLE original_selection_style = original_selection_style_;
        ITfComposition* composition = composition_;
        composition->AddRef();
        const HRESULT end_result = composition->EndComposition(edit_cookie);
        composition->Release();
        HRESULT selection_result = E_FAIL;
        if (SUCCEEDED(result) && SUCCEEDED(end_result) && range != nullptr) {
            TF_SELECTION selection{};
            selection.range = range;
            selection.style = original_selection_style;
            selection_result = context->SetSelection(edit_cookie, 1, &selection);
        }
        SafeRelease(range);
        if (SUCCEEDED(end_result)) {
            ClearCompositionState();
        }
        if (SUCCEEDED(result) && SUCCEEDED(end_result) && SUCCEEDED(selection_result)) {
            return Status::Applied;
        }
        return Status::EditSessionFailed;
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
    }

    void PipeLoop() {
        DWORD retry_delay_ms = kDisconnectedRetryInitialMs;
        while (!stop_pipe_.load()) {
            if (!IsForegroundProcess()) {
                WaitForPipeWake(kBackgroundPollMs);
                continue;
            }
            if (!WaitNamedPipeW(caps_writer::tsf::kPipeName, 500)) {
                WaitForPipeWake(retry_delay_ms);
                retry_delay_ms = std::min(
                    retry_delay_ms * 2, kDisconnectedRetryMaximumMs);
                continue;
            }
            HANDLE connected_pipe = CreateFileW(
                caps_writer::tsf::kPipeName,
                GENERIC_READ | GENERIC_WRITE,
                0,
                nullptr,
                OPEN_EXISTING,
                SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION,
                nullptr);
            if (connected_pipe == INVALID_HANDLE_VALUE) {
                WaitForPipeWake(retry_delay_ms);
                retry_delay_ms = std::min(
                    retry_delay_ms * 2, kDisconnectedRetryMaximumMs);
                continue;
            }
            retry_delay_ms = kDisconnectedRetryInitialMs;
            connected_.store(true);
            if (!SendHello(connected_pipe)) {
                connected_.store(false);
                CloseHandle(connected_pipe);
                WaitForPipeWake(retry_delay_ms);
                continue;
            }
            while (!stop_pipe_.load()) {
                if (!IsForegroundProcess() && !composition_active_.load()) {
                    break;
                }
                if (!FlushOutgoing(connected_pipe)) {
                    break;
                }
                DWORD available = 0;
                if (!PeekNamedPipe(
                        connected_pipe, nullptr, 0, nullptr, &available, nullptr)) {
                    break;
                }
                if (available < sizeof(FrameHeader)) {
                    WaitForPipeWake(kConnectedPollMs);
                    continue;
                }
                auto frame = std::make_unique<Frame>();
                if (!ReadExact(connected_pipe, &frame->header, sizeof(FrameHeader))) {
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
                    if (!ReadExact(
                        connected_pipe,
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

    void SendAck(const Frame& request, Status status) {
        if (!connected_.load()) {
            return;
        }
        FrameHeader response = request.header;
        response.operation = static_cast<std::uint16_t>(
            request.header.operation | static_cast<std::uint16_t>(Operation::AckFlag));
        response.text_bytes = 0;
        response.status = static_cast<std::uint32_t>(status);
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

    bool FlushOutgoing(HANDLE pipe) {
        while (!stop_pipe_.load()) {
            FrameHeader response{};
            {
                std::scoped_lock lock(outgoing_mutex_);
                if (outgoing_.empty()) {
                    return true;
                }
                response = outgoing_.front();
                outgoing_.pop_front();
            }
            if (!WriteExact(pipe, &response, sizeof(response))) {
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
    ITfThreadMgr* thread_manager_ = nullptr;
    TfClientId client_id_ = TF_CLIENTID_NULL;
    DWORD activation_thread_id_ = 0;
    HWND dispatch_window_ = nullptr;
    std::thread pipe_thread_;
    std::atomic<bool> stop_pipe_{false};
    std::atomic<bool> connected_{false};
    HANDLE pipe_wake_event_ = nullptr;
    std::mutex outgoing_mutex_;
    std::deque<FrameHeader> outgoing_;

    ITfContext* context_ = nullptr;
    ITfComposition* composition_ = nullptr;
    std::atomic<bool> composition_active_{false};
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
    result = install
        ? category_manager->RegisterCategory(
              kTextServiceClsid, GUID_TFCAT_TIP_SPEECH, kTextServiceClsid)
        : category_manager->UnregisterCategory(
              kTextServiceClsid, GUID_TFCAT_TIP_SPEECH, kTextServiceClsid);
    category_manager->Release();
    return install ? result : S_OK;
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
