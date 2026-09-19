from __future__ import annotations

import re
import unittest
from pathlib import Path


class TaskfileTests(unittest.TestCase):
    def test_launch_profiles_do_not_override_configurable_numbers(self) -> None:
        taskfile = (Path(__file__).parents[1] / "Taskfile.yml").read_text(encoding="utf-8")

        for option in (
            "--match-threshold",
            "--activity-interval-seconds",
            "--resume-index-refresh-seconds",
        ):
            with self.subTest(option=option):
                self.assertNotIn(option, taskfile)

    def test_telegram_bot_has_cross_platform_task(self) -> None:
        taskfile = (Path(__file__).parents[1] / "Taskfile.yml").read_text(encoding="utf-8")

        self.assertIn("hh-resume-raiser-bot", taskfile)
        self.assertNotIn("BOT_CONFIG_FILE", taskfile)

    def test_main_instance_uses_project_state_directory(self) -> None:
        taskfile = (Path(__file__).parents[1] / "Taskfile.yml").read_text(encoding="utf-8")

        self.assertIn("STATE_DIR: state/main", taskfile)
        self.assertIn('CONFIG_FILE: "{{.STATE_DIR}}/hh-config.ini"', taskfile)
        self.assertIn('PROFILE_DIR: "{{.STATE_DIR}}/browser-profile"', taskfile)
        self.assertIn('PLAYWRIGHT_BROWSERS_PATH: "{{.TASKFILE_DIR}}/{{.STATE_DIR}}/', taskfile)

    def test_full_activity_profiles_enable_safe_responses(self) -> None:
        taskfile = (Path(__file__).parents[1] / "Taskfile.yml").read_text(encoding="utf-8")

        for task_name in ("full-activity", "full-activity-ui"):
            with self.subTest(task_name=task_name):
                profile_match = re.search(
                    rf"^  {re.escape(task_name)}:\n(?P<profile>.*?)(?=^  \S|\Z)",
                    taskfile,
                    flags=re.MULTILINE | re.DOTALL,
                )
                self.assertIsNotNone(profile_match)
                profile = profile_match.group("profile")
                self.assertIn("{{.RESPONSE_ARGS}}", profile)

    def test_server_worker_enables_manual_telegram_captcha(self) -> None:
        service = (
            Path(__file__).parents[1] / "deploy" / "systemd" / "hhraiser@.service"
        ).read_text(encoding="utf-8")

        self.assertIn("--telegram-captcha", service)
        self.assertIn("Environment=HHRAISER_LOG_COLOR=true", service)
        self.assertIn("--config-file state/%i/hh-config.ini", service)
        self.assertIn("--profile-dir state/%i/browser-profile", service)

    def test_server_bot_uses_system_wide_systemd(self) -> None:
        service = (
            Path(__file__).parents[1] / "deploy" / "systemd" / "hhraiser-bot.service"
        ).read_text(encoding="utf-8")

        self.assertIn("hh-resume-raiser-bot --systemd-mode system", service)
        self.assertIn("Environment=HHRAISER_LOG_COLOR=true", service)
