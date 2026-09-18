from __future__ import annotations

import random
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from hh_raiser.activities.vacancy_viewer import VacancyViewOutcome
from hh_raiser.application.page_group_service import run_vacancy_page_group
from hh_raiser.application.vacancy_traversal import VacancyTraversal
from hh_raiser.domain.action import ActivityKind
from hh_raiser.domain.policies import ActivityPolicy
from hh_raiser.domain.result import ActivityResult, ActivityStatus
from hh_raiser.domain.vacancy_response import VacancyResponseRecord, VacancyResponseStatus
from hh_raiser.infrastructure.storage.vacancy_history import VacancyHistory


def result(
    action: ActivityKind,
    *,
    status: ActivityStatus = ActivityStatus.SUCCESS,
    **metadata: object,
) -> ActivityResult:
    return ActivityResult(action=action, status=status, detail="ok", metadata=metadata)


class PageGroupServiceTests(unittest.TestCase):
    def test_resume_text_is_captured_before_opening_search_results(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "history.sqlite3")
            traversal = VacancyTraversal(("Python",), randomizer=random.Random(1))
            policy = ActivityPolicy(vacancies_per_cycle=1, vacancy_matching=True)
            calls: list[str] = []

            def read_resume(_page, _title):
                calls.append("resume")
                return "Python developer"

            def search(_page, _policy, **_options):
                calls.append("search")
                return result(ActivityKind.REVIEW_SEARCH), [], 1

            with (
                patch(
                    "hh_raiser.application.page_group_service.read_resume_text",
                    side_effect=read_resume,
                ),
                patch(
                    "hh_raiser.application.page_group_service.view_search_page",
                    side_effect=search,
                ),
                patch(
                    "hh_raiser.application.page_group_service.review_resume",
                    return_value=result(ActivityKind.REVIEW_RESUME),
                ),
            ):
                run_vacancy_page_group(object(), policy, traversal, history, "Python developer")

            self.assertEqual(calls[0], "resume")
            self.assertTrue(all(call == "search" for call in calls[1:]))
            self.assertEqual(traversal.resume_text, "Python developer")

    def test_known_response_is_viewed_without_analysis_or_second_response(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "history.sqlite3")
            known_url = "https://hh.ru/vacancy/1"
            new_url = "https://hh.ru/vacancy/2"
            history.record_response(
                VacancyResponseRecord.now(
                    vacancy_id="1",
                    status=VacancyResponseStatus.MANUAL_REQUIRED,
                    detail="questionnaire",
                    vacancy_title="Known",
                    company_name="Example",
                    search_query="Python",
                    match_score=80,
                )
            )
            traversal = VacancyTraversal(("Python",), randomizer=random.Random(2))
            policy = ActivityPolicy(
                vacancies_per_cycle=2,
                vacancy_matching=True,
                auto_respond=True,
                vacancy_view_seconds=0,
                scroll_pause_seconds=0,
            )
            matcher_presence: dict[str, bool] = {}

            def view_one(_page, urls, _policy, **options):
                url = urls[0]
                matcher_presence[url] = options["matcher"] is not None
                return [
                    VacancyViewOutcome(
                        url=url,
                        result=result(
                            ActivityKind.VIEW_VACANCY,
                            vacancy_title="Vacancy",
                            company_name="Example",
                            match_evaluated=options["matcher"] is not None,
                            match_score=80 if options["matcher"] is not None else None,
                            match_accepted=True if options["matcher"] is not None else None,
                        ),
                    )
                ]

            response = result(
                ActivityKind.RESPOND_VACANCY,
                vacancy_id="2",
                vacancy_title="Vacancy",
                response_status="sent",
                manual_reason=None,
            )
            with (
                patch(
                    "hh_raiser.application.page_group_service.view_search_page",
                    return_value=(
                        result(ActivityKind.REVIEW_SEARCH),
                        [known_url, new_url],
                        1,
                    ),
                ),
                patch(
                    "hh_raiser.application.page_group_service.read_resume_text",
                    return_value="Python developer",
                ),
                patch(
                    "hh_raiser.application.page_group_service.view_vacancies",
                    side_effect=view_one,
                ),
                patch(
                    "hh_raiser.application.page_group_service.respond_to_vacancy",
                    return_value=response,
                ) as respond,
                patch(
                    "hh_raiser.application.page_group_service.review_resume",
                    return_value=result(ActivityKind.REVIEW_RESUME),
                ),
            ):
                run_vacancy_page_group(
                    object(),
                    policy,
                    traversal,
                    history,
                    "Python developer",
                )

            self.assertFalse(matcher_presence[known_url])
            self.assertTrue(matcher_presence[new_url])
            respond.assert_called_once()
            self.assertEqual(
                {record.vacancy_id for record in history.response_records()},
                {"1", "2"},
            )

    def test_below_threshold_vacancy_is_viewed_but_not_sent_or_saved(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "history.sqlite3")
            url = "https://hh.ru/vacancy/7"
            traversal = VacancyTraversal(("Python",), randomizer=random.Random(1))
            policy = ActivityPolicy(
                vacancies_per_cycle=1,
                vacancy_matching=True,
                auto_respond=True,
            )
            viewed = VacancyViewOutcome(
                url=url,
                result=result(
                    ActivityKind.VIEW_VACANCY,
                    vacancy_title="Other role",
                    company_name="Example",
                    match_evaluated=True,
                    match_score=30,
                    match_accepted=False,
                    scrolls_completed=2,
                ),
            )
            with (
                patch(
                    "hh_raiser.application.page_group_service.view_search_page",
                    return_value=(result(ActivityKind.REVIEW_SEARCH), [url], 1),
                ),
                patch(
                    "hh_raiser.application.page_group_service.read_resume_text",
                    return_value="Python developer",
                ),
                patch(
                    "hh_raiser.application.page_group_service.view_vacancies",
                    return_value=[viewed],
                ) as view,
                patch("hh_raiser.application.page_group_service.respond_to_vacancy") as respond,
                patch(
                    "hh_raiser.application.page_group_service.review_resume",
                    return_value=result(ActivityKind.REVIEW_RESUME),
                ),
            ):
                run_vacancy_page_group(
                    object(),
                    policy,
                    traversal,
                    history,
                    "Python developer",
                )

            self.assertTrue(view.call_args.kwargs["view_below_threshold"])
            respond.assert_not_called()
            self.assertFalse(history.has_response_record(url))


if __name__ == "__main__":
    unittest.main()
