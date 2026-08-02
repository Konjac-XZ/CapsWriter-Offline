import asyncio
import platform
from pathlib import Path
import subprocess
import sys
import uuid

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
