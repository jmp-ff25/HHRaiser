from __future__ import annotations

import os
import random
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from hh_raiser.activities.vacancy_viewer import VacancyViewOutcome
from hh_raiser.application.page_group_service import (
    _response_outcome_message,
    run_vacancy_page_group,
)
from hh_raiser.application.polza_vacancy_matcher import resume_fingerprint
from hh_raiser.application.vacancy_traversal import VacancyTraversal
from hh_raiser.bot.statistics import read_instance_statistics
from hh_raiser.domain.action import ActivityKind
from hh_raiser.domain.matching import ModelDecision
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
    def setUp(self) -> None:
        key = patch.dict(os.environ, {"HHRAISER_MATCHING_API_KEY": "test-key"})
        key.start()
        self.addCleanup(key.stop)

    def test_cached_model_decision_is_viewed_without_api_or_response(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "history.sqlite3")
            traversal = VacancyTraversal(("Python",), randomizer=random.Random(1))
            traversal.resume_text = "Python backend, FastAPI и PostgreSQL"
            policy = ActivityPolicy(
                vacancies_per_cycle=1,
                vacancy_matching=True,
                auto_respond=True,
            )
            url = "https://hh.ru/vacancy/137803190"
            history.record_model_evaluation(
                url,
                resume_fingerprint=resume_fingerprint("Python-разработчик", traversal.resume_text),
                model="deepseek/deepseek-v4.1-flash",
                decision=ModelDecision("fit", "Подходит", ()),
            )

            def assess(_page, _urls, _policy, **options):
                self.assertIsNone(options["matcher"])
                return [
                    VacancyViewOutcome(
                        url=url,
                        result=result(
                            ActivityKind.VIEW_VACANCY,
                            vacancy_title="Инженер-разработчик SDR / RF / DSP",
                            scrolls_completed=2,
                        ),
                    )
                ]

            with (
                patch(
                    "hh_raiser.application.page_group_service.view_search_page",
                    return_value=(result(ActivityKind.REVIEW_SEARCH), [url], 1),
                ),
                patch(
                    "hh_raiser.application.page_group_service.view_vacancies", side_effect=assess
                ),
                patch("hh_raiser.application.page_group_service.respond_to_vacancy") as respond,
                patch(
                    "hh_raiser.application.page_group_service.review_resume",
                    return_value=result(ActivityKind.REVIEW_RESUME),
                ),
            ):
                run_vacancy_page_group(object(), policy, traversal, history, "Python-разработчик")
            respond.assert_not_called()
            self.assertEqual(history.sent_response_count_today(), 0)

    def test_view_only_mode_never_enters_response_checks(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "history.sqlite3")
            traversal = VacancyTraversal(("Python",), randomizer=random.Random(1))
            policy = ActivityPolicy(
                vacancies_per_cycle=1,
                vacancy_matching=True,
                auto_respond=False,
            )
            url = "https://hh.ru/vacancy/123"
            outcome = VacancyViewOutcome(
                url=url,
                result=result(
                    ActivityKind.VIEW_VACANCY,
                    vacancy_title="Python backend",
                    match_evaluated=True,
                    match_score=30,
                    match_accepted=False,
                    semantic_mode="semantic",
                    semantic_verdict="unfit",
                ),
            )
            with (
                patch(
                    "hh_raiser.application.page_group_service.view_search_page",
                    return_value=(result(ActivityKind.REVIEW_SEARCH), [url], 1),
                ),
                patch(
                    "hh_raiser.application.page_group_service.read_resume_text",
                    return_value="Python backend",
                ),
                patch(
                    "hh_raiser.application.page_group_service.view_vacancies",
                    return_value=[outcome],
                ),
                patch("hh_raiser.application.page_group_service.respond_to_vacancy") as respond,
                patch(
                    "hh_raiser.application.page_group_service.review_resume",
                    return_value=result(ActivityKind.REVIEW_RESUME),
                ),
                self.assertLogs("hh_resume_raiser", level="INFO") as captured,
            ):
                run_vacancy_page_group(object(), policy, traversal, history, "Python backend")
            respond.assert_not_called()
            self.assertNotIn("Отклик на вакансию", "\n".join(captured.output))

    def test_fullstack_vacancy_is_viewed_but_never_auto_responded(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "history.sqlite3")
            traversal = VacancyTraversal(("Python",), randomizer=random.Random(1))
            policy = ActivityPolicy(
                vacancies_per_cycle=1, vacancy_matching=False, auto_respond=True
            )
            url = "https://hh.ru/vacancy/123"
            outcome = VacancyViewOutcome(
                url=url,
                result=result(
                    ActivityKind.VIEW_VACANCY,
                    vacancy_title="Fullstack-разработчик (AI-Enhanced)",
                ),
            )
            with (
                patch(
                    "hh_raiser.application.page_group_service.view_search_page",
                    return_value=(result(ActivityKind.REVIEW_SEARCH), [url], 1),
                ),
                patch(
                    "hh_raiser.application.page_group_service.view_vacancies",
                    return_value=[outcome],
                ),
                patch(
                    "hh_raiser.application.page_group_service.respond_to_vacancy",
                ) as respond,
                patch(
                    "hh_raiser.application.page_group_service.review_resume",
                    return_value=result(ActivityKind.REVIEW_RESUME),
                ),
            ):
                results = run_vacancy_page_group(
                    object(), policy, traversal, history, "Python backend"
                )
            respond.assert_not_called()
            self.assertTrue(any(item.action == ActivityKind.VIEW_VACANCY for item in results))

    def test_model_verdict_controls_response(self) -> None:
        for verdict, should_respond in (("fit", True), ("unsure", False)):
            with self.subTest(verdict=verdict), TemporaryDirectory() as directory:
                history = VacancyHistory(Path(directory) / "vacancy-history.sqlite3")
                traversal = VacancyTraversal(("Python",), randomizer=random.Random(1))
                policy = ActivityPolicy(
                    vacancies_per_cycle=1,
                    vacancy_matching=True,
                    auto_respond=True,
                )
                url = "https://hh.ru/vacancy/123"
                outcome = VacancyViewOutcome(
                    url=url,
                    result=result(
                        ActivityKind.VIEW_VACANCY,
                        vacancy_title="RAG developer",
                        match_evaluated=True,
                        match_score=20,
                        match_accepted=should_respond,
                        semantic_mode="semantic",
                        semantic_verdict=verdict,
                    ),
                )
                response = result(
                    ActivityKind.RESPOND_VACANCY,
                    vacancy_id="123",
                    vacancy_title="RAG developer",
                    response_status="sent",
                )
                with (
                    patch(
                        "hh_raiser.application.page_group_service.view_search_page",
                        return_value=(result(ActivityKind.REVIEW_SEARCH), [url], 1),
                    ),
                    patch(
                        "hh_raiser.application.page_group_service.read_resume_text",
                        return_value="Python FastAPI backend",
                    ),
                    patch(
                        "hh_raiser.application.page_group_service.view_vacancies",
                        return_value=[outcome],
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
                    run_vacancy_page_group(object(), policy, traversal, history, "Python backend")
                self.assertEqual(respond.called, should_respond)
                self.assertEqual(history.sent_response_count_today(), int(should_respond))

    def test_groups_every_vacancy_even_when_history_already_has_views(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "history.sqlite3")
            urls = [f"https://hh.ru/vacancy/{index}" for index in range(1, 11)]
            for url in urls:
                history.reserve_unseen([url], search_query="Python", limit=1, revisit_after_days=0)
                history.mark_viewed(url)
            traversal = VacancyTraversal(("Python",), randomizer=random.Random(1))
            policy = ActivityPolicy(
                vacancies_per_cycle=5,
                auto_respond=False,
                vacancy_matching=False,
            )

            def view_one(_page, selected_urls, _policy, **_options):
                url = selected_urls[0]
                return [
                    VacancyViewOutcome(
                        url=url,
                        result=result(ActivityKind.VIEW_VACANCY, vacancy_title="Python developer"),
                    )
                ]

            with (
                patch(
                    "hh_raiser.application.page_group_service.view_search_page",
                    return_value=(result(ActivityKind.REVIEW_SEARCH), urls, 1),
                ),
                patch(
                    "hh_raiser.application.page_group_service.view_vacancies",
                    side_effect=view_one,
                ) as view,
                patch(
                    "hh_raiser.application.page_group_service.review_resume",
                    return_value=result(ActivityKind.REVIEW_RESUME),
                ),
            ):
                run_vacancy_page_group(object(), policy, traversal, history, "Python developer")
                run_vacancy_page_group(object(), policy, traversal, history, "Python developer")

            self.assertEqual(view.call_count, 10)

    def test_starts_new_history_generation_after_exhausted_cycle(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "history.sqlite3")
            url = "https://hh.ru/vacancy/1"
            history.reserve_unseen([url], search_query="Python", limit=1, revisit_after_days=0)
            history.mark_viewed(url)
            traversal = VacancyTraversal(("Python",), randomizer=random.Random(1))
            policy = ActivityPolicy(vacancy_matching=False)

            with (
                patch(
                    "hh_raiser.application.page_group_service.view_search_page",
                    return_value=(result(ActivityKind.REVIEW_SEARCH), [url], 1),
                ) as search,
                patch(
                    "hh_raiser.application.page_group_service.view_vacancies",
                    return_value=[
                        VacancyViewOutcome(
                            url=url,
                            result=result(
                                ActivityKind.VIEW_VACANCY, vacancy_title="Python developer"
                            ),
                        )
                    ],
                ),
                patch(
                    "hh_raiser.application.page_group_service.review_resume",
                    return_value=result(ActivityKind.REVIEW_RESUME),
                ),
            ):
                run_vacancy_page_group(object(), policy, traversal, history, "Python developer")
                run_vacancy_page_group(object(), policy, traversal, history, "Python developer")

            self.assertEqual(history.generation, 2)
            self.assertEqual(search.call_count, 1)

    def test_response_outcome_message_explains_existing_hh_response(self) -> None:
        message = _response_outcome_message("already_sent")

        self.assertIn("не отправлен", message)
        self.assertIn("существующий отклик", message)

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

    def test_persists_discovered_and_viewed_vacancy_for_bot_statistics(self) -> None:
        with TemporaryDirectory() as directory:
            state_dir = Path(directory)
            history = VacancyHistory(state_dir / "vacancy-history.sqlite3")
            url = "https://hh.ru/vacancy/8"
            traversal = VacancyTraversal(("Python",), randomizer=random.Random(1))
            policy = ActivityPolicy(
                vacancies_per_cycle=1,
                vacancy_matching=False,
            )
            viewed = VacancyViewOutcome(
                url=url,
                result=result(ActivityKind.VIEW_VACANCY, vacancy_title="Python developer"),
            )
            with (
                patch(
                    "hh_raiser.application.page_group_service.view_search_page",
                    return_value=(result(ActivityKind.REVIEW_SEARCH), [url], 1),
                ),
                patch(
                    "hh_raiser.application.page_group_service.view_vacancies",
                    return_value=[viewed],
                ),
                patch(
                    "hh_raiser.application.page_group_service.review_resume",
                    return_value=result(ActivityKind.REVIEW_RESUME),
                ),
            ):
                run_vacancy_page_group(object(), policy, traversal, history, "Python developer")

            self.assertEqual(history.search_coverage(("Python",))["Python"][0], 1)
            self.assertEqual(history.response_records(), [])
            self.assertEqual(history.sent_response_count_today(), 0)
            statistics = read_instance_statistics(state_dir)

        self.assertEqual(statistics.discovered, 1)
        self.assertEqual(statistics.total_views, 1)


if __name__ == "__main__":
    unittest.main()
