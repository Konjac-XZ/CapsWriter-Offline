import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.infra import runtime_logging


def test_log_directory_uses_local_app_data() -> None:
    path = runtime_logging.log_directory(
        {"LOCALAPPDATA": r"C:\\Users\\Ada\\AppData\\Local"}
    )

    assert (
        path == Path(r"C:\\Users\\Ada\\AppData\\Local") / "CapsWriter-Offline" / "Logs"
    )


def test_log_directory_falls_back_to_app_data() -> None:
    path = runtime_logging.log_directory(
        {"APPDATA": r"C:\\Users\\Ada\\AppData\\Roaming"}
    )

    assert (
        path
        == Path(r"C:\\Users\\Ada\\AppData\\Roaming") / "CapsWriter-Offline" / "Logs"
    )


def test_log_directory_honors_explicit_override() -> None:
    path = runtime_logging.log_directory({"CAPSWRITER_LOG_DIR": r"D:\\portable\\logs"})

    assert path == Path(r"D:\\portable\\logs")


def test_console_messages_redact_recognized_text() -> None:
    assert (
        runtime_logging._sanitize_console_message("识别结果：private dictated text")
        == "识别结果：[内容未写入诊断日志]"
    )
    assert (
        runtime_logging._sanitize_console_message("api_key=secret-value")
        == "api_key=[REDACTED]"
    )


def test_configure_runtime_logging_creates_bounded_log_file(
    monkeypatch, tmp_path: Path
) -> None:
    logger = logging.getLogger("capswriter")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    monkeypatch.setenv("CAPSWRITER_LOG_DIR", str(tmp_path / "Logs"))

    configured_logger = runtime_logging.configure_runtime_logging("test")
    configured_logger.info("test entry")

    handlers = [
        handler
        for handler in configured_logger.handlers
        if isinstance(handler, RotatingFileHandler)
    ]
    assert len(handlers) == 1
    assert handlers[0].maxBytes == runtime_logging.MAX_LOG_BYTES
    assert handlers[0].backupCount == runtime_logging.BACKUP_LOG_COUNT
    assert (
        (tmp_path / "Logs" / runtime_logging.LOG_FILE_NAME)
        .read_text(encoding="utf-8")
        .endswith("test entry\n")
    )
