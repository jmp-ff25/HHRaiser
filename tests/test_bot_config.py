from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hh_raiser.bot.config import BotConfigError, load_bot_settings


class BotConfigTests(unittest.TestCase):
    def test_loads_allow_list_and_resolves_relative_state_directory(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "hh-bot.ini"
            config_path.write_text(
                """
[telegram]
allowed_user_ids = 100, 200
log_lines = 30
summary_interval_minutes = 360
user_systemd = true

[instance:main]
name = Основное резюме
service = hhraiser@main.service
state_dir = state/main
""".strip(),
                encoding="utf-8",
            )

            settings = load_bot_settings(
                config_path,
                environment={"HHRAISER_BOT_TOKEN": "secret"},
            )

        self.assertEqual(settings.token, "secret")
        self.assertEqual(settings.allowed_user_ids, frozenset({100, 200}))
        self.assertEqual(settings.log_lines, 30)
        self.assertEqual(settings.summary_interval_minutes, 360)
        self.assertTrue(settings.user_systemd)
        self.assertEqual(settings.instances["main"].state_dir, root / "state" / "main")
        self.assertEqual(
            settings.instances["main"].config_file,
            root / "state" / "main" / "hh-config.ini",
        )

    def test_environment_user_ids_override_ini_value(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "hh-bot.ini"
            config_path.write_text(
                """
[telegram]
allowed_user_ids = 100

[instance:main]
service = hhraiser@main.service
state_dir = state
""".strip(),
                encoding="utf-8",
            )

            settings = load_bot_settings(
                config_path,
                environment={
                    "HHRAISER_BOT_TOKEN": "secret",
                    "HHRAISER_BOT_ALLOWED_USER_IDS": "300 400",
                },
            )

        self.assertEqual(settings.allowed_user_ids, frozenset({300, 400}))

    def test_rejects_missing_token(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "hh-bot.ini"
            config_path.write_text("[telegram]\nallowed_user_ids = 100", encoding="utf-8")

            with self.assertRaisesRegex(BotConfigError, "HHRAISER_BOT_TOKEN"):
                load_bot_settings(config_path, environment={})

    def test_rejects_missing_telegram_section(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "hh-bot.ini"
            config_path.write_text(
                "[instance:main]\nservice = hhraiser@main.service\nstate_dir = state",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(BotConfigError, r"\[telegram\]"):
                load_bot_settings(
                    config_path,
                    environment={"HHRAISER_BOT_TOKEN": "secret"},
                )

    def test_rejects_unsafe_service_name(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "hh-bot.ini"
            config_path.write_text(
                """
[telegram]
allowed_user_ids = 100

[instance:main]
service = hhraiser@main.service; reboot
state_dir = state
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(BotConfigError, "допустимым именем"):
                load_bot_settings(
                    config_path,
                    environment={"HHRAISER_BOT_TOKEN": "secret"},
                )

    def test_rejects_editable_config_outside_instance_state_directory(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "hh-bot.ini"
            config_path.write_text(
                """
[telegram]
allowed_user_ids = 100

[instance:main]
service = hhraiser@main.service
state_dir = state
config_file = ../private.ini
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(BotConfigError, "внутри state_dir"):
                load_bot_settings(
                    config_path,
                    environment={"HHRAISER_BOT_TOKEN": "secret"},
                )
