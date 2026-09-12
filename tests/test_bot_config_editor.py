from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hh_raiser.bot.config_editor import (
    SETTINGS_BY_KEY,
    ConfigEditError,
    IniConfigStore,
    display_setting_value,
)


class IniConfigStoreTests(unittest.TestCase):
    def test_applies_allow_listed_change_without_removing_comments(self) -> None:
        with TemporaryDirectory() as directory:
            state_dir = Path(directory)
            config_path = state_dir / "hh-config.ini"
            config_path.write_text(
                "# Важный комментарий\n"
                "[resume]\n"
                "title = Python-разработчик\n\n"
                "[activity]\n"
                "search_queries =\n"
                "    Python разработчик\n"
                "search_pages_per_cycle = 25\n\n"
                "[responses]\n"
                "enabled = false\n"
                "daily_limit = 0\n",
                encoding="utf-8",
            )
            store = IniConfigStore(config_path, state_dir)

            change = store.prepare_change("search_queries", "Backend Python\nDjango")
            backup = store.apply(change)

            content = config_path.read_text(encoding="utf-8")
            self.assertIn("# Важный комментарий", content)
            self.assertIn("    Backend Python\n    Django", content)
            self.assertTrue(backup.is_file())
            self.assertIn("search_queries", (state_dir / "config-audit.jsonl").read_text())

    def test_rejects_unknown_setting_and_invalid_integer(self) -> None:
        with TemporaryDirectory() as directory:
            state_dir = Path(directory)
            config_path = state_dir / "hh-config.ini"
            config_path.write_text("[resume]\ntitle = Python\n", encoding="utf-8")
            store = IniConfigStore(config_path, state_dir)

            with self.assertRaisesRegex(ConfigEditError, "не разрешена"):
                store.prepare_change("password", "secret")
            with self.assertRaisesRegex(ConfigEditError, "от 0 до 100"):
                store.prepare_change("match_threshold", "101")

    def test_detects_concurrent_change_before_applying(self) -> None:
        with TemporaryDirectory() as directory:
            state_dir = Path(directory)
            config_path = state_dir / "hh-config.ini"
            config_path.write_text("[resume]\ntitle = Python\n", encoding="utf-8")
            store = IniConfigStore(config_path, state_dir)
            change = store.prepare_change("resume_title", "Backend")
            config_path.write_text("[resume]\ntitle = Data Engineer\n", encoding="utf-8")

            with self.assertRaisesRegex(ConfigEditError, "уже изменилась"):
                store.apply(change)

    def test_restore_returns_to_previous_valid_version(self) -> None:
        with TemporaryDirectory() as directory:
            state_dir = Path(directory)
            config_path = state_dir / "hh-config.ini"
            config_path.write_text(
                "[resume]\ntitle = Python\n[responses]\ndaily_limit = 0\n",
                encoding="utf-8",
            )
            store = IniConfigStore(config_path, state_dir)
            store.apply(store.prepare_change("daily_response_limit", "12"))

            restored_from = store.restore_latest()

            self.assertTrue(restored_from.is_file())
            self.assertEqual(store.read_value(SETTINGS_BY_KEY["daily_response_limit"]), "0")

    def test_zero_daily_limit_is_presented_as_unlimited(self) -> None:
        setting = SETTINGS_BY_KEY["daily_response_limit"]

        self.assertEqual(display_setting_value(setting, "0"), "без ограничения")
