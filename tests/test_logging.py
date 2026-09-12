from __future__ import annotations

import unittest
from io import StringIO
from pathlib import Path

from hh_raiser.logging_config import LOGGER, LogEvent, configure_logging, event_data


class LoggingTests(unittest.TestCase):
    def test_warning_colours_prefix_and_message_by_level(self) -> None:
        stream = StringIO()
        configure_logging(stream=stream, use_color=True)

        LOGGER.warning("Обычный текст сообщения")

        output = stream.getvalue()
        self.assertIn("tests/test_logging.py:", output)
        self.assertNotIn(str(Path(__file__).resolve().parents[1]), output)
        self.assertTrue(output.startswith("\033[33m"))
        self.assertIn("Обычный текст сообщения", output)
        self.assertTrue(output.endswith("\033[0m\n"))

    def test_event_category_sets_message_colour_and_structured_fields(self) -> None:
        stream = StringIO()
        configure_logging(stream=stream, use_color=True)

        LOGGER.info(
            "Отклик успешно отправлен",
            extra=event_data(LogEvent.RESPONSE_SENT, vacancy_id="123"),
        )

        output = stream.getvalue()
        prefix, message = output.split("Отклик успешно отправлен", maxsplit=1)
        self.assertIn("\033[32m", prefix)
        self.assertTrue(prefix.endswith("\033[1;38;2;253;224;71m"))
        self.assertTrue(message.startswith("\033[0m"))

    def test_plain_stream_has_no_terminal_control_sequences(self) -> None:
        stream = StringIO()
        configure_logging(stream=stream, use_color=False)

        LOGGER.info(
            "Открыта вакансия",
            extra=event_data(LogEvent.VACANCY_OPEN, vacancy_id="123"),
        )

        output = stream.getvalue()
        self.assertNotIn("\033[", output)
        self.assertIn("INFO tests/test_logging.py:", output)
        self.assertTrue(output.endswith(" Открыта вакансия\n"))

    def test_every_user_facing_event_category_colours_its_message(self) -> None:
        for event in LogEvent:
            with self.subTest(event=event):
                stream = StringIO()
                configure_logging(stream=stream, use_color=True)

                LOGGER.info("Проверяемое сообщение", extra=event_data(event))

                prefix, suffix = stream.getvalue().split("Проверяемое сообщение", maxsplit=1)
                self.assertIn("\033[", prefix)
                self.assertTrue(suffix.startswith("\033[0m"))
