import asyncio

from src.tsf_ipc.revision_coalescer import LatestTextRevisionCoalescer


def test_first_revision_is_immediate_and_slow_ack_coalesces_to_latest():
    async def exercise() -> None:
        calls: list[str] = []
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def dispatch(text: str) -> None:
            calls.append(text)
            if len(calls) == 1:
                first_started.set()
                await release_first.wait()

        stream = LatestTextRevisionCoalescer(dispatch, min_interval_s=60)
        stream.submit("首")
        await asyncio.wait_for(first_started.wait(), timeout=0.1)

        stream.submit("首个")
        stream.submit("首个快速")
        stream.submit("首个快速结果")
        release_first.set()
        await stream.flush()

        assert calls == ["首", "首个快速结果"]
        assert stream.stats.submitted == 4
        assert stream.stats.dispatched == 2
        assert stream.stats.coalesced == 2
        assert stream.is_idle
        await stream.close()

    asyncio.run(exercise())


def test_flush_waits_for_final_revision_ack():
    async def exercise() -> None:
        ack = asyncio.Event()
        completed: list[str] = []

        async def dispatch(text: str) -> None:
            await ack.wait()
            completed.append(text)

        stream = LatestTextRevisionCoalescer(dispatch)
        stream.submit("最终文本")
        flush_task = asyncio.create_task(stream.flush())
        await asyncio.sleep(0)
        assert not flush_task.done()

        ack.set()
        await flush_task
        assert completed == ["最终文本"]
        assert stream.is_idle
        await stream.close()

    asyncio.run(exercise())


def test_cancel_discards_pending_revision_and_joins_worker():
    async def exercise() -> None:
        started = asyncio.Event()

        async def dispatch(_text: str) -> None:
            started.set()
            await asyncio.Future()

        stream = LatestTextRevisionCoalescer(dispatch)
        stream.submit("在途")
        await started.wait()
        stream.submit("待发送")

        await stream.cancel()

        assert stream.is_idle
        assert stream.stats.dispatched == 0

    asyncio.run(exercise())


def test_dispatch_failure_does_not_leak_worker_or_drop_latest():
    async def exercise() -> None:
        calls: list[str] = []

        async def dispatch(text: str) -> None:
            calls.append(text)
            if len(calls) == 1:
                raise RuntimeError("simulated ACK failure")

        stream = LatestTextRevisionCoalescer(dispatch, min_interval_s=0)
        stream.submit("失败版本")
        await asyncio.sleep(0)
        stream.submit("恢复后的最终版本")
        await stream.flush()

        assert calls == ["失败版本", "恢复后的最终版本"]
        assert stream.stats.failures == 1
        assert stream.stats.dispatched == 1
        assert stream.is_idle
        await stream.close()

    asyncio.run(exercise())
