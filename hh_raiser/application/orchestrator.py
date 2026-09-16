from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from hh_raiser.application.page_group_service import run_vacancy_page_group
from hh_raiser.application.vacancy_traversal import VacancyTraversal
from hh_raiser.domain.policies import ActivityPolicy
from hh_raiser.domain.result import ActivityResult
from hh_raiser.infrastructure.browser.captcha_guard import CaptchaResolver
from hh_raiser.infrastructure.storage.vacancy_history import VacancyHistory
from hh_raiser.logging_config import LOGGER, LogEvent, event_data
from hh_raiser.reporting.activity_report import append_activity_results
from hh_raiser.reporting.response_report import ResponseReportError, export_response_workbook

if TYPE_CHECKING:
    from playwright.sync_api import Page


@dataclass
class ActivityOrchestrator:
    policy: ActivityPolicy
    report_path: Path
    history: VacancyHistory
    resume_title: str
    traversal: VacancyTraversal
    response_report_path: Path | None = None
    captcha_guard: CaptchaResolver | None = None
    stop_requested: Callable[[], bool] | None = None

    def run(self, page: Page) -> list[ActivityResult]:
        common_options = {
            "captcha_guard": self.captcha_guard,
            "stop_requested": self.stop_requested,
        }
        results = run_vacancy_page_group(
            page,
            self.policy,
            self.traversal,
            self.history,
            self.resume_title,
            **common_options,
        )
        append_activity_results(self.report_path, results)
        if self.response_report_path is not None:
            try:
                export_response_workbook(
                    self.response_report_path,
                    self.history.response_records(),
                )
                LOGGER.info(
                    "Excel-журнал откликов обновлён: %s",
                    self.response_report_path,
                    extra=event_data(LogEvent.REPORT),
                )
            except ResponseReportError:
                LOGGER.warning(
                    "Excel-журнал откликов сейчас недоступен для записи. Закройте его "
                    "в Excel; SQLite и JSONL уже сохранили результат.",
                    extra=event_data(LogEvent.REPORT),
                )
        return results
