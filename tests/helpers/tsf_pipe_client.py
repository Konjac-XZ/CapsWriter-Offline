from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tsf_ipc.protocol import (
    HEADER,
    Frame,
    Operation,
    Status,
    decode_frame,
    decode_header,
    encode_frame,
)
from src.tsf_ipc.context_snapshot import encode_context_snapshot
from src.tsf_ipc.windows_pipe import (
    GENERIC_READ,
    GENERIC_WRITE,
    INVALID_HANDLE_VALUE,
    OPEN_EXISTING,
    WindowsNamedPipeBroker,
)


def main(pipe_name: str) -> None:
    client_api = WindowsNamedPipeBroker(pipe_name)
    client_api._configure_winapi()
    deadline = time.monotonic() + 3.0
    while True:
        handle = client_api._kernel32.CreateFileW(
            pipe_name,
            GENERIC_READ | GENERIC_WRITE,
            0,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if handle != INVALID_HANDLE_VALUE:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("test named pipe did not appear")
        time.sleep(0.01)

    try:
        if not client_api._write_all(
            handle, encode_frame(Frame(Operation.HELLO, uuid.UUID(int=0), status=1234))
        ):
            raise OSError("failed to write HELLO")
        while True:
            header = client_api._read_exact(handle, HEADER.size)
            if header is None:
                raise EOFError("missing request header")
            _op, _session, _revision, text_size, _status = decode_header(header)
            payload = client_api._read_exact(handle, text_size)
            if payload is None:
                raise EOFError("missing request payload")
            request = decode_frame(header, payload)
            response_text = (
                encode_context_snapshot("pipe ", "", "context")
                if request.operation == Operation.QUERY_CONTEXT
                else ""
            )
            if not client_api._write_all(
                handle,
                encode_frame(
                    Frame(
                        int(Operation.ACK_FLAG) | int(request.operation),
                        request.session_id,
                        request.revision,
                        text=response_text,
                        status=Status.APPLIED,
                    )
                ),
            ):
                raise OSError("failed to write ACK")
            if request.operation == Operation.BEGIN:
                break
    finally:
        client_api._kernel32.CloseHandle(handle)


if __name__ == "__main__":
    main(sys.argv[1])
