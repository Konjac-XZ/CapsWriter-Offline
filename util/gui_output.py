import json
import sys

MARKER = "CW_GUI:"


def gui_print(text: str, color: str | None = None) -> None:
    """Emit a single-line JSON payload to stdout prefixed with a stable marker.

    Only emit the marker when stdout is not a TTY (i.e. when the process is
    being captured by a parent process such as the GUI). When running
    interactively (TTY) this function is a no-op so terminal output remains
    handled by rich.Console.print.
    """
    try:
        is_tty = getattr(sys.stdout, "isatty", lambda: False)()
        if is_tty:
            # Running in terminal: don't emit marker to avoid cluttering stdout
            return
        payload = {"text": str(text)}
        if color:
            payload["color"] = str(color)
        # Ensure a single-line emission so stdout readers that splitlines work
        line = MARKER + json.dumps(payload, ensure_ascii=False)
        print(line, flush=True)
    except Exception:
        # Don't raise from a logging helper
        try:
            print(str(text), flush=True)
        except Exception:
            pass
