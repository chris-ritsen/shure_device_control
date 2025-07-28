import inspect
import logging
import os
import sys

from systemd import journal

_syslog_identifier = "default"


def init_logging(identifier="default"):
    global _syslog_identifier
    _syslog_identifier = identifier

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )


def log(message, **fields):
    frame = inspect.currentframe().f_back
    fields.pop("SYSLOG_IDENTIFIER", None)

    if "JOURNAL_STREAM" in os.environ:
        journal.send(
            MESSAGE=message,
            SYSLOG_IDENTIFIER=_syslog_identifier,
            PRIORITY=int(fields.pop("PRIORITY", 5)),
            CODE_FILE=frame.f_globals.get("__file__", "?"),
            CODE_LINE=frame.f_lineno,
            CODE_FUNC=frame.f_code.co_name,
            **fields,
        )
    else:
        print(f"[{_syslog_identifier}] {message}")
