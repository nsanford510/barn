"""Structured ``key=value`` logging (spec 13.1/13.2)."""

from __future__ import annotations

import logging
import sys
from typing import Any

LOGGER_NAME = "symphony"
_MAX_VALUE_LEN = 500


def _fmt_value(value: Any) -> str:
    text = "null" if value is None else str(value)
    if len(text) > _MAX_VALUE_LEN:
        text = text[:_MAX_VALUE_LEN] + "...(truncated)"
    if text == "" or any(c in ' "=' or not c.isprintable() for c in text):
        text = '"' + "".join(_escape(c) for c in text) + '"'
    return text


_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _escape(c: str) -> str:
    """Escape control characters so one log record always stays on one terminal line."""
    if c in _ESCAPES:
        return _ESCAPES[c]
    if not c.isprintable() and c != " ":
        return f"\\x{ord(c):02x}" if ord(c) < 256 else f"\\u{ord(c):04x}"
    return c


def kv(**fields: Any) -> str:
    return " ".join(f"{k}={_fmt_value(v)}" for k, v in fields.items())


class _KVLogger:
    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def _log(self, level: int, action: str, fields: dict[str, Any], /) -> None:
        if self._logger.isEnabledFor(level):
            self._logger.log(level, "%s %s", action, kv(**fields) if fields else "")

    def debug(self, action: str, /, **fields: Any) -> None:
        self._log(logging.DEBUG, action, fields)

    def info(self, action: str, /, **fields: Any) -> None:
        self._log(logging.INFO, action, fields)

    def warning(self, action: str, /, **fields: Any) -> None:
        self._log(logging.WARNING, action, fields)

    def error(self, action: str, /, **fields: Any) -> None:
        self._log(logging.ERROR, action, fields)


def get_logger(component: str) -> _KVLogger:
    return _KVLogger(logging.getLogger(f"{LOGGER_NAME}.{component}"))


class _SafeFileHandler(logging.FileHandler):
    """A failing sink warns through the remaining sinks instead of crashing (spec 13.2)."""

    _warned = False

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802
        if not self._warned:
            self._warned = True
            logging.getLogger(LOGGER_NAME).warning(
                "log_sink_failed %s", kv(sink=self.baseFilename))


def configure_logging(level: str = "INFO", log_file: str | None = None) -> None:
    root = logging.getLogger(LOGGER_NAME)
    root.handlers.clear()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.propagate = False
    fmt = logging.Formatter("%(asctime)s level=%(levelname)s component=%(name)s %(message)s")
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(fmt)
    root.addHandler(stderr)
    if log_file:
        try:
            fh = _SafeFileHandler(log_file)
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except OSError as exc:
            root.warning("log_sink_failed %s", kv(sink=log_file, reason=exc))
