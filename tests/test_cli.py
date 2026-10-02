from __future__ import annotations

import signal
import unittest
from argparse import Namespace
from contextlib import redirect_stderr
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from hh_raiser.cli import (
    build_parser,
    chromium_launch_args,
    graceful_interrupt,
    launch_chromium_context,
    log_activity_results,
    next_wait_seconds,
    resume_wait_delay,
)
from hh_raiser.domain.action import ActivityKind
from hh_raiser.domain.result import ActivityResult, ActivityStatus
from hh_raiser.logging_config import configure_logging


class CliTests(unittest.TestCase):
    def test_headless_browser_uses_configured_desktop_viewport(self) -> None:
        chromium = MagicMock()
        args = Namespace(
            headless=True,
            headless_viewport_width=1920,
            headless_viewport_height=1200,
            debug_cdp_port=None,
        )

        launch_chromium_context(SimpleNamespace(chromium=chromium), Path("profile"), args)

        options = chromium.launch_persistent_context.call_args.kwargs
        self.assertEqual(options["viewport"], {"width": 1920, "height": 1200})
        self.assertNotIn("no_viewport", options)

    def test_visible_browser_uses_real_window_size(self) -> None:
        chromium = MagicMock()
        args = Namespace(
            headless=False,
            headless_viewport_width=1920,
            headless_viewport_height=1200,
            debug_cdp_port=None,
        )

        launch_chromium_context(SimpleNamespace(chromium=chromium), Path("profile"), args)

        options = chromium.launch_persistent_context.call_args.kwargs
        self.assertTrue(options["no_viewport"])
        self.assertNotIn("viewport", options)

    def test_ctrl_c_requests_graceful_shutdown(self) -> None:
        with graceful_interrupt() as requested:
            signal.raise_signal(signal.SIGINT)

        self.assertTrue(requested.is_set())

    @unittest.skipUnless(hasattr(signal, "SIGTERM"), "SIGTERM is unavailable")
    def test_service_stop_requests_graceful_shutdown(self) -> None:
        with graceful_interrupt() as requested:
            signal.raise_signal(signal.SIGTERM)

        self.assertTrue(requested.is_set())

    def test_stale_raise_time_falls_back_to_normal_polling(self) -> None:
        delay = resume_wait_delay(
            datetime.now().astimezone() - timedelta(minutes=5),
            buffer_seconds=30,
            poll_seconds=600,
        )

        self.assertEqual(delay, 600)

    def test_next_wait_uses_nearest_scheduled_action(self) -> None:
        now = datetime.now().astimezone()

        wait_seconds = next_wait_seconds(
            600,
            now + timedelta(seconds=180),
            now + timedelta(seconds=300),
        )

        self.assertGreaterEqual(wait_seconds, 179)
        self.assertLessEqual(wait_seconds, 180)

    def test_profession_is_not_hardcoded_in_cli_defaults(self) -> None:
        args = build_parser().parse_args([])

        self.assertIsNone(args.resume_title)
        self.assertIsNone(args.search_query)

    def test_multiple_search_queries_are_accepted(self) -> None:
        args = build_parser().parse_args(
            ["--search-query", "Первый запрос", "--search-query", "Второй запрос"]
        )

        self.assertEqual(args.search_query, ["Первый запрос", "Второй запрос"])

    def test_group_summary_reconciles_all_viewed_vacancies_and_daily_total(self) -> None:
        stream = StringIO()
        configure_logging(stream=stream, use_color=False)
        results = [
            ActivityResult(
                action=ActivityKind.VIEW_VACANCY,
                status=ActivityStatus.SUCCESS,
                detail="Страница вакансии содержательно просмотрена.",
                metadata={
                    "group_index": 2,
                    "group_count": 10,
                    "group_size": 5,
                    **({"response_skip_reason": "unfit"} if index >= 3 else {}),
                },
            )
            for index in range(5)
        ]
        results.extend(
            ActivityResult(
                action=ActivityKind.RESPOND_VACANCY,
                status=ActivityStatus.SKIPPED,
                detail="Нужна анкета",
                metadata={"response_status": "manual_required"},
            )
            for _ in range(3)
        )

        log_activity_results(results, today_sent=7, daily_response_limit=15)

        output = stream.getvalue()
        self.assertIn("Группа 2 из 10: обработано вакансий — 5 из 5; просмотрено — 5", output)
        self.assertIn("Итог группы (5 вакансий): новый отклик подтверждён HH — 0", output)
        self.assertIn("нужна помощь кандидата (отклик не отправлен) — 3", output)
        self.assertIn("без проверки отклика — 2", output)
        self.assertIn("Без проверки отклика: модель сочла неподходящими — 2", output)
        self.assertIn("Сегодня (МСК): подтверждено новых откликов — 7 из 15", output)
        self.assertNotIn("кнопка отклика недоступна", output)
        self.assertNotIn("причина не указана", output)
        self.assertNotIn("Страница вакансии содержательно просмотрена", output)

    def test_command_line_credentials_are_accepted(self) -> None:
        args = build_parser().parse_args(["--phone", "+79990000000", "--password", "secret"])
        self.assertEqual(args.phone, "+79990000000")
        self.assertEqual(args.password, "secret")

    def test_default_browser_profile_is_next_to_project(self) -> None:
        args = build_parser().parse_args([])
        self.assertEqual(
            args.profile_dir,
            Path(__file__).resolve().parents[1] / ".hh-resume-raiser" / "browser-profile",
        )

    def test_self_test_flag_is_preserved_for_compatibility(self) -> None:
        args = build_parser().parse_args(["--self-test"])
        self.assertTrue(args.self_test)

    def test_page_refresh_timeout_can_be_configured(self) -> None:
        args = build_parser().parse_args(["--page-refresh-seconds", "120"])

        self.assertEqual(args.page_refresh_seconds, 120)

    def test_page_refresh_timeout_must_be_positive(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["--page-refresh-seconds", "0"])

    def test_legacy_mode_remains_default(self) -> None:
        args = build_parser().parse_args([])
        self.assertFalse(args.full_activity)

    def test_captcha_answer_source_is_a_short_boolean_option(self) -> None:
        self.assertTrue(
            build_parser().parse_args(["--captcha-answer-source"]).captcha_answer_source
        )
        self.assertFalse(
            build_parser().parse_args(["--no-captcha-answer-source"]).captcha_answer_source
        )

    def test_debug_cdp_port_opens_only_a_loopback_endpoint(self) -> None:
        args = build_parser().parse_args(["--debug-cdp-port", "9222"])

        self.assertEqual(args.debug_cdp_port, 9222)
        self.assertEqual(
            chromium_launch_args(debug_cdp_port=args.debug_cdp_port),
            [
                "--start-maximized",
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=9222",
            ],
        )

    def test_debug_cdp_port_rejects_privileged_port(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["--debug-cdp-port", "443"])

    def test_full_activity_options_are_parsed(self) -> None:
        args = build_parser().parse_args(
            [
                "--full-activity",
                "--activity-interval-seconds",
                "300",
                "--resume-index-refresh",
                "--resume-index-refresh-seconds",
                "1800",
                "--vacancies-per-cycle",
                "3",
                "--search-scrolls",
                "4",
                "--vacancy-scrolls",
                "3",
                "--vacancy-view-seconds",
                "8.5",
                "--search-pages-per-cycle",
                "40",
                "--no-vacancy-matching",
                "--matching-model",
                "deepseek/deepseek-v4.1-flash",
                "--auto-respond",
                "--daily-response-limit",
                "25",
                "--telegram-captcha",
            ]
        )
        self.assertTrue(args.full_activity)
        self.assertEqual(args.activity_interval_seconds, 300)
        self.assertTrue(args.resume_index_refresh)
        self.assertEqual(args.resume_index_refresh_seconds, 1800)
        self.assertEqual(args.vacancies_per_cycle, 3)
        self.assertEqual(args.search_scrolls, 4)
        self.assertEqual(args.vacancy_scrolls, 3)
        self.assertEqual(args.vacancy_view_seconds, 8.5)
        self.assertEqual(args.search_pages_per_cycle, 40)
        self.assertFalse(args.vacancy_matching)
        self.assertEqual(args.matching_model, "deepseek/deepseek-v4.1-flash")
        self.assertTrue(args.auto_respond)
        self.assertEqual(args.daily_response_limit, 25)
        self.assertTrue(args.telegram_captcha)

    def test_automatic_responses_are_opt_in(self) -> None:
        args = build_parser().parse_args([])

        self.assertIsNone(args.auto_respond)

    def test_activity_limits_are_bounded(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["--vacancies-per-cycle", "26"])

    def test_activity_interval_must_be_positive(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["--activity-interval-seconds", "0"])

    def test_activity_interval_is_resolved_from_config_by_default(self) -> None:
        args = build_parser().parse_args([])

        self.assertIsNone(args.activity_interval_seconds)

    def test_resume_refresh_is_opt_in(self) -> None:
        args = build_parser().parse_args([])

        self.assertFalse(args.resume_index_refresh)
        self.assertEqual(args.resume_index_refresh_seconds, 1800)

    def test_resume_refresh_interval_must_be_positive(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["--resume-index-refresh-seconds", "0"])

    def test_browser_restart_is_disabled_by_default(self) -> None:
        args = build_parser().parse_args([])

        self.assertFalse(args.restart_browser_on_close)

    def test_browser_restart_requires_explicit_flag(self) -> None:
        args = build_parser().parse_args(["--restart-browser-on-close"])

        self.assertTrue(args.restart_browser_on_close)
