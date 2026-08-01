"""Application-local diagnostic logging.

Runtime files must never be created next to the executable or current working
directory.  On Windows, diagnostic logs belong in the per-user Local AppData
directory because they are machine-local and can grow over time.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType


APP_DIRECTORY_NAME = "CapsWriter-Offline"
LOG_DIRECTORY_NAME = "Logs"
LOG_FILE_NAME = "capswriter.log"
MAX_LOG_BYTES = 2 * 1024 * 1024
BACKUP_LOG_COUNT = 5

_SENSITIVE_CONSOLE_PREFIXES = (
    "识别结果：",
    "转录原文：",
    "[transcript-delta]",
)


def application_data_directory(environ: dict[str, str] | None = None) -> Path:
    """Return the per-user machine-local application directory without creating it."""
    env = os.environ if environ is None else environ
    if local_app_data := env.get("LOCALAPPDATA"):
        base_directory = Path(local_app_data)
    elif app_data := env.get("APPDATA"):
        base_directory = Path(app_data)
    elif sys.platform == "win32":
        base_directory = Path.home() / "AppData" / "Local"
    else:
        base_directory = Path.home() / ".local" / "state"
    return base_directory / APP_DIRECTORY_NAME


def log_directory(environ: dict[str, str] | None = None) -> Path:
    """Return the per-user directory for diagnostic logs without creating it.

    ``LOCALAPPDATA`` is the Windows location for non-roaming, potentially large
    runtime data.  ``CAPSWRITER_LOG_DIR`` exists for portable deployments and
    automated tests; it is intentionally an explicit opt-in override.
    """
    env = os.environ if environ is None else environ
    if override := env.get("CAPSWRITER_LOG_DIR"):
        return Path(override).expanduser()

    return application_data_directory(env) / LOG_DIRECTORY_NAME


def log_file_path(environ: dict[str, str] | None = None) -> Path:
    """Return the active log-file path without creating it."""
    return log_directory(environ) / LOG_FILE_NAME


def _sanitize_console_message(message: str) -> str:
    """Avoid persisting dictated or recognized text in diagnostic logs."""
    stripped_message = message.lstrip()
    if any(stripped_message.startswith(prefix) for prefix in _SENSITIVE_CONSOLE_PREFIXES):
        prefix, _, _ = stripped_message.partition("：")
        return f"{prefix}：[内容未写入诊断日志]"
    return re.sub(
        r"(api[_ -]?key\s*[=:]\s*)\S+",
        r"\1[REDACTED]",
        message,
        flags=re.IGNORECASE,
    )


def record_console_message(message: object, *, style: object | None = None) -> None:
    """Persist a console event without allowing logging failures to affect ASR."""
    try:
        logger = logging.getLogger("capswriter.console")
        if logger.handlers or logger.propagate:
            style_suffix = f" style={style}" if style else ""
            logger.info("%s%s", _sanitize_console_message(str(message)), style_suffix)
    except Exception:
        pass


def _log_unhandled_exception(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_traceback: TracebackType | None,
) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        return
    logging.getLogger("capswriter.unhandled").error(
        "Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback)
    )


def configure_runtime_logging(component: str) -> logging.Logger:
    """Configure bounded UTF-8 diagnostic logging for one application process.

    The operation is idempotent.  A failure to create Local AppData logs is
    deliberately non-fatal: speech input remains usable on locked-down hosts.
    """
    logger = logging.getLogger("capswriter")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        try:
            directory = log_directory()
            directory.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                directory / LOG_FILE_NAME,
                maxBytes=MAX_LOG_BYTES,
                backupCount=BACKUP_LOG_COUNT,
                encoding="utf-8",
                delay=True,
            )
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)-8s %(process)d %(name)s: %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            logger.addHandler(handler)
        except OSError:
            return logger

    if not getattr(configure_runtime_logging, "_exception_hooks_installed", False):
        previous_excepthook = sys.excepthook

        def excepthook(
            exc_type: type[BaseException], exc_value: BaseException, exc_traceback: TracebackType | None
        ) -> None:
            _log_unhandled_exception(exc_type, exc_value, exc_traceback)
            previous_excepthook(exc_type, exc_value, exc_traceback)

        sys.excepthook = excepthook

        previous_threading_excepthook = threading.excepthook

        def threading_excepthook(args: threading.ExceptHookArgs) -> None:
            _log_unhandled_exception(args.exc_type, args.exc_value, args.exc_traceback)
            previous_threading_excepthook(args)

        threading.excepthook = threading_excepthook
        configure_runtime_logging._exception_hooks_installed = True

    logger.info("Runtime logging initialized for component=%s", component)
    return logger
