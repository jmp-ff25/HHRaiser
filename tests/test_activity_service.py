from __future__ import annotations

import random
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from hh_raiser.activities.vacancy_viewer import VacancyViewOutcome
from hh_raiser.application.activity_service import run_permitted_activities
from hh_raiser.application.vacancy_rotation import VacancyRotation
from hh_raiser.domain.action import ActivityKind
from hh_raiser.domain.policies import ActivityPolicy
from hh_raiser.domain.result import ActivityResult, ActivityStatus
from hh_raiser.infrastructure.storage.vacancy_history import VacancyHistory


def successful_result(action: ActivityKind) -> ActivityResult:
    return ActivityResult(action=action, status=ActivityStatus.SUCCESS, detail="ok")


class ActivityServiceTests(unittest.TestCase):
    def test_collects_unique_vacancies_across_multiple_pages(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(
                Path(directory) / "history.sqlite3",
                randomizer=random.Random(1),
            )
            rotation = VacancyRotation(queries=("Python",), randomizer=random.Random(1))
            policy = ActivityPolicy(
                vacancies_per_cycle=3,
                search_pages_per_cycle=3,
                revisit_after_days=0,
            )

            def search_page(_page, _policy, *, query: str, search_page: int):
                self.assertEqual(query, "Python")
                urls = {
                    0: ["https://hh.ru/vacancy/1", "https://hh.ru/vacancy/2"],
                    1: ["https://hh.ru/vacancy/2", "https://hh.ru/vacancy/3"],
                }[search_page]
                return successful_result(ActivityKind.REVIEW_SEARCH), urls, 2

            def view_pages(_page, urls: list[str], _policy):
                return [
                    VacancyViewOutcome(
                        url=url,
                        result=successful_result(ActivityKind.VIEW_VACANCY),
                    )
                    for url in urls
                ]

            with (
                patch(
                    "hh_raiser.application.activity_service.view_search_page",
                    side_effect=search_page,
                ),
                patch(
                    "hh_raiser.application.activity_service.view_vacancies",
                    side_effect=view_pages,
                ) as view_mock,
                patch(
                    "hh_raiser.application.activity_service.review_resume",
                    return_value=successful_result(ActivityKind.REVIEW_RESUME),
                ),
            ):
                results = run_permitted_activities(
                    object(),
                    policy,
                    rotation,
                    history,
                )

            selected_urls = view_mock.call_args.args[1]
            self.assertEqual(len(selected_urls), 3)
            self.assertEqual(len(set(selected_urls)), 3)
            self.assertEqual(history.viewed_count(), 3)
            self.assertEqual(
                sum(result.action == ActivityKind.REVIEW_SEARCH for result in results),
                2,
            )

    def test_unknown_view_is_released_for_future_attempt(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "history.sqlite3")
            rotation = VacancyRotation(queries=("Python",))
            policy = ActivityPolicy(
                vacancies_per_cycle=1,
                search_pages_per_cycle=1,
                revisit_after_days=0,
            )
            url = "https://hh.ru/vacancy/1"
            unknown = ActivityResult(
                action=ActivityKind.VIEW_VACANCY,
                status=ActivityStatus.UNKNOWN,
                detail="unknown",
            )
            with (
                patch(
                    "hh_raiser.application.activity_service.view_search_page",
                    return_value=(
                        successful_result(ActivityKind.REVIEW_SEARCH),
                        [url],
                        1,
                    ),
                ),
                patch(
                    "hh_raiser.application.activity_service.view_vacancies",
                    return_value=[VacancyViewOutcome(url=url, result=unknown)],
                ),
                patch(
                    "hh_raiser.application.activity_service.review_resume",
                    return_value=successful_result(ActivityKind.REVIEW_RESUME),
                ),
            ):
                run_permitted_activities(object(), policy, rotation, history)

            self.assertEqual(
                history.reserve_unseen([url], search_query="Python", limit=1, revisit_after_days=0),
                [url],
            )

    def test_interrupted_view_releases_all_reserved_vacancies(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "history.sqlite3")
            rotation = VacancyRotation(queries=("Python",))
            policy = ActivityPolicy(
                vacancies_per_cycle=1,
                search_pages_per_cycle=1,
                revisit_after_days=0,
            )
            url = "https://hh.ru/vacancy/1"
            with (
                patch(
                    "hh_raiser.application.activity_service.view_search_page",
                    return_value=(
                        successful_result(ActivityKind.REVIEW_SEARCH),
                        [url],
                        1,
                    ),
                ),
                patch(
                    "hh_raiser.application.activity_service.view_vacancies",
                    side_effect=RuntimeError("browser closed"),
                ),
                self.assertRaisesRegex(RuntimeError, "browser closed"),
            ):
                run_permitted_activities(object(), policy, rotation, history)

            self.assertEqual(
                history.reserve_unseen([url], search_query="Python", limit=1, revisit_after_days=0),
                [url],
            )
