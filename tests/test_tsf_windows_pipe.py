import asyncio
import platform
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from src.tsf_ipc.protocol import Frame, Operation, Status
from src.tsf_ipc.windows_pipe import (
    PIPE_UNLIMITED_INSTANCES,
    WindowsNamedPipeBroker,
    _PipeClient,
)

pytestmark = pytest.mark.skipif(
    platform.system() != "Windows", reason="Windows named pipes are required"
)


def test_pipe_server_does_not_cap_loaded_tip_clients_at_sixteen():
    assert PIPE_UNLIMITED_INSTANCES == 255


def test_windows_pipe_request_ack_round_trip():
    pipe_name = rf"\\.\pipe\CapsWriter.TsfSpeechTip.test.{uuid.uuid4().hex}"

    async def exercise() -> None:
        broker = WindowsNamedPipeBroker(pipe_name)
        assert broker.start(asyncio.get_running_loop()) is True
        helper = Path(__file__).parent / "helpers" / "tsf_pipe_client.py"
        client = subprocess.Popen([sys.executable, str(helper), pipe_name])
        try:
            for _ in range(200):
                if broker.client_count:
                    break
                await asyncio.sleep(0.01)
            assert broker.client_count == 1
            request = Frame(Operation.BEGIN, uuid.uuid4(), 1, "管道端到端")
            response = await broker.request(request, timeout=1.0)
            assert response is not None
            assert response.session_id == request.session_id
            assert response.revision == request.revision
            assert response.status == Status.APPLIED
        finally:
            client.wait(timeout=2.0)
            broker.stop()
        assert client.returncode == 0

    asyncio.run(exercise())


def test_outgoing_queue_coalesces_unsent_full_text_revisions():
    broker = WindowsNamedPipeBroker("unused")
    client = _PipeClient(1)
    session_id = uuid.uuid4()

    for revision in range(1, 301):
        frame = Frame(Operation.REVISE, session_id, revision, f"full text {revision}")
        broker._enqueue_frame(client, frame, b"encoded")
    commit = Frame(Operation.COMMIT, session_id, 301)
    broker._enqueue_frame(client, commit, b"commit")

    first = client.outgoing.get_nowait()
    second = client.outgoing.get_nowait()
    assert first is not None and first[0].revision == 300
    assert second is not None and second[0].operation == Operation.COMMIT
    assert client.outgoing.empty()


def test_request_without_clients_returns_immediately():
    async def exercise() -> None:
        broker = WindowsNamedPipeBroker("unused")
        request = Frame(Operation.COMMIT, uuid.uuid4(), 3)
        assert await broker.request(request, timeout=1.0) is None

    asyncio.run(exercise())


def test_negative_ack_waits_for_possible_applied_ack_from_another_client():
    async def exercise() -> None:
        broker = WindowsNamedPipeBroker("unused")
        broker._loop = asyncio.get_running_loop()
        first = _PipeClient(1)
        second = _PipeClient(2)
        broker._clients = {1: first, 2: second}
        request = Frame(Operation.REVISE, uuid.uuid4(), 7, "最终文本")
        response_task = asyncio.create_task(broker.request(request, timeout=1.0))
        await asyncio.sleep(0)

        broker._resolve_ack(
            1,
            Frame(
                int(Operation.ACK_FLAG) | int(Operation.REVISE),
                request.session_id,
                request.revision,
                status=Status.INACTIVE_SESSION,
            ),
        )
        await asyncio.sleep(0)
        assert response_task.done() is False

        applied = Frame(
            int(Operation.ACK_FLAG) | int(Operation.REVISE),
            request.session_id,
            request.revision,
            status=Status.APPLIED,
        )
        broker._resolve_ack(2, applied)
        assert await response_task == applied

    asyncio.run(exercise())


def test_request_returns_negative_after_every_target_client_rejects():
    async def exercise() -> None:
        broker = WindowsNamedPipeBroker("unused")
        broker._loop = asyncio.get_running_loop()
        broker._clients = {1: _PipeClient(1), 2: _PipeClient(2)}
        request = Frame(Operation.CANCEL, uuid.uuid4(), 9)
        response_task = asyncio.create_task(broker.request(request, timeout=1.0))
        await asyncio.sleep(0)

        for handle, status in (
            (1, Status.INACTIVE_SESSION),
            (2, Status.EDIT_SESSION_FAILED),
        ):
            broker._resolve_ack(
                handle,
                Frame(
                    int(Operation.ACK_FLAG) | int(Operation.CANCEL),
                    request.session_id,
                    request.revision,
                    status=status,
                ),
            )
        response = await response_task
        assert response is not None
        assert response.status == Status.EDIT_SESSION_FAILED

    asyncio.run(exercise())


def test_request_returns_none_when_all_target_clients_disconnect():
    async def exercise() -> None:
        broker = WindowsNamedPipeBroker("unused")
        broker._loop = asyncio.get_running_loop()
        broker._clients = {1: _PipeClient(1)}
        request = Frame(Operation.COMMIT, uuid.uuid4(), 4)
        response_task = asyncio.create_task(broker.request(request, timeout=1.0))
        await asyncio.sleep(0)
        broker._resolve_disconnect(1)
        assert await response_task is None

    asyncio.run(exercise())


def test_request_times_out_when_connected_client_does_not_ack():
    async def exercise() -> None:
        broker = WindowsNamedPipeBroker("unused")
        broker._loop = asyncio.get_running_loop()
        broker._clients = {1: _PipeClient(1)}
        request = Frame(Operation.REVISE, uuid.uuid4(), 2, "等待确认")
        assert await broker.request(request, timeout=0.01) is None

    asyncio.run(exercise())
