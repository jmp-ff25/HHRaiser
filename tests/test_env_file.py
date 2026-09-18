from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hh_raiser.env_file import EnvFileError, load_env_file


class EnvFileTests(unittest.TestCase):
    def test_loads_private_values_without_overwriting_environment(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "# secrets\nHH_PHONE=+79990000000\nHH_PASSWORD='pa#ss=word'\n",
                encoding="utf-8",
            )
            environment = {"HH_PHONE": "+78880000000"}

            load_env_file(path, environment=environment)

        self.assertEqual(environment["HH_PHONE"], "+78880000000")
        self.assertEqual(environment["HH_PASSWORD"], "pa#ss=word")

    def test_rejects_malformed_assignment_without_exposing_value(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("HH_PASSWORD secret-value\n", encoding="utf-8")

            with self.assertRaisesRegex(EnvFileError, "строка 1"):
                load_env_file(path, environment={})
