from __future__ import annotations

import argparse
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from hh_raiser.config import read_file_config, resolve_runtime_settings


class ConfigTests(unittest.TestCase):
    def test_reads_resume_and_multiple_queries_from_ini(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hh-config.ini"
            path.write_text(
                "[resume]\ntitle = Аналитик\n\n"
                "[activity]\nsearch_queries =\n    Аналитик данных\n    Data analyst\n",
                encoding="utf-8",
            )

            config = read_file_config(path)

        self.assertEqual(config.resume_title, "Аналитик")
        self.assertEqual(config.search_queries, ("Аналитик данных", "Data analyst"))

    def test_cli_values_override_file_config(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hh-config.ini"
            path.write_text(
                "[resume]\ntitle = Из файла\n[activity]\nsearch_queries = Из файла\n",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config_file=path,
                resume_title="Из CLI",
                search_query=["Первый", "Второй"],
                full_activity=True,
            )

            settings = resolve_runtime_settings(args)

        self.assertEqual(settings.resume_title, "Из CLI")
        self.assertEqual(settings.search_queries, ("Первый", "Второй"))

    def test_environment_queries_are_supported(self) -> None:
        args = argparse.Namespace(
            config_file=Path("missing.ini"),
            resume_title="Резюме",
            search_query=None,
            full_activity=True,
        )
        with patch.dict("os.environ", {"HH_SEARCH_QUERIES": "Первый|Второй"}, clear=False):
            settings = resolve_runtime_settings(args)

        self.assertEqual(settings.search_queries, ("Первый", "Второй"))

    def test_full_activity_requires_at_least_one_query(self) -> None:
        args = argparse.Namespace(
            config_file=Path("missing.ini"),
            resume_title="Резюме",
            search_query=None,
            full_activity=True,
        )
        with (
            patch.dict("os.environ", {}, clear=True),
            self.assertRaisesRegex(ValueError, "search_queries"),
        ):
            resolve_runtime_settings(args)
