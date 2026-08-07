from __future__ import annotations

from src.gui.worker_output_router import WorkerOutputRouter


def test_output_notification_is_coalesced_until_gui_consumes_it() -> None:
    router = WorkerOutputRouter()
    notifications: list[None] = []
    router.output_available.connect(lambda: notifications.append(None))

    router.route_line("first")
    router.route_line("second")

    assert len(notifications) == 1
    router.consume_output_notification()
    router.notify_if_output_remains()
    assert len(notifications) == 2
    assert [line.text for line in router.take_log_lines(10)] == ["first", "second"]


def test_daily_input_count_uses_structured_event_without_log_polling() -> None:
    router = WorkerOutputRouter()
    counts: list[int] = []
    router.daily_input_count_event.connect(counts.append)

    router.route_line('CW_GUI:{"event":"daily_input_count","count":42}')

    assert counts == [42]
    assert router.take_log_lines(10) == []
