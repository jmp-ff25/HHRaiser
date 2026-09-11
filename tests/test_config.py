from __future__ import annotations

import argparse
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from hh_raiser.config import read_file_config, resolve_runtime_settings
from hh_raiser.domain.search_filters import ExperienceLevel, SearchField


class ConfigTests(unittest.TestCase):
    def test_reads_resume_and_multiple_queries_from_ini(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hh-config.ini"
            path.write_text(
                "[resume]\ntitle = Аналитик\n\n"
                "[activity]\nsearch_queries =\n    Аналитик данных\n    Data analyst\n"
                "unique_vacancy_limit = 750\n"
                "revisit_after_days = 21\n"
                "search_pages_per_cycle = 30\n"
                "reset_on_exhaustion = false\n"
                "[search_filters]\n"
                "excluded_words =\n    senior\n    аналитик\n"
                "search_fields =\n    name\n"
                "experience =\n    between1And3\n    between3And6\n"
                "areas =\n    Москва\n    Санкт-Петербург\n"
                "[matching]\nenabled = false\nthreshold = 67\n"
                "[responses]\nenabled = true\n",
                encoding="utf-8",
            )

            config = read_file_config(path)

        self.assertEqual(config.resume_title, "Аналитик")
        self.assertEqual(config.search_queries, ("Аналитик данных", "Data analyst"))
        self.assertEqual(config.unique_vacancy_limit, 750)
        self.assertEqual(config.revisit_after_days, 21)
        self.assertEqual(config.search_pages_per_cycle, 30)
        self.assertFalse(config.reset_on_exhaustion)
        self.assertFalse(config.vacancy_matching)
        self.assertEqual(config.match_threshold, 67)
        self.assertTrue(config.auto_respond)
        self.assertEqual(config.search_filters.excluded_words, ("senior", "аналитик"))
        self.assertEqual(config.search_filters.search_fields, (SearchField.VACANCY_NAME,))
        self.assertEqual(
            config.search_filters.experience,
            (
                ExperienceLevel.BETWEEN_ONE_AND_THREE,
                ExperienceLevel.BETWEEN_THREE_AND_SIX,
            ),
        )
        self.assertEqual(config.search_filters.areas, ("Москва", "Санкт-Петербург"))

    def test_rejects_unknown_search_filter_value(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hh-config.ini"
            path.write_text(
                "[resume]\ntitle = Разработчик\n[search_filters]\nsearch_fields = everywhere\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "search_filters.search_fields"):
                read_file_config(path)

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

    def test_rejects_out_of_range_history_settings_from_ini(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hh-config.ini"
            path.write_text(
                "[resume]\ntitle = Резюме\n"
                "[activity]\nsearch_queries = Python\nrevisit_after_days = -1\n",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config_file=path,
                resume_title=None,
                search_query=None,
                full_activity=True,
            )

            with self.assertRaisesRegex(ValueError, "revisit_after_days"):
                resolve_runtime_settings(args)
