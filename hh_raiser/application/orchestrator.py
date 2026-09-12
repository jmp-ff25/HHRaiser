from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from hh_raiser.application.activity_service import run_permitted_activities
from hh_raiser.application.vacancy_rotation import VacancyRotation
from hh_raiser.domain.policies import ActivityPolicy
from hh_raiser.domain.result import ActivityResult
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
    rotation: VacancyRotation
    history: VacancyHistory
    resume_title: str
    response_report_path: Path | None = None

    def run(self, page: Page) -> list[ActivityResult]:
        results = run_permitted_activities(
            page,
            self.policy,
            self.rotation,
            self.history,
            self.resume_title,
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
