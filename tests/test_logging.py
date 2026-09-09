from __future__ import annotations

import unittest
from io import StringIO
from pathlib import Path

from hh_raiser.logging_config import LOGGER, configure_logging


class LoggingTests(unittest.TestCase):
    def test_warning_colours_only_timestamp_and_level(self) -> None:
        stream = StringIO()
        configure_logging(stream=stream, use_color=True)

        LOGGER.warning("Обычный текст сообщения")

        output = stream.getvalue()
        self.assertIn("\033[33m", output)
        self.assertIn("tests/test_logging.py:", output)
        self.assertNotIn(str(Path(__file__).resolve().parents[1]), output)
        self.assertIn("\033[0m Обычный текст сообщения", output)
        self.assertNotIn("\033[33mОбычный текст сообщения", output)
