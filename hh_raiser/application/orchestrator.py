from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from hh_raiser.application.activity_service import run_permitted_activities
from hh_raiser.application.vacancy_rotation import VacancyRotation
from hh_raiser.domain.policies import ActivityPolicy
from hh_raiser.domain.result import ActivityResult
from hh_raiser.infrastructure.storage.vacancy_history import VacancyHistory
from hh_raiser.reporting.activity_report import append_activity_results

if TYPE_CHECKING:
    from playwright.sync_api import Page


@dataclass
class ActivityOrchestrator:
    policy: ActivityPolicy
    report_path: Path
    rotation: VacancyRotation
    history: VacancyHistory

    def run(self, page: Page) -> list[ActivityResult]:
        results = run_permitted_activities(
            page,
            self.policy,
            self.rotation,
            self.history,
        )
        append_activity_results(self.report_path, results)
        return results
