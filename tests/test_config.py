from __future__ import annotations

import argparse
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from hh_raiser.config import (
    DEFAULT_HEADLESS_VIEWPORT_HEIGHT,
    DEFAULT_HEADLESS_VIEWPORT_WIDTH,
    DEFAULT_MATCHING_PROMPT,
    read_file_config,
    resolve_runtime_settings,
)
from hh_raiser.domain.search_filters import ExperienceLevel, SearchField


class ConfigTests(unittest.TestCase):
    def test_headless_viewport_defaults_to_desktop_size(self) -> None:
        settings = resolve_runtime_settings(
            argparse.Namespace(
                config_file=Path("missing.ini"),
                resume_title="Разработчик",
                search_query=None,
                full_activity=False,
            )
        )
        self.assertEqual(settings.headless_viewport_width, DEFAULT_HEADLESS_VIEWPORT_WIDTH)
        self.assertEqual(settings.headless_viewport_height, DEFAULT_HEADLESS_VIEWPORT_HEIGHT)

    def test_headless_viewport_can_be_set_in_ini(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hh-config.ini"
            path.write_text(
                "[resume]\ntitle = Разработчик\n"
                "[browser]\nviewport_width = 1440\nviewport_height = 900\n",
                encoding="utf-8",
            )
            settings = resolve_runtime_settings(
                argparse.Namespace(
                    config_file=path,
                    resume_title=None,
                    search_query=None,
                    full_activity=False,
                )
            )
        self.assertEqual(
            (settings.headless_viewport_width, settings.headless_viewport_height), (1440, 900)
        )

    def test_headless_viewport_rejects_compact_width(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hh-config.ini"
            path.write_text(
                "[resume]\ntitle = Разработчик\n[browser]\nviewport_width = 800\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "browser.viewport_width"):
                resolve_runtime_settings(
                    argparse.Namespace(
                        config_file=path,
                        resume_title=None,
                        search_query=None,
                        full_activity=False,
                    )
                )

    def test_multiline_matching_prompt_and_title_exclusions_are_configurable(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hh-config.ini"
            path.write_text(
                "[resume]\ntitle = Data Engineer\n"
                "[matching]\nmode = semantic\n"
                "prompt =\n    Сравни главные задачи.\n    Ответь JSON.\n"
                "excluded_titles =\n    преподаватель\n    продажи\n",
                encoding="utf-8",
            )
            settings = resolve_runtime_settings(
                argparse.Namespace(
                    config_file=path,
                    resume_title=None,
                    search_query=None,
                    full_activity=False,
                )
            )
        self.assertEqual(settings.matching_prompt, "Сравни главные задачи.\nОтветь JSON.")
        self.assertEqual(settings.matching_excluded_titles, ("преподаватель", "продажи"))

    def test_missing_prompt_uses_packaged_default_and_empty_title_list_disables_vetoes(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hh-config.ini"
            path.write_text(
                "[resume]\ntitle = Data Engineer\n[matching]\nexcluded_titles =\n",
                encoding="utf-8",
            )
            settings = resolve_runtime_settings(
                argparse.Namespace(
                    config_file=path,
                    resume_title=None,
                    search_query=None,
                    full_activity=False,
                )
            )
        self.assertEqual(settings.matching_prompt, DEFAULT_MATCHING_PROMPT)
        self.assertEqual(settings.matching_excluded_titles, ())

    def test_explicit_empty_matching_prompt_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hh-config.ini"
            path.write_text(
                "[resume]\ntitle = Data Engineer\n[matching]\nprompt =\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "matching.prompt"):
                resolve_runtime_settings(
                    argparse.Namespace(
                        config_file=path,
                        resume_title=None,
                        search_query=None,
                        full_activity=False,
                    )
                )

    def test_example_configuration_remains_parseable(self) -> None:
        example = Path(__file__).parents[1] / "hh-config.example.ini"
        config = read_file_config(example)
        self.assertEqual(config.matching_mode, "semantic")
        self.assertTrue(config.matching_prompt)

    def test_reads_resume_and_multiple_queries_from_ini(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hh-config.ini"
            path.write_text(
                "[resume]\ntitle = Аналитик\n\n"
                "[activity]\nsearch_queries =\n    Аналитик данных\n    Data analyst\n"
                "activity_interval_seconds = 600\n"
                "search_pages_per_cycle = 30\n"
                "[search_filters]\n"
                "excluded_words =\n    senior\n    аналитик\n"
                "search_fields =\n    name\n"
                "experience =\n    between1And3\n    between3And6\n"
                "areas =\n    Москва\n    Санкт-Петербург\n"
                "[matching]\nenabled = false\nthreshold = 67\n"
                "[responses]\nenabled = true\ndaily_limit = 25\n",
                encoding="utf-8",
            )

            config = read_file_config(path)

        self.assertEqual(config.resume_title, "Аналитик")
        self.assertEqual(config.search_queries, ("Аналитик данных", "Data analyst"))
        self.assertEqual(config.activity_interval_seconds, 600)
        self.assertEqual(config.search_pages_per_cycle, 30)
        self.assertFalse(config.vacancy_matching)
        self.assertEqual(config.match_threshold, 67)
        self.assertTrue(config.auto_respond)
        self.assertEqual(config.daily_response_limit, 25)
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

    def test_reads_captcha_answer_source_from_ini(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hh-config.ini"
            path.write_text(
                "[resume]\ntitle = Аналитик\n[captchasolution]\nenabled = true\n",
                encoding="utf-8",
            )
            config = read_file_config(path)

        self.assertTrue(config.captcha_answer_source)

    def test_responses_are_enabled_by_default_when_only_limit_is_configured(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hh-config.ini"
            path.write_text(
                "[resume]\ntitle = Аналитик\n"
                "[activity]\nsearch_queries = Аналитик\n"
                "[responses]\ndaily_limit = 15\n",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config_file=path,
                resume_title=None,
                search_query=None,
                full_activity=True,
            )

            settings = resolve_runtime_settings(args)

        self.assertTrue(settings.auto_respond)
        self.assertEqual(settings.daily_response_limit, 15)

    def test_reads_activity_interval_from_ini(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hh-config.ini"
            path.write_text(
                "[resume]\ntitle = Аналитик\n"
                "[activity]\nsearch_queries = Аналтик\nactivity_interval_seconds = 600\n",
                encoding="utf-8",
            )
            settings = resolve_runtime_settings(
                argparse.Namespace(
                    config_file=path,
                    resume_title=None,
                    search_query=None,
                    activity_interval_seconds=None,
                    full_activity=True,
                )
            )

        self.assertEqual(settings.activity_interval_seconds, 600)

    def test_rejects_non_positive_activity_interval_from_ini(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "hh-config.ini"
            path.write_text(
                "[resume]\ntitle = Аналитик\n"
                "[activity]\nsearch_queries = Аналитик\nactivity_interval_seconds = 0\n",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                config_file=path,
                resume_title=None,
                search_query=None,
                activity_interval_seconds=None,
                full_activity=True,
            )

            with self.assertRaisesRegex(ValueError, "activity_interval_seconds"):
                resolve_runtime_settings(args)

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
