from __future__ import annotations

import argparse
import math
import os
import signal
import subprocess
import sys
import threading
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from hh_raiser.activities.resume_index_refresher import refresh_resume_index
from hh_raiser.application.orchestrator import ActivityOrchestrator
from hh_raiser.application.vacancy_rotation import VacancyRotation
from hh_raiser.browser import (
    NetworkCapture,
    close_context_quietly,
    is_closed_playwright_error,
    login_if_needed,
    wait_for_page_close,
    wait_for_profile_content,
)
from hh_raiser.config import DEFAULT_CONFIG_PATH, resolve_runtime_settings
from hh_raiser.domain.action import ActivityKind
from hh_raiser.domain.policies import ActivityPolicy
from hh_raiser.domain.result import ActivityResult, ActivityStatus
from hh_raiser.infrastructure.browser.playwright_browser import maximize_browser_window
from hh_raiser.infrastructure.storage.vacancy_history import VacancyHistory
from hh_raiser.logging_config import LOGGER, configure_logging
from hh_raiser.models import MOSCOW, PROFILE_URL
from hh_raiser.reporting.activity_report import append_activity_results
from hh_raiser.scheduling import format_wait_duration, seconds_until, wait_for_due_time
from hh_raiser.service import run_cycle


class BrowserClosedDuringWait(RuntimeError):
    pass


@contextmanager
def graceful_interrupt() -> Iterator[threading.Event]:
    """Turn Ctrl+C into a cooperative shutdown request."""
    requested = threading.Event()
    previous_handler = signal.getsignal(signal.SIGINT)

    def request_stop(_signum: int, _frame: object) -> None:
        requested.set()

    signal.signal(signal.SIGINT, request_stop)
    try:
        yield requested
    finally:
        signal.signal(signal.SIGINT, previous_handler)


def log_activity_results(results: list[ActivityResult]) -> None:
    search_results = [result for result in results if result.action == ActivityKind.REVIEW_SEARCH]
    vacancy_results = [result for result in results if result.action == ActivityKind.VIEW_VACANCY]
    for result in results:
        if result.action not in {ActivityKind.REVIEW_SEARCH, ActivityKind.VIEW_VACANCY}:
            LOGGER.info("Активность %s: %s — %s", result.action, result.status, result.detail)

    if search_results:
        search_counts = Counter(result.status for result in search_results)
        LOGGER.info(
            "Итоги поиска уникальных вакансий: страниц проверено — %s; "
            "успешно распознано — %s; неизвестный результат — %s; ошибки — %s.",
            len(search_results),
            search_counts[ActivityStatus.SUCCESS],
            search_counts[ActivityStatus.UNKNOWN],
            search_counts[ActivityStatus.ERROR],
        )

    if not vacancy_results:
        return
    if len(vacancy_results) == 1 and vacancy_results[0].status == ActivityStatus.SKIPPED:
        result = vacancy_results[0]
        LOGGER.info("Активность %s: %s — %s", result.action, result.status, result.detail)
        return

    counts = Counter(result.status for result in vacancy_results)
    LOGGER.info(
        "Итоги просмотра вакансий: успешно — %s; неизвестный результат — %s; "
        "ошибки — %s; пропущено — %s.",
        counts[ActivityStatus.SUCCESS],
        counts[ActivityStatus.UNKNOWN],
        counts[ActivityStatus.ERROR],
        counts[ActivityStatus.SKIPPED],
    )
    for result in vacancy_results:
        if result.status in {ActivityStatus.ERROR, ActivityStatus.UNKNOWN}:
            LOGGER.warning("Проблема при просмотре вакансии: %s — %s", result.status, result.detail)


def positive_seconds(value: str) -> int:
    seconds = int(value)
    if seconds <= 0:
        raise argparse.ArgumentTypeError("Значение должно быть больше нуля")
    return seconds


def bounded_non_negative_int(value: str, *, maximum: int) -> int:
    parsed = int(value)
    if not 0 <= parsed <= maximum:
        raise argparse.ArgumentTypeError(f"Значение должно быть от 0 до {maximum}")
    return parsed


def bounded_positive_int(value: str, *, maximum: int) -> int:
    parsed = int(value)
    if not 1 <= parsed <= maximum:
        raise argparse.ArgumentTypeError(f"Значение должно быть от 1 до {maximum}")
    return parsed


def bounded_non_negative_float(value: str, *, maximum: float) -> float:
    parsed = float(value)
    if not 0 <= parsed <= maximum:
        raise argparse.ArgumentTypeError(f"Значение должно быть от 0 до {maximum:g}")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Управляет разрешённой активностью и актуальностью личного резюме на HH.ru.")
    )
    parser.add_argument("--resume-title")
    parser.add_argument("--config-file", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--search-query",
        action="append",
        help="Поисковый запрос для вакансий; параметр можно повторить несколько раз.",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=Path(
            os.environ.get(
                "HH_PROFILE_DIR",
                Path(__file__).resolve().parents[1] / ".hh-resume-raiser" / "browser-profile",
            )
        ).expanduser(),
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--restart-browser-on-close",
        action="store_true",
        help="Явно перезапускать Chromium после закрытия окна (по умолчанию программа завершается).",
    )
    parser.add_argument("--check-login", action="store_true")
    parser.add_argument("--phone")
    parser.add_argument("--password")
    parser.add_argument("--credentials-file", type=Path)
    parser.add_argument("--install-browser", action="store_true")
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--poll-seconds", type=int, default=600)
    parser.add_argument(
        "--page-refresh-seconds",
        type=positive_seconds,
        default=300,
        help="Перезагрузить страницу, если её состояние не меняется указанное число секунд.",
    )
    parser.add_argument("--buffer-seconds", type=int, default=30)
    parser.add_argument("--minimum-cooldown-hours", type=float, default=4.0)
    parser.add_argument(
        "--full-activity",
        action="store_true",
        help="После проверки поднятия просмотреть выдачу, вакансии и структуру резюме.",
    )
    parser.add_argument(
        "--activity-interval-seconds",
        type=positive_seconds,
        default=300,
        help="Интервал между циклами просмотра вакансий (по умолчанию 300 секунд).",
    )
    parser.add_argument(
        "--resume-index-refresh",
        action="store_true",
        help="Раз в заданный интервал добавлять или удалять контрольную точку в опыте.",
    )
    parser.add_argument(
        "--resume-index-refresh-seconds",
        type=positive_seconds,
        default=1_800,
        help="Интервал смены версии резюме (по умолчанию 1800 секунд).",
    )
    parser.add_argument(
        "--vacancies-per-cycle",
        type=lambda value: bounded_non_negative_int(value, maximum=25),
        default=10,
    )
    parser.add_argument(
        "--search-pages-per-cycle",
        type=lambda value: bounded_positive_int(value, maximum=200),
        default=None,
        help="Максимум страниц выдачи, проверяемых для набора уникальных вакансий.",
    )
    parser.add_argument(
        "--unique-vacancy-limit",
        type=lambda value: bounded_non_negative_int(value, maximum=100_000),
        default=None,
        help="Размер цикла уникальных просмотров; 0 отключает лимит.",
    )
    parser.add_argument(
        "--revisit-after-days",
        type=lambda value: bounded_non_negative_int(value, maximum=3_650),
        default=None,
        help="Минимальный возраст просмотра перед повторным выбором вакансии.",
    )
    parser.add_argument(
        "--reset-on-exhaustion",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Начинать новый цикл уникальных просмотров после полного прохода страниц выдачи.",
    )
    parser.add_argument(
        "--reset-vacancy-history",
        action="store_true",
        help="Перед запуском вручную начать новый цикл уникальных просмотров.",
    )
    parser.add_argument(
        "--vacancy-matching",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Проверять соответствие вакансии резюме до содержательного просмотра.",
    )
    parser.add_argument(
        "--match-threshold",
        type=lambda value: bounded_non_negative_int(value, maximum=100),
        default=None,
        help="Минимальная оценка соответствия для просмотра вакансии, от 0 до 100.",
    )
    parser.add_argument(
        "--search-scrolls",
        type=lambda value: bounded_non_negative_int(value, maximum=20),
        default=3,
    )
    parser.add_argument(
        "--vacancy-scrolls",
        type=lambda value: bounded_non_negative_int(value, maximum=10),
        default=2,
    )
    parser.add_argument(
        "--scroll-pause-seconds",
        type=lambda value: bounded_non_negative_float(value, maximum=60),
        default=1.5,
    )
    parser.add_argument(
        "--vacancy-view-seconds",
        type=lambda value: bounded_non_negative_float(value, maximum=300),
        default=12.0,
    )
    return parser


def run_browser_context(
    playwright: object,
    args: argparse.Namespace,
    *,
    stop_requested: Callable[[], bool] | None = None,
) -> None:
    should_stop = stop_requested or (lambda: False)
    LOGGER.info("Запускаю Chromium...")
    context = playwright.chromium.launch_persistent_context(
        str(args.profile_dir),
        headless=args.headless,
        no_viewport=True,
        args=["--start-maximized"],
        timeout=30_000,
    )
    LOGGER.info("Chromium запущен; проверяю авторизацию HH.")
    page = context.pages[0] if context.pages else context.new_page()
    maximize_browser_window(context, page, headless=args.headless)
    capture = NetworkCapture()
    page.on("response", capture.observe)
    try:
        login_if_needed(page, args)
        minimum_cooldown = timedelta(hours=args.minimum_cooldown_hours)
        activity_policy = ActivityPolicy(
            vacancies_per_cycle=args.vacancies_per_cycle,
            search_pages_per_cycle=args.search_pages_per_cycle,
            unique_vacancy_limit=args.unique_vacancy_limit,
            revisit_after_days=args.revisit_after_days,
            reset_on_exhaustion=args.reset_on_exhaustion,
            search_scrolls=args.search_scrolls,
            vacancy_scrolls=args.vacancy_scrolls,
            scroll_pause_seconds=args.scroll_pause_seconds,
            vacancy_view_seconds=args.vacancy_view_seconds,
            vacancy_matching=args.vacancy_matching,
            match_threshold=args.match_threshold,
            search_filters=args.search_filters,
        )
        report_path = args.profile_dir.parent / "activity-events.jsonl"
        orchestrator = ActivityOrchestrator(
            policy=activity_policy,
            report_path=report_path,
            rotation=VacancyRotation(queries=args.search_queries),
            history=args.vacancy_history,
            resume_title=args.resume_title,
        )
        activity_enabled = args.full_activity and not args.dry_run
        next_activity_at = datetime.now(MOSCOW) if activity_enabled else None
        resume_refresh_enabled = args.resume_index_refresh and not args.dry_run
        resume_refresh_interval = timedelta(seconds=args.resume_index_refresh_seconds)
        next_resume_refresh_at = datetime.now(MOSCOW) if resume_refresh_enabled else None
        while True:
            if should_stop():
                return
            next_at = run_cycle(
                page,
                resume_title=args.resume_title,
                profile_dir=args.profile_dir,
                dry_run=args.dry_run,
                capture=capture,
                minimum_cooldown=minimum_cooldown,
                page_refresh_seconds=args.page_refresh_seconds,
            )
            if should_stop():
                return
            if args.full_activity and args.dry_run:
                LOGGER.info("--dry-run: дополнительные действия просмотра пропущены.")
            if args.resume_index_refresh and args.dry_run:
                LOGGER.info("--dry-run: обновление версии резюме пропущено.")
            elif resume_refresh_enabled and (
                next_resume_refresh_at is None or datetime.now(MOSCOW) >= next_resume_refresh_at
            ):
                refresh_result = refresh_resume_index(page, profile_dir=args.profile_dir)
                append_activity_results(report_path, [refresh_result])
                LOGGER.info(
                    "Активность %s: %s — %s",
                    refresh_result.action,
                    refresh_result.status,
                    refresh_result.detail,
                )
                next_resume_refresh_at = datetime.now(MOSCOW) + resume_refresh_interval
            elif activity_enabled and (
                next_activity_at is None or datetime.now(MOSCOW) >= next_activity_at
            ):
                activity_started_at = datetime.now(MOSCOW)
                results = orchestrator.run(page)
                log_activity_results(results)
                next_activity_at = activity_started_at + timedelta(
                    seconds=args.activity_interval_seconds
                )
            if args.once:
                return
            resume_wait_seconds = (
                seconds_until(next_at, buffer_seconds=args.buffer_seconds)
                if next_at
                else args.poll_seconds
            )
            wait_seconds = resume_wait_seconds
            if next_activity_at is not None:
                activity_wait_seconds = max(
                    0,
                    math.ceil((next_activity_at - datetime.now(MOSCOW)).total_seconds()),
                )
                wait_seconds = min(resume_wait_seconds, activity_wait_seconds)
            if next_resume_refresh_at is not None:
                refresh_wait_seconds = max(
                    0,
                    math.ceil((next_resume_refresh_at - datetime.now(MOSCOW)).total_seconds()),
                )
                wait_seconds = min(wait_seconds, refresh_wait_seconds)
            LOGGER.info(
                "Следующая проверка через %s; часы сверяются каждые %s.",
                format_wait_duration(wait_seconds),
                format_wait_duration(args.poll_seconds),
            )
            poll_intervals = [args.poll_seconds]
            if activity_enabled:
                poll_intervals.append(args.activity_interval_seconds)
            if resume_refresh_enabled:
                poll_intervals.append(args.resume_index_refresh_seconds)
            due = wait_for_due_time(
                datetime.now(MOSCOW) + timedelta(seconds=wait_seconds),
                buffer_seconds=0,
                poll_seconds=min(poll_intervals),
                wait_for_stop=lambda seconds: wait_for_page_close(
                    page,
                    seconds,
                    stop_requested=should_stop,
                ),
            )
            if not due:
                if should_stop():
                    return
                if args.restart_browser_on_close:
                    raise BrowserClosedDuringWait
                LOGGER.info("Окно браузера закрыто; программа завершена без перезапуска.")
                return
    finally:
        close_context_quietly(context)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging()
    if args.self_test:
        import unittest

        tests_dir = Path(__file__).resolve().parents[1] / "tests"
        suite = unittest.defaultTestLoader.discover(str(tests_dir))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1

    args.profile_dir = args.profile_dir.resolve()
    browser_dir = args.profile_dir.parent / "playwright-browsers"
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_dir)
    if args.install_browser:
        args.profile_dir.parent.mkdir(parents=True, exist_ok=True)
        return subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"], check=False
        ).returncode
    try:
        settings = resolve_runtime_settings(args)
    except ValueError as error:
        parser.error(str(error))
    args.resume_title = settings.resume_title
    args.search_queries = settings.search_queries
    args.search_pages_per_cycle = settings.search_pages_per_cycle
    args.unique_vacancy_limit = settings.unique_vacancy_limit
    args.revisit_after_days = settings.revisit_after_days
    args.reset_on_exhaustion = settings.reset_on_exhaustion
    args.vacancy_matching = settings.vacancy_matching
    args.match_threshold = settings.match_threshold
    args.search_filters = settings.search_filters
    args.vacancy_history = VacancyHistory(args.profile_dir.parent / "vacancy-history.sqlite3")
    if args.reset_vacancy_history:
        generation = args.vacancy_history.advance_generation()
        LOGGER.info("Вручную начат цикл уникальных просмотров № %s.", generation)
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    args.profile_dir.parent.mkdir(parents=True, exist_ok=True)
    if args.check_login:
        with (
            TemporaryDirectory(dir=args.profile_dir.parent, prefix="login-check-") as temporary_dir,
            sync_playwright() as playwright,
        ):
            context = playwright.chromium.launch_persistent_context(
                temporary_dir,
                headless=args.headless,
                no_viewport=True,
                args=["--start-maximized"],
                timeout=30_000,
            )
            page = context.pages[0] if context.pages else context.new_page()
            maximize_browser_window(context, page, headless=args.headless)
            try:
                login_if_needed(page, args)
                page.goto(PROFILE_URL, wait_until="domcontentloaded")
                wait_for_profile_content(
                    page,
                    args.resume_title,
                    page_refresh_seconds=args.page_refresh_seconds,
                )
                LOGGER.info("Авторизация подтверждена. Временный профиль проверки удалён.")
            finally:
                close_context_quietly(context)
        return 0
    args.profile_dir.mkdir(parents=True, exist_ok=True)
    with graceful_interrupt() as shutdown_requested:
        while True:
            try:
                LOGGER.info("Запускаю Playwright...")
                with sync_playwright() as playwright:
                    run_browser_context(
                        playwright,
                        args,
                        stop_requested=shutdown_requested.is_set,
                    )
                if shutdown_requested.is_set():
                    LOGGER.info("Остановлено пользователем.")
                return 0
            except KeyboardInterrupt:
                LOGGER.info("Остановлено пользователем.")
                return 0
            except BrowserClosedDuringWait:
                retry_seconds = min(max(args.poll_seconds, 1), 30)
                LOGGER.warning(
                    "Окно браузера закрыто; явный перезапуск через %s.",
                    format_wait_duration(retry_seconds),
                )
                if shutdown_requested.wait(retry_seconds):
                    LOGGER.info("Остановлено пользователем.")
                    return 0
            except PlaywrightError as error:
                if not is_closed_playwright_error(error):
                    raise
                if not args.restart_browser_on_close:
                    LOGGER.info("Окно браузера закрыто; программа завершена без перезапуска.")
                    return 0
                retry_seconds = min(max(args.poll_seconds, 1), 30)
                LOGGER.warning(
                    "Связь с браузером потеряна; перезапуск через %s.",
                    format_wait_duration(retry_seconds),
                )
                if shutdown_requested.wait(retry_seconds):
                    LOGGER.info("Остановлено пользователем.")
                    return 0
