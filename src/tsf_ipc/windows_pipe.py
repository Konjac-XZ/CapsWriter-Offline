from __future__ import annotations

import asyncio
import ctypes
import platform
import queue
import threading
import uuid
from ctypes import wintypes
from dataclasses import dataclass, field

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


@dataclass(slots=True)
class _PipeClient:
    handle: int
    outgoing: queue.Queue[tuple[Frame, bytes] | None] = field(
        default_factory=lambda: queue.Queue(maxsize=256)
    )
    queue_lock: threading.Lock = field(default_factory=threading.Lock)


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
        self._pending: dict[
            tuple[uuid.UUID, int, int], list[asyncio.Future[Frame]]
        ] = {}
        self._pending_lock = threading.Lock()

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
            self._clients.clear()
        for client in clients:
            try:
                client.outgoing.put_nowait(None)
            except queue.Full:
                pass
            self._kernel32.CloseHandle(client.handle)
        self._fail_pending(RuntimeError("TSF named-pipe broker stopped"))

    async def request(self, frame: Frame, timeout: float) -> Frame | None:
        if self.client_count == 0:
            return None
        loop = self._loop or asyncio.get_running_loop()
        key = (frame.session_id, frame.revision, int(frame.operation))
        future: asyncio.Future[Frame] = loop.create_future()
        with self._pending_lock:
            self._pending.setdefault(key, []).append(future)
        sent = self.broadcast(frame)
        if sent == 0:
            self._remove_pending(key, future)
            return None
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._remove_pending(key, future)

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

    @staticmethod
    def _enqueue_frame(client: _PipeClient, frame: Frame, encoded: bytes) -> None:
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
        self._kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
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
        self._kernel32.PeekNamedPipe.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel32.PeekNamedPipe.restype = wintypes.BOOL
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
                        PIPE_ACCESS_DUPLEX,
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
                connected = bool(self._kernel32.ConnectNamedPipe(handle, None))
                if not connected and ctypes.get_last_error() != ERROR_PIPE_CONNECTED:
                    self._kernel32.CloseHandle(handle)
                    if self._stop.wait(retry_delay):
                        return
                    retry_delay = min(retry_delay * 2, 5.0)
                    continue
                if self._stop.is_set():
                    self._kernel32.CloseHandle(handle)
                    return
                client = _PipeClient(int(handle))
                with self._clients_lock:
                    self._clients[client.handle] = client
                threading.Thread(
                    target=self._service_client,
                    args=(client,),
                    name=f"capswriter-tsf-pipe-{client.handle}",
                    daemon=True,
                ).start()
            except Exception as exc:
                self._startup_error = exc
                self._ready.set()
                return

    def _service_client(self, client: _PipeClient) -> None:
        try:
            while not self._stop.is_set():
                try:
                    queued = client.outgoing.get_nowait()
                except queue.Empty:
                    queued = None
                else:
                    if queued is None or not self._write_all(client.handle, queued[1]):
                        return

                available = wintypes.DWORD()
                if not self._kernel32.PeekNamedPipe(
                    client.handle,
                    None,
                    0,
                    None,
                    ctypes.byref(available),
                    None,
                ):
                    return
                if available.value < HEADER.size:
                    self._stop.wait(0.01)
                    continue
                header = self._read_exact(client.handle, HEADER.size)
                if header is None:
                    return
                _op, _session, _revision, text_size, _status = decode_header(header)
                payload = self._read_exact(client.handle, text_size)
                if payload is None:
                    return
                frame = decode_frame(header, payload)
                if frame.is_ack:
                    self._resolve_ack(frame)
        except Exception:
            return
        finally:
            self._remove_client(client.handle)

    def _resolve_ack(self, frame: Frame) -> None:
        key = (frame.session_id, frame.revision, frame.acknowledged_operation)
        with self._pending_lock:
            futures = list(self._pending.get(key, ()))
        if not futures or self._loop is None:
            return
        # BEGIN is broadcast to every loaded TIP. Background processes reject it,
        # so only a positive foreground APPLIED result may claim the session.
        if frame.status != int(Status.APPLIED):
            return
        for future in futures:
            self._loop.call_soon_threadsafe(self._set_future_result, future, frame)

    @staticmethod
    def _set_future_result(future: asyncio.Future[Frame], frame: Frame) -> None:
        if not future.done():
            future.set_result(frame)

    def _remove_pending(
        self,
        key: tuple[uuid.UUID, int, int],
        future: asyncio.Future[Frame],
    ) -> None:
        with self._pending_lock:
            futures = self._pending.get(key)
            if not futures:
                return
            if future in futures:
                futures.remove(future)
            if not futures:
                self._pending.pop(key, None)

    def _fail_pending(self, error: Exception) -> None:
        with self._pending_lock:
            futures = [future for group in self._pending.values() for future in group]
            self._pending.clear()
        if self._loop is None:
            return
        for future in futures:
            self._loop.call_soon_threadsafe(self._set_future_exception, future, error)

    @staticmethod
    def _set_future_exception(future: asyncio.Future[Frame], error: Exception) -> None:
        if not future.done():
            future.set_exception(error)

    def _remove_client(self, handle: int) -> None:
        with self._clients_lock:
            client = self._clients.pop(handle, None)
        if client is not None:
            try:
                client.outgoing.put_nowait(None)
            except queue.Full:
                pass
            self._kernel32.CloseHandle(client.handle)

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
