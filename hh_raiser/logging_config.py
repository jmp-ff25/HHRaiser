from __future__ import annotations

import logging
import sys
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, TextIO

from rich.console import Console
from rich.text import Text

LOGGER = logging.getLogger("hh_resume_raiser")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class LogEvent(StrEnum):
    """Stable categories used to render and later persist application events."""

    SYSTEM = "system"
    AUTH = "auth"
    BROWSER = "browser"
    MODAL = "modal"
    NETWORK = "network"
    RESUME = "resume"
    SCHEDULE = "schedule"
    SEARCH = "search"
    VACANCY_OPEN = "vacancy.open"
    VACANCY_MATCH = "vacancy.match"
    VACANCY_VIEW = "vacancy.view"
    RESPONSE_SENT = "response.sent"
    RESPONSE_MANUAL = "response.manual"
    REPORT = "report"


def event_data(event: LogEvent, **fields: object) -> dict[str, object]:
    """Build structured ``logging`` metadata without mixing colour markup into messages."""

    return {"event_kind": event.value, **fields}


class RichEventHandler(logging.Handler):
    """Colour the traditional prefix by severity and the message by event category."""

    _PREFIX_STYLES: ClassVar[dict[int, str]] = {
        logging.DEBUG: "cyan",
        logging.INFO: "green",
        logging.WARNING: "yellow",
        logging.ERROR: "red",
        logging.CRITICAL: "bold red",
    }
    _EVENT_STYLES: ClassVar[dict[str, str]] = {
        LogEvent.SYSTEM: "#f8fafc",
        LogEvent.AUTH: "bold #c084fc",
        LogEvent.BROWSER: "#60a5fa",
        LogEvent.MODAL: "bold #fb923c",
        LogEvent.NETWORK: "#67e8f9",
        LogEvent.RESUME: "#f472b6",
        LogEvent.SCHEDULE: "#38bdf8",
        LogEvent.SEARCH: "#818cf8",
        LogEvent.VACANCY_OPEN: "#22d3ee",
        LogEvent.VACANCY_MATCH: "#e879f9",
        LogEvent.VACANCY_VIEW: "#34d399",
        LogEvent.RESPONSE_SENT: "bold #fde047",
        LogEvent.RESPONSE_MANUAL: "bold #fb923c",
        LogEvent.REPORT: "#a3e635",
    }
    _LEVEL_STYLES: ClassVar[dict[int, str]] = {
        logging.DEBUG: "#67e8f9",
        logging.WARNING: "bold #facc15",
        logging.ERROR: "bold #fb7185",
        logging.CRITICAL: "bold #ffffff on #e11d48",
    }

    def __init__(self, stream: TextIO, *, use_color: bool) -> None:
        super().__init__()
        self._use_color = use_color
        self._formatter = logging.Formatter(datefmt=DATE_FORMAT)
        self._console = Console(
            file=stream,
            color_system="truecolor" if use_color else None,
            force_terminal=use_color,
            no_color=not use_color,
            highlight=False,
            markup=False,
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            timestamp = self._formatter.formatTime(record, DATE_FORMAT)
            try:
                location = Path(record.pathname).resolve().relative_to(PROJECT_ROOT).as_posix()
            except ValueError:
                location = record.filename
            message = record.getMessage()
            if record.exc_info:
                message = f"{message}\n{self._formatter.formatException(record.exc_info)}"
            if record.stack_info:
                message = f"{message}\n{self._formatter.formatStack(record.stack_info)}"

            output = Text(
                f"{timestamp} {record.levelname} {location}:{record.lineno} ",
                style=self._prefix_style(record),
            )
            output.append(message, style=self._message_style(record))
            self._console.print(output, soft_wrap=True)
        except Exception:  # noqa: BLE001 - a logging handler must never crash the application
            self.handleError(record)

    def _prefix_style(self, record: logging.LogRecord) -> str | None:
        if not self._use_color:
            return None
        return self._PREFIX_STYLES.get(record.levelno)

    def _message_style(self, record: logging.LogRecord) -> str | None:
        if not self._use_color:
            return None
        event_kind = getattr(record, "event_kind", None)
        return self._EVENT_STYLES.get(event_kind) or self._LEVEL_STYLES.get(record.levelno)


def configure_logging(*, stream: TextIO | None = None, use_color: bool | None = None) -> None:
    target_stream = stream or sys.stderr
    color_enabled = target_stream.isatty() if use_color is None else use_color
    handler = RichEventHandler(target_stream, use_color=color_enabled)
    for current_handler in LOGGER.handlers:
        current_handler.close()
    LOGGER.handlers.clear()
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False


def show_log_color_demo() -> None:
    """Print every severity and event colour without starting browser automation."""

    configure_logging(use_color=True)
    LOGGER.setLevel(logging.DEBUG)
    for level in (
        logging.DEBUG,
        logging.INFO,
        logging.WARNING,
        logging.ERROR,
        logging.CRITICAL,
    ):
        LOGGER.log(level, "Пример уровня %s", logging.getLevelName(level))
    for event in LogEvent:
        LOGGER.info(
            "Пример категории %s",
            event.value,
            extra=event_data(event),
        )
