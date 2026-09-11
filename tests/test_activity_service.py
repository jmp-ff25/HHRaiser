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
    def test_manual_response_requirement_does_not_stop_next_vacancy(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "history.sqlite3")
            rotation = VacancyRotation(queries=("Python",), randomizer=random.Random(1))
            policy = ActivityPolicy(
                vacancies_per_cycle=2,
                search_pages_per_cycle=1,
                revisit_after_days=0,
                vacancy_matching=False,
                auto_respond=True,
            )
            urls = ["https://hh.ru/vacancy/41", "https://hh.ru/vacancy/42"]
            views = [
                VacancyViewOutcome(
                    url=url,
                    result=ActivityResult(
                        action=ActivityKind.VIEW_VACANCY,
                        status=ActivityStatus.SUCCESS,
                        detail="viewed",
                        metadata={
                            "vacancy_title": f"Vacancy {index}",
                            "company_name": "Example",
                        },
                    ),
                )
                for index, url in enumerate(urls, start=1)
            ]
            responses = [
                ActivityResult(
                    action=ActivityKind.RESPOND_VACANCY,
                    status=ActivityStatus.SKIPPED,
                    detail="questionnaire",
                    metadata={
                        "vacancy_id": "41",
                        "vacancy_title": "Vacancy 1",
                        "response_status": "manual_required",
                        "manual_reason": "questionnaire",
                    },
                ),
                ActivityResult(
                    action=ActivityKind.RESPOND_VACANCY,
                    status=ActivityStatus.SUCCESS,
                    detail="sent",
                    metadata={
                        "vacancy_id": "42",
                        "vacancy_title": "Vacancy 2",
                        "response_status": "sent",
                        "manual_reason": None,
                    },
                ),
            ]
            with (
                patch(
                    "hh_raiser.application.activity_service.view_search_page",
                    return_value=(successful_result(ActivityKind.REVIEW_SEARCH), urls, 1),
                ),
                patch(
                    "hh_raiser.application.activity_service.view_vacancies",
                    return_value=views,
                ),
                patch(
                    "hh_raiser.application.activity_service.respond_to_vacancy",
                    side_effect=responses,
                ) as respond_mock,
                patch(
                    "hh_raiser.application.activity_service.review_resume",
                    return_value=successful_result(ActivityKind.REVIEW_RESUME),
                ),
            ):
                run_permitted_activities(object(), policy, rotation, history, "Python-разработчик")

            self.assertEqual(respond_mock.call_count, 2)
            self.assertEqual(
                {record.status.value for record in history.response_records()},
                {"manual_required", "sent"},
            )

    def test_successful_view_can_create_one_persisted_response_outcome(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "history.sqlite3")
            rotation = VacancyRotation(queries=("Python",), randomizer=random.Random(1))
            policy = ActivityPolicy(
                vacancies_per_cycle=1,
                search_pages_per_cycle=1,
                revisit_after_days=0,
                vacancy_matching=False,
                auto_respond=True,
            )
            url = "https://hh.ru/vacancy/42"
            viewed = ActivityResult(
                action=ActivityKind.VIEW_VACANCY,
                status=ActivityStatus.SUCCESS,
                detail="viewed",
                metadata={
                    "vacancy_title": "Python developer",
                    "company_name": "Example",
                    "match_score": 81,
                },
            )
            response = ActivityResult(
                action=ActivityKind.RESPOND_VACANCY,
                status=ActivityStatus.SUCCESS,
                detail="sent",
                metadata={
                    "vacancy_id": "42",
                    "vacancy_title": "Python developer",
                    "response_status": "sent",
                    "manual_reason": None,
                },
            )
            with (
                patch(
                    "hh_raiser.application.activity_service.view_search_page",
                    return_value=(successful_result(ActivityKind.REVIEW_SEARCH), [url], 1),
                ),
                patch(
                    "hh_raiser.application.activity_service.view_vacancies",
                    return_value=[VacancyViewOutcome(url=url, result=viewed)],
                ),
                patch(
                    "hh_raiser.application.activity_service.respond_to_vacancy",
                    return_value=response,
                ) as respond_mock,
                patch(
                    "hh_raiser.application.activity_service.review_resume",
                    return_value=successful_result(ActivityKind.REVIEW_RESUME),
                ),
            ):
                results = run_permitted_activities(
                    object(), policy, rotation, history, "Python-разработчик"
                )

            respond_mock.assert_called_once()
            self.assertTrue(history.has_response_record(url))
            self.assertEqual(history.response_records()[0].status.value, "sent")
            self.assertTrue(
                any(result.action == ActivityKind.RESPOND_VACANCY for result in results)
            )

    def test_rejected_vacancy_is_evaluated_but_not_viewed(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "history.sqlite3")
            rotation = VacancyRotation(queries=("Backend",), randomizer=random.Random(1))
            policy = ActivityPolicy(
                vacancies_per_cycle=1,
                search_pages_per_cycle=1,
                revisit_after_days=0,
                vacancy_matching=True,
            )
            url = "https://hh.ru/vacancy/77"
            rejected = ActivityResult(
                action=ActivityKind.VIEW_VACANCY,
                status=ActivityStatus.SKIPPED,
                detail="rejected",
                metadata={
                    "match_evaluated": True,
                    "match_score": 18,
                    "match_accepted": False,
                },
            )
            with (
                patch(
                    "hh_raiser.application.activity_service.read_resume_text",
                    return_value="Backend developer Python",
                ),
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
                    return_value=[VacancyViewOutcome(url=url, result=rejected)],
                ),
                patch(
                    "hh_raiser.application.activity_service.review_resume",
                    return_value=successful_result(ActivityKind.REVIEW_RESUME),
                ),
            ):
                run_permitted_activities(object(), policy, rotation, history, "Backend developer")

            self.assertEqual(history.viewed_count(), 0)
            self.assertEqual(
                history.reserve_unseen(
                    [url],
                    search_query="Backend",
                    limit=1,
                    revisit_after_days=0,
                ),
                [],
            )

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
                vacancy_matching=False,
            )

            def search_page(_page, _policy, *, query: str, search_page: int):
                self.assertEqual(query, "Python")
                urls = {
                    0: ["https://hh.ru/vacancy/1", "https://hh.ru/vacancy/2"],
                    1: ["https://hh.ru/vacancy/2", "https://hh.ru/vacancy/3"],
                }[search_page]
                return successful_result(ActivityKind.REVIEW_SEARCH), urls, 2

            def view_pages(_page, urls: list[str], _policy, *, matcher=None):
                self.assertIsNone(matcher)
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
                    "Python-разработчик",
                )

            selected_urls = [url for call in view_mock.call_args_list for url in call.args[1]]
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
                vacancy_matching=False,
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
                run_permitted_activities(object(), policy, rotation, history, "Python-разработчик")

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
                vacancy_matching=False,
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
                run_permitted_activities(object(), policy, rotation, history, "Python-разработчик")

            self.assertEqual(
                history.reserve_unseen([url], search_query="Python", limit=1, revisit_after_days=0),
                [url],
            )
