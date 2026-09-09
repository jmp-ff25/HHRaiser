from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import ClassVar, TextIO

LOGGER = logging.getLogger("hh_resume_raiser")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ColorFormatter(logging.Formatter):
    _COLORS: ClassVar[dict[int, str]] = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[1;31m",
    }
    _RESET: ClassVar[str] = "\033[0m"

    def __init__(self, *, use_color: bool) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, self.datefmt)
        try:
            location = Path(record.pathname).resolve().relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            location = record.filename
        location = f"{location}:{record.lineno}"
        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        if record.stack_info:
            message = f"{message}\n{self.formatStack(record.stack_info)}"
        prefix = f"{timestamp} {record.levelname} {location}"
        if not self._use_color:
            return f"{prefix} {message}"
        color = self._COLORS.get(record.levelno)
        return f"{color}{prefix}{self._RESET} {message}" if color else f"{prefix} {message}"


def configure_logging(*, stream: TextIO | None = None, use_color: bool | None = None) -> None:
    target_stream = stream or sys.stderr
    color_enabled = target_stream.isatty() if use_color is None else use_color
    handler = logging.StreamHandler(target_stream)
    handler.setFormatter(ColorFormatter(use_color=color_enabled))
    LOGGER.handlers.clear()
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
