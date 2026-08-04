from __future__ import annotations

import asyncio
import ctypes
import logging
import platform
import queue
import threading
import uuid
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Callable

from .protocol import (
    HEADER,
    Frame,
    Operation,
    Status,
    decode_frame,
    decode_header,
    encode_frame,
)

PIPE_NAME = r"\\.\pipe\CapsWriter.TsfSpeechTip.v1"
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
ERROR_PIPE_CONNECTED = 535
ERROR_IO_PENDING = 997
FILE_FLAG_OVERLAPPED = 0x40000000
WAIT_OBJECT_0 = 0
WAIT_FAILED = 0xFFFFFFFF
INFINITE = 0xFFFFFFFF
PIPE_UNLIMITED_INSTANCES = 255
PIPE_ACCESS_DUPLEX = 0x00000003
PIPE_TYPE_BYTE = 0x00000000
PIPE_READMODE_BYTE = 0x00000000
PIPE_WAIT = 0x00000000
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
OPEN_EXISTING = 3
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
TOKEN_QUERY = 0x0008
TOKEN_USER = 1
SDDL_REVISION_1 = 1

_LOGGER = logging.getLogger("capswriter.tsf.pipe")


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", wintypes.LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    ]


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]


class TOKEN_USER_STRUCT(ctypes.Structure):
    _fields_ = [("User", SID_AND_ATTRIBUTES)]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


@dataclass(slots=True)
class _PipeClient:
    handle: int
    process_id: int = 0
    process_name: str | None = None
    outgoing: queue.Queue[tuple[Frame, bytes] | None] = field(
        default_factory=lambda: queue.Queue(maxsize=256)
    )
    queue_lock: threading.Lock = field(default_factory=threading.Lock)
    outgoing_event: int = 0
    stop_event: int = 0
    service_thread: threading.Thread | None = None


@dataclass(slots=True)
class _PendingRequest:
    future: asyncio.Future[BrokerReply | None]
    client_handles: set[int]
    last_negative_ack: BrokerReply | None = None


@dataclass(frozen=True, slots=True)
class BrokerReply:
    frame: Frame
    process_id: int = 0
    process_name: str | None = None


class WindowsNamedPipeBroker:
    """Same-user, local-only named-pipe server used by all in-process TIP clients."""

    def __init__(self, pipe_name: str = PIPE_NAME) -> None:
        self.pipe_name = pipe_name
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._startup_error: Exception | None = None
        self._accept_thread: threading.Thread | None = None
        self._clients: dict[int, _PipeClient] = {}
        self._clients_lock = threading.Lock()
        self._pending: dict[tuple[uuid.UUID, int, int], list[_PendingRequest]] = {}
        self._pending_lock = threading.Lock()
        self._event_handler: Callable[[Frame], None] | None = None
        self._client_event: asyncio.Event | None = None

    @property
    def client_count(self) -> int:
        with self._clients_lock:
            return len(self._clients)

    @property
    def startup_error(self) -> Exception | None:
        return self._startup_error

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> bool:
        if platform.system() != "Windows":
            return False
        if self._accept_thread is not None and self._accept_thread.is_alive():
            return True
        self._loop = loop or asyncio.get_running_loop()
        self._client_event = asyncio.Event()
        if self.client_count:
            self._client_event.set()
        self._stop.clear()
        self._ready.clear()
        self._startup_error = None
        self._configure_winapi()
        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name="capswriter-tsf-pipe-accept",
            daemon=True,
        )
        self._accept_thread.start()
        self._ready.wait(timeout=0.5)
        return self._ready.is_set() and self._startup_error is None

    def set_event_handler(self, handler: Callable[[Frame], None] | None) -> None:
        """Receive unsolicited lifecycle events on the broker event loop."""
        self._event_handler = handler

    def stop(self) -> None:
        self._stop.set()
        if platform.system() == "Windows" and hasattr(self, "_kernel32"):
            wake = self._kernel32.CreateFileW(
                self.pipe_name,
                GENERIC_READ | GENERIC_WRITE,
                0,
                None,
                OPEN_EXISTING,
                0,
                None,
            )
            if wake != INVALID_HANDLE_VALUE:
                self._kernel32.CloseHandle(wake)
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=1.0)
            self._accept_thread = None
        with self._clients_lock:
            clients = list(self._clients.values())
        for client in clients:
            self._remove_client(client.handle)
        for client in clients:
            if client.service_thread is not None:
                client.service_thread.join(timeout=1.0)
        self._fail_pending(RuntimeError("TSF named-pipe broker stopped"))

    async def wait_for_client(self, timeout: float) -> bool:
        if self.client_count:
            return True
        event = self._client_event
        if event is None:
            return False
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            return False
        return self.client_count > 0

    async def request(self, frame: Frame, timeout: float) -> BrokerReply | None:
        loop = self._loop or asyncio.get_running_loop()
        key = (frame.session_id, frame.revision, int(frame.operation))
        future: asyncio.Future[BrokerReply | None] = loop.create_future()
        encoded = encode_frame(frame)
        with self._clients_lock:
            clients = list(self._clients.values())
            if not clients:
                return None
            pending = _PendingRequest(
                future=future,
                client_handles={client.handle for client in clients},
            )
            with self._pending_lock:
                self._pending.setdefault(key, []).append(pending)
        sent = 0
        for client in clients:
            try:
                self._enqueue_frame(client, frame, encoded)
                sent += 1
            except queue.Full:
                self._remove_client(client.handle)
        if sent == 0:
            self._remove_pending(key, pending)
            return None
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            return None
        finally:
            self._remove_pending(key, pending)

    def broadcast(self, frame: Frame) -> int:
        encoded = encode_frame(frame)
        with self._clients_lock:
            clients = list(self._clients.values())
        sent = 0
        for client in clients:
            try:
                self._enqueue_frame(client, frame, encoded)
                sent += 1
            except queue.Full:
                self._remove_client(client.handle)
        return sent

    def _enqueue_frame(self, client: _PipeClient, frame: Frame, encoded: bytes) -> None:
        with client.queue_lock:
            retained: list[tuple[Frame, bytes] | None] = []
            if frame.operation == int(Operation.REVISE):
                while True:
                    try:
                        queued = client.outgoing.get_nowait()
                    except queue.Empty:
                        break
                    if (
                        queued is not None
                        and queued[0].operation == int(Operation.REVISE)
                        and queued[0].session_id == frame.session_id
                    ):
                        continue
                    retained.append(queued)
            for queued in retained:
                client.outgoing.put_nowait(queued)
            client.outgoing.put_nowait((frame, encoded))
        if client.outgoing_event:
            self._kernel32.SetEvent(client.outgoing_event)

    def _configure_winapi(self) -> None:
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._kernel32.CreateNamedPipeW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(SECURITY_ATTRIBUTES),
        ]
        self._kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
        self._kernel32.ConnectNamedPipe.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(OVERLAPPED),
        ]
        self._kernel32.ConnectNamedPipe.restype = wintypes.BOOL
        self._kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self._kernel32.CreateFileW.restype = wintypes.HANDLE
        self._kernel32.ReadFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        self._kernel32.ReadFile.restype = wintypes.BOOL
        self._kernel32.WriteFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        self._kernel32.WriteFile.restype = wintypes.BOOL
        self._kernel32.CreateEventW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self._kernel32.CreateEventW.restype = wintypes.HANDLE
        self._kernel32.SetEvent.argtypes = [wintypes.HANDLE]
        self._kernel32.SetEvent.restype = wintypes.BOOL
        self._kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
        self._kernel32.ResetEvent.restype = wintypes.BOOL
        self._kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self._kernel32.WaitForMultipleObjects.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self._kernel32.WaitForMultipleObjects.restype = wintypes.DWORD
        self._kernel32.GetOverlappedResult.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(OVERLAPPED),
            ctypes.POINTER(wintypes.DWORD),
            wintypes.BOOL,
        ]
        self._kernel32.GetOverlappedResult.restype = wintypes.BOOL
        self._kernel32.CancelIoEx.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(OVERLAPPED),
        ]
        self._kernel32.CancelIoEx.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        self._kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        self._kernel32.LocalFree.restype = wintypes.HLOCAL
        self._advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        self._advapi32.OpenProcessToken.restype = wintypes.BOOL
        self._advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._advapi32.GetTokenInformation.restype = wintypes.BOOL
        self._advapi32.ConvertSidToStringSidW.argtypes = [
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.LPWSTR),
        ]
        self._advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
        self._advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
            wintypes.BOOL
        )

    def _security_attributes(self) -> tuple[SECURITY_ATTRIBUTES, int]:
        token = wintypes.HANDLE()
        if not self._advapi32.OpenProcessToken(
            self._kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            needed = wintypes.DWORD()
            self._advapi32.GetTokenInformation(
                token, TOKEN_USER, None, 0, ctypes.byref(needed)
            )
            buf = ctypes.create_string_buffer(needed.value)
            if not self._advapi32.GetTokenInformation(
                token, TOKEN_USER, buf, needed, ctypes.byref(needed)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            token_user = ctypes.cast(buf, ctypes.POINTER(TOKEN_USER_STRUCT)).contents
            sid_string = wintypes.LPWSTR()
            if not self._advapi32.ConvertSidToStringSidW(
                token_user.User.Sid, ctypes.byref(sid_string)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                sddl = f"D:P(A;;GA;;;{sid_string.value})"
            finally:
                self._kernel32.LocalFree(sid_string)
        finally:
            self._kernel32.CloseHandle(token)

        descriptor = wintypes.LPVOID()
        if not self._advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, SDDL_REVISION_1, ctypes.byref(descriptor), None
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        attributes = SECURITY_ATTRIBUTES(
            ctypes.sizeof(SECURITY_ATTRIBUTES), descriptor, False
        )
        if descriptor.value is None:
            raise RuntimeError("Security descriptor conversion returned a null pointer")
        return attributes, int(descriptor.value)

    def _accept_loop(self) -> None:
        retry_delay = 0.1
        while not self._stop.is_set():
            try:
                attributes, descriptor = self._security_attributes()
                try:
                    handle = self._kernel32.CreateNamedPipeW(
                        self.pipe_name,
                        PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED,
                        PIPE_TYPE_BYTE
                        | PIPE_READMODE_BYTE
                        | PIPE_WAIT
                        | PIPE_REJECT_REMOTE_CLIENTS,
                        PIPE_UNLIMITED_INSTANCES,
                        64 * 1024,
                        64 * 1024,
                        0,
                        ctypes.byref(attributes),
                    )
                finally:
                    self._kernel32.LocalFree(descriptor)
                if handle == INVALID_HANDLE_VALUE:
                    error = ctypes.WinError(ctypes.get_last_error())
                    if not self._ready.is_set():
                        self._startup_error = error
                        self._ready.set()
                        return
                    self._startup_error = error
                    if self._stop.wait(retry_delay):
                        return
                    retry_delay = min(retry_delay * 2, 5.0)
                    continue
                self._ready.set()
                self._startup_error = None
                retry_delay = 0.1
                connect_event = self._kernel32.CreateEventW(None, True, False, None)
                if not connect_event:
                    self._kernel32.CloseHandle(handle)
                    raise ctypes.WinError(ctypes.get_last_error())
                connect_overlapped = OVERLAPPED()
                connect_overlapped.hEvent = connect_event
                connected = bool(
                    self._kernel32.ConnectNamedPipe(
                        handle, ctypes.byref(connect_overlapped)
                    )
                )
                if not connected:
                    connect_error = ctypes.get_last_error()
                    if connect_error == ERROR_IO_PENDING:
                        connected = self._kernel32.WaitForSingleObject(
                            connect_event, INFINITE
                        ) == WAIT_OBJECT_0 and bool(
                            self._kernel32.GetOverlappedResult(
                                handle,
                                ctypes.byref(connect_overlapped),
                                ctypes.byref(wintypes.DWORD()),
                                False,
                            )
                        )
                    elif connect_error == ERROR_PIPE_CONNECTED:
                        connected = True
                self._kernel32.CloseHandle(connect_event)
                if not connected:
                    self._kernel32.CloseHandle(handle)
                    if self._stop.wait(retry_delay):
                        return
                    retry_delay = min(retry_delay * 2, 5.0)
                    continue
                if self._stop.is_set():
                    self._kernel32.CloseHandle(handle)
                    return
                outgoing_event = self._kernel32.CreateEventW(None, False, False, None)
                stop_event = self._kernel32.CreateEventW(None, True, False, None)
                if not outgoing_event or not stop_event:
                    if outgoing_event:
                        self._kernel32.CloseHandle(outgoing_event)
                    if stop_event:
                        self._kernel32.CloseHandle(stop_event)
                    self._kernel32.CloseHandle(handle)
                    raise ctypes.WinError(ctypes.get_last_error())
                client = _PipeClient(
                    int(handle),
                    outgoing_event=int(outgoing_event),
                    stop_event=int(stop_event),
                )
                with self._clients_lock:
                    self._clients[client.handle] = client
                self._notify_client_change()
                service_thread = threading.Thread(
                    target=self._service_client,
                    args=(client,),
                    name=f"capswriter-tsf-pipe-{client.handle}",
                    daemon=True,
                )
                client.service_thread = service_thread
                service_thread.start()
            except Exception as exc:
                self._startup_error = exc
                self._ready.set()
                return

    def _service_client(self, client: _PipeClient) -> None:
        read_event = self._kernel32.CreateEventW(None, True, False, None)
        write_event = self._kernel32.CreateEventW(None, True, False, None)
        if not read_event or not write_event:
            if read_event:
                self._kernel32.CloseHandle(read_event)
            if write_event:
                self._kernel32.CloseHandle(write_event)
            self._remove_client(client.handle)
            self._kernel32.CloseHandle(client.handle)
            self._kernel32.CloseHandle(client.outgoing_event)
            self._kernel32.CloseHandle(client.stop_event)
            return
        try:
            while not self._stop.is_set():
                header = self._read_exact_event(
                    client, HEADER.size, read_event, write_event
                )
                if header is None:
                    return
                _op, _session, _revision, text_size, _status = decode_header(header)
                payload = self._read_exact_event(
                    client, text_size, read_event, write_event
                )
                if payload is None:
                    return
                frame = decode_frame(header, payload)
                if frame.operation == int(Operation.HELLO):
                    client.process_id = int(frame.status)
                    client.process_name = self._safe_process_name(client.process_id)
                    _LOGGER.info(
                        "TIP connected pid=%d process=%s handle=%d clients=%d",
                        client.process_id,
                        client.process_name or "unknown",
                        client.handle,
                        self.client_count,
                    )
                elif frame.is_ack:
                    self._resolve_ack(client.handle, frame)
                else:
                    if frame.operation == int(Operation.EDIT_SESSION_WATCHDOG):
                        _LOGGER.warning(
                            "TIP edit session watchdog recovered pid=%d process=%s "
                            "session=%s revision=%d",
                            client.process_id,
                            client.process_name or "unknown",
                            frame.session_id.hex[:8],
                            frame.revision,
                        )
                    self._dispatch_event(frame)
        except Exception:
            return
        finally:
            self._remove_client(client.handle)
            self._kernel32.CancelIoEx(client.handle, None)
            self._kernel32.CloseHandle(read_event)
            self._kernel32.CloseHandle(write_event)
            self._kernel32.CloseHandle(client.handle)
            self._kernel32.CloseHandle(client.outgoing_event)
            self._kernel32.CloseHandle(client.stop_event)

    def _resolve_ack(self, client_handle: int, frame: Frame) -> None:
        key = (frame.session_id, frame.revision, frame.acknowledged_operation)
        with self._clients_lock:
            client = self._clients.get(client_handle)
            reply = BrokerReply(
                frame,
                process_id=client.process_id if client is not None else 0,
                process_name=client.process_name if client is not None else None,
            )
        with self._pending_lock:
            requests = list(self._pending.get(key, ()))
            completions: list[
                tuple[asyncio.Future[BrokerReply | None], BrokerReply | None]
            ] = []
            for request in requests:
                if client_handle not in request.client_handles:
                    continue
                if frame.status == int(Status.APPLIED):
                    request.client_handles.clear()
                    completions.append((request.future, reply))
                    continue
                request.client_handles.discard(client_handle)
                request.last_negative_ack = reply
                if not request.client_handles:
                    completions.append((request.future, request.last_negative_ack))
        if not completions or self._loop is None:
            return
        for future, result in completions:
            self._loop.call_soon_threadsafe(self._set_future_result, future, result)

    def _dispatch_event(self, frame: Frame) -> None:
        if self._loop is not None and self._event_handler is not None:
            self._loop.call_soon_threadsafe(self._event_handler, frame)

    @staticmethod
    def _set_future_result(
        future: asyncio.Future[BrokerReply | None], reply: BrokerReply | None
    ) -> None:
        if not future.done():
            future.set_result(reply)

    @staticmethod
    def _safe_process_name(process_id: int) -> str | None:
        if not process_id:
            return None
        try:
            import psutil

            name = psutil.Process(process_id).name()
        except Exception:
            return None
        return name.strip() or None

    def _remove_pending(
        self,
        key: tuple[uuid.UUID, int, int],
        request: _PendingRequest,
    ) -> None:
        with self._pending_lock:
            requests = self._pending.get(key)
            if not requests:
                return
            if request in requests:
                requests.remove(request)
            if not requests:
                self._pending.pop(key, None)

    def _fail_pending(self, error: Exception) -> None:
        with self._pending_lock:
            futures = [
                request.future for group in self._pending.values() for request in group
            ]
            self._pending.clear()
        if self._loop is None:
            return
        for future in futures:
            self._loop.call_soon_threadsafe(self._set_future_exception, future, error)

    @staticmethod
    def _set_future_exception(
        future: asyncio.Future[BrokerReply], error: Exception
    ) -> None:
        if not future.done():
            future.set_exception(error)

    def _remove_client(self, handle: int) -> None:
        with self._clients_lock:
            client = self._clients.pop(handle, None)
        if client is not None:
            self._notify_client_change()
            _LOGGER.info(
                "TIP disconnected pid=%d handle=%d",
                client.process_id,
                client.handle,
            )
            try:
                client.outgoing.put_nowait(None)
            except queue.Full:
                pass
            if client.outgoing_event:
                self._kernel32.SetEvent(client.outgoing_event)
            if client.stop_event:
                self._kernel32.SetEvent(client.stop_event)
            self._resolve_disconnect(handle)

    def _notify_client_change(self) -> None:
        if self._loop is not None and self._client_event is not None:
            self._loop.call_soon_threadsafe(self._update_client_event)

    def _update_client_event(self) -> None:
        if self._client_event is None:
            return
        if self.client_count:
            self._client_event.set()
        else:
            self._client_event.clear()

    def _resolve_disconnect(self, handle: int) -> None:
        with self._pending_lock:
            completions: list[asyncio.Future[BrokerReply | None]] = []
            for requests in self._pending.values():
                for request in requests:
                    if handle not in request.client_handles:
                        continue
                    request.client_handles.discard(handle)
                    if not request.client_handles:
                        completions.append(request.future)
        if self._loop is None:
            return
        for future in completions:
            self._loop.call_soon_threadsafe(self._set_future_result, future, None)

    def _read_exact_event(
        self,
        client: _PipeClient,
        size: int,
        read_event: int,
        write_event: int,
    ) -> bytes | None:
        result = bytearray()
        while len(result) < size:
            chunk_size = size - len(result)
            buf = ctypes.create_string_buffer(chunk_size)
            read = wintypes.DWORD()
            self._kernel32.ResetEvent(read_event)
            overlapped = OVERLAPPED()
            overlapped.hEvent = read_event
            completed = bool(
                self._kernel32.ReadFile(
                    client.handle,
                    buf,
                    chunk_size,
                    ctypes.byref(read),
                    ctypes.byref(overlapped),
                )
            )
            if not completed:
                if ctypes.get_last_error() != ERROR_IO_PENDING:
                    return None
                waits = (wintypes.HANDLE * 3)(
                    read_event, client.outgoing_event, client.stop_event
                )
                while True:
                    wait_result = self._kernel32.WaitForMultipleObjects(
                        3, waits, False, INFINITE
                    )
                    if wait_result == WAIT_OBJECT_0:
                        if not self._kernel32.GetOverlappedResult(
                            client.handle,
                            ctypes.byref(overlapped),
                            ctypes.byref(read),
                            False,
                        ):
                            return None
                        break
                    if wait_result == WAIT_OBJECT_0 + 1:
                        if not self._flush_outgoing(client, write_event):
                            self._kernel32.CancelIoEx(
                                client.handle, ctypes.byref(overlapped)
                            )
                            self._kernel32.WaitForSingleObject(read_event, INFINITE)
                            return None
                        continue
                    if wait_result == WAIT_OBJECT_0 + 2:
                        self._kernel32.CancelIoEx(
                            client.handle, ctypes.byref(overlapped)
                        )
                        self._kernel32.WaitForSingleObject(read_event, INFINITE)
                        return None
                    if wait_result == WAIT_FAILED:
                        self._kernel32.CancelIoEx(
                            client.handle, ctypes.byref(overlapped)
                        )
                        self._kernel32.WaitForSingleObject(read_event, INFINITE)
                        return None
                    self._kernel32.CancelIoEx(client.handle, ctypes.byref(overlapped))
                    self._kernel32.WaitForSingleObject(read_event, INFINITE)
                    return None
            if read.value == 0:
                return None
            result.extend(buf.raw[: read.value])
        return bytes(result)

    def _flush_outgoing(self, client: _PipeClient, write_event: int) -> bool:
        while not self._stop.is_set():
            try:
                queued = client.outgoing.get_nowait()
            except queue.Empty:
                return True
            if queued is None or not self._write_all_event(
                client, queued[1], write_event
            ):
                return False
        return False

    def _write_all_event(
        self, client: _PipeClient, data: bytes, write_event: int
    ) -> bool:
        buffer = ctypes.create_string_buffer(data)
        offset = 0
        while offset < len(data):
            written = wintypes.DWORD()
            self._kernel32.ResetEvent(write_event)
            overlapped = OVERLAPPED()
            overlapped.hEvent = write_event
            if not self._kernel32.WriteFile(
                client.handle,
                ctypes.byref(buffer, offset),
                len(data) - offset,
                ctypes.byref(written),
                ctypes.byref(overlapped),
            ):
                if ctypes.get_last_error() != ERROR_IO_PENDING:
                    return False
                waits = (wintypes.HANDLE * 2)(write_event, client.stop_event)
                wait_result = self._kernel32.WaitForMultipleObjects(
                    2, waits, False, INFINITE
                )
                if wait_result == WAIT_OBJECT_0 + 1:
                    self._kernel32.CancelIoEx(client.handle, ctypes.byref(overlapped))
                    self._kernel32.WaitForSingleObject(write_event, INFINITE)
                    return False
                if (
                    wait_result != WAIT_OBJECT_0
                    or not self._kernel32.GetOverlappedResult(
                        client.handle,
                        ctypes.byref(overlapped),
                        ctypes.byref(written),
                        False,
                    )
                ):
                    return False
            if written.value == 0:
                return False
            offset += written.value
        return True

    def _read_exact(self, handle: int, size: int) -> bytes | None:
        result = bytearray()
        while len(result) < size:
            chunk_size = size - len(result)
            buf = ctypes.create_string_buffer(chunk_size)
            read = wintypes.DWORD()
            if not self._kernel32.ReadFile(
                handle, buf, chunk_size, ctypes.byref(read), None
            ):
                return None
            if read.value == 0:
                return None
            result.extend(buf.raw[: read.value])
        return bytes(result)

    def _write_all(self, handle: int, data: bytes) -> bool:
        buffer = ctypes.create_string_buffer(data)
        offset = 0
        while offset < len(data):
            written = wintypes.DWORD()
            if not self._kernel32.WriteFile(
                handle,
                ctypes.byref(buffer, offset),
                len(data) - offset,
                ctypes.byref(written),
                None,
            ):
                return False
            if written.value == 0:
                return False
            offset += written.value
        return True
