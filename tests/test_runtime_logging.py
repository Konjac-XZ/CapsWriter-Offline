import logging
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


def test_console_messages_keep_recognized_text_but_redact_credentials() -> None:
    assert (
        runtime_logging._sanitize_console_message("识别结果：private dictated text")
        == "识别结果：private dictated text"
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
        if isinstance(handler, runtime_logging.ResilientRotatingFileHandler)
    ]
    assert len(handlers) == 1
    assert handlers[0].maxBytes == runtime_logging.MAX_LOG_BYTES
    assert handlers[0].backupCount == runtime_logging.BACKUP_LOG_COUNT
    assert (
        (tmp_path / "Logs" / runtime_logging.LOG_FILE_NAME)
        .read_text(encoding="utf-8")
        .endswith("test entry\n")
    )


def test_rotation_conflict_keeps_logging_to_active_file(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / runtime_logging.LOG_FILE_NAME
    handler = runtime_logging.ResilientRotatingFileHandler(
        path,
        maxBytes=1,
        backupCount=1,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        handler,
        "rotate",
        lambda _source, _destination: (_ for _ in ()).throw(
            PermissionError(32, "file is used by another process")
        ),
    )

    handler.emit(logging.makeLogRecord({"msg": "still recorded"}))
    handler.close()

    assert path.read_text(encoding="utf-8").endswith("still recorded\n")
