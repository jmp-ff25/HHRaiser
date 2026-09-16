from __future__ import annotations

import argparse
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hh_raiser.config import read_file_config, resolve_runtime_settings


class VacancyGroupConfigTests(unittest.TestCase):
    def test_reads_group_size_from_ini_and_uses_it_at_runtime(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hh-config.ini"
            path.write_text(
                "[resume]\ntitle = Python\n"
                "[activity]\nsearch_queries = Python\nvacancies_per_group = 7\n",
                encoding="utf-8",
            )
            file_config = read_file_config(path)
            settings = resolve_runtime_settings(
                argparse.Namespace(
                    config_file=path,
                    resume_title=None,
                    search_query=None,
                    vacancies_per_cycle=None,
                    full_activity=True,
                )
            )

        self.assertEqual(file_config.vacancies_per_group, 7)
        self.assertEqual(settings.vacancies_per_group, 7)

    def test_group_size_defaults_to_five(self) -> None:
        settings = resolve_runtime_settings(
            argparse.Namespace(
                config_file=Path("missing.ini"),
                resume_title="Python",
                search_query=["Python"],
                vacancies_per_cycle=None,
                full_activity=True,
            )
        )

        self.assertEqual(settings.vacancies_per_group, 5)


if __name__ == "__main__":
    unittest.main()
