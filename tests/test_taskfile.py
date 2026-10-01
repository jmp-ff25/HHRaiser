from __future__ import annotations

import re
import unittest
from pathlib import Path


class TaskfileTests(unittest.TestCase):
    def test_help_lists_tasks_and_cli_help_remains_available(self) -> None:
        taskfile = (Path(__file__).parents[1] / "Taskfile.yml").read_text(encoding="utf-8")
        help_task = re.search(
            r"^  help:\n(?P<body>.*?)(?=^  \S|\Z)",
            taskfile,
            flags=re.MULTILINE | re.DOTALL,
        )
        cli_help_task = re.search(
            r"^  help-cli:\n(?P<body>.*?)(?=^  \S|\Z)",
            taskfile,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(help_task)
        self.assertIsNotNone(cli_help_task)
        self.assertIn("task --list", help_task.group("body"))
        self.assertIn("silent: true", help_task.group("body"))
        self.assertIn("hh-resume-raiser --help", cli_help_task.group("body"))

    def test_every_setup_entrypoint_prepares_ollama_model(self) -> None:
        root = Path(__file__).parents[1]
        taskfile = (root / "Taskfile.yml").read_text(encoding="utf-8")
        setup_task = re.search(
            r"^  setup:\n(?P<body>.*?)(?=^  \S|\Z)",
            taskfile,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(setup_task)
        self.assertIn("scripts/setup.ps1", setup_task.group("body"))
        self.assertIn("platforms: [windows]", setup_task.group("body"))
        self.assertIn("scripts/setup.sh", setup_task.group("body"))
        self.assertIn("platforms: [linux, darwin]", setup_task.group("body"))
        self.assertIn("  install:\n", taskfile)
        self.assertIn("- task: setup", taskfile)
        for script_name in ("setup.ps1", "setup.sh", "install-ubuntu.sh"):
            with self.subTest(script=script_name):
                script = (root / "scripts" / script_name).read_text(encoding="utf-8")
                self.assertIn("python -m hh_raiser.setup_ollama", script)

    def test_setup_creates_config_templates_without_replacing_existing_files(self) -> None:
        root = Path(__file__).parents[1]
        powershell = (root / "scripts" / "setup.ps1").read_text(encoding="utf-8")
        shell = (root / "scripts" / "setup.sh").read_text(encoding="utf-8")
        for script in (powershell, shell):
            self.assertIn(".env.example", script)
            self.assertIn("hh-config.example.ini", script)
            self.assertNotIn("hhraiser setup", script)
        self.assertIn('if (-not (Test-Path -LiteralPath "$projectDir\\.env"))', powershell)
        self.assertIn('if [ ! -f "$PROJECT_DIR/.env" ]; then', shell)

    def test_windows_setup_uses_utf8_bom_for_windows_powershell(self) -> None:
        script = (Path(__file__).parents[1] / "scripts" / "setup.ps1").read_bytes()
        self.assertTrue(script.startswith(b"\xef\xbb\xbf"))
        self.assertIn("Установка готова", script.decode("utf-8-sig"))

    def test_reset_tasks_include_preview_and_actual_cleanup(self) -> None:
        taskfile = (Path(__file__).parents[1] / "Taskfile.yml").read_text(encoding="utf-8")
        self.assertIn("  reset-data-preview:\n", taskfile)
        self.assertIn("python -m hh_raiser.reset_data --dry-run", taskfile)
        self.assertIn("  reset-data:\n", taskfile)
        self.assertIn("python -m hh_raiser.reset_data\n", taskfile)

    def test_ollama_view_task_disables_responses(self) -> None:
        taskfile = (Path(__file__).parents[1] / "Taskfile.yml").read_text(encoding="utf-8")
        task_match = re.search(
            r"^  activity-ollama-once:\n(?P<task>.*?)(?=^  \S|\Z)",
            taskfile,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(task_match)
        task = task_match.group("task")
        self.assertIn("--matching-mode semantic --no-auto-respond", task)
        self.assertNotIn("{{.RESPONSE_ARGS}}", task)

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

    def test_main_instance_uses_project_state_directory_and_shared_browser_cache(self) -> None:
        taskfile = (Path(__file__).parents[1] / "Taskfile.yml").read_text(encoding="utf-8")
        cli = (Path(__file__).parents[1] / "hh_raiser" / "cli.py").read_text(encoding="utf-8")

        self.assertIn("STATE_DIR: state/main", taskfile)
        self.assertIn('CONFIG_FILE: "{{.STATE_DIR}}/hh-config.ini"', taskfile)
        self.assertIn('PROFILE_DIR: "{{.STATE_DIR}}/browser-profile"', taskfile)
        self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH", taskfile)
        self.assertNotIn('os.environ["PLAYWRIGHT_BROWSERS_PATH"]', cli)

    def test_cdp_session_task_uses_only_local_debugging_port(self) -> None:
        taskfile = (Path(__file__).parents[1] / "Taskfile.yml").read_text(encoding="utf-8")

        task_match = re.search(
            r"^  init-session-cdp:\n(?P<task>.*?)(?=^  \S|\Z)",
            taskfile,
            flags=re.MULTILINE | re.DOTALL,
        )

        self.assertIsNotNone(task_match)
        self.assertIn("--debug-cdp-port 9222", task_match.group("task"))

    def test_live_captcha_task_uses_existing_local_cdp_session(self) -> None:
        taskfile = (Path(__file__).parents[1] / "Taskfile.yml").read_text(encoding="utf-8")

        task_match = re.search(
            r"^  login-captcha-e2e:\n(?P<task>.*?)(?=^  \S|\Z)",
            taskfile,
            flags=re.MULTILINE | re.DOTALL,
        )

        self.assertIsNotNone(task_match)
        self.assertIn('HH_LIVE_CDP_PORT: "9222"', task_match.group("task"))

    def test_resume_index_debug_task_uses_loopback_cdp(self) -> None:
        taskfile = (Path(__file__).parents[1] / "Taskfile.yml").read_text(encoding="utf-8")

        task_match = re.search(
            r"^  resume-index-e2e-cdp-ui:\n(?P<task>.*?)(?=^  \S|\Z)",
            taskfile,
            flags=re.MULTILINE | re.DOTALL,
        )

        self.assertIsNotNone(task_match)
        self.assertIn('HH_LIVE_RESUME_INDEX_CDP_PORT: "9223"', task_match.group("task"))

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

        for alias, target in (
            ("full-activity-responses", "full-activity"),
            ("full-activity-responses-ui", "full-activity-ui"),
        ):
            with self.subTest(alias=alias):
                alias_match = re.search(
                    rf"^  {re.escape(alias)}:\n(?P<body>.*?)(?=^  \S|\Z)",
                    taskfile,
                    flags=re.MULTILINE | re.DOTALL,
                )
                self.assertIsNotNone(alias_match)
                self.assertNotIn("desc:", alias_match.group("body"))
                self.assertIn(f"- task: {target}", alias_match.group("body"))

    def test_server_worker_enables_manual_telegram_captcha(self) -> None:
        service = (
            Path(__file__).parents[1] / "deploy" / "systemd" / "hhraiser@.service"
        ).read_text(encoding="utf-8")

        self.assertIn("--telegram-captcha", service)
        self.assertIn("Environment=HHRAISER_LOG_COLOR=true", service)
        self.assertIn("--config-file state/%i/hh-config.ini", service)
        self.assertIn("--profile-dir state/%i/browser-profile", service)
        self.assertIn("After=network-online.target ollama.service", service)

    def test_server_bot_uses_system_wide_systemd(self) -> None:
        service = (
            Path(__file__).parents[1] / "deploy" / "systemd" / "hhraiser-bot.service"
        ).read_text(encoding="utf-8")

        self.assertIn("hh-resume-raiser-bot --systemd-mode system", service)
        self.assertIn("Environment=HHRAISER_LOG_COLOR=true", service)
