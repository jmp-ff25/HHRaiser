from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from hh_raiser.activities.search_page_viewer import pagination_page_count
from hh_raiser.activities.vacancy_viewer import normalize_vacancy_title, view_vacancies
from hh_raiser.domain.matching import MatchAssessment
from hh_raiser.domain.policies import ActivityPolicy
from hh_raiser.domain.result import ActivityStatus
from hh_raiser.infrastructure.hh.selectors import (
    VACANCY_DESCRIPTION,
    VACANCY_HEADING,
    VACANCY_SKILL,
)


class FakeLocator:
    def __init__(self, text: str = "", *, texts: list[str] | None = None) -> None:
        self.text = text
        self.texts = texts or []
        self.press_calls: list[str] = []

    @property
    def first(self) -> FakeLocator:
        return self

    def count(self) -> int:
        return 1

    def is_visible(self) -> bool:
        return True

    def wait_for(self, **_kwargs) -> None:
        return None

    def inner_text(self) -> str:
        return self.text

    def all_inner_texts(self) -> list[str]:
        return self.texts

    def press(self, key: str) -> None:
        self.press_calls.append(key)


class FakeVacancyPage:
    def __init__(self) -> None:
        self.heading = FakeLocator("Водитель-экспедитор")
        self.description = FakeLocator("Доставка грузов и обслуживание автомобиля")
        self.skills = FakeLocator(texts=["Водительское удостоверение"])
        self.body = FakeLocator()
        self.wait_calls: list[int] = []

    def bring_to_front(self) -> None:
        return None

    def goto(self, _url: str, **_kwargs) -> None:
        return None

    def locator(self, selector: str) -> FakeLocator:
        return {
            VACANCY_HEADING: self.heading,
            VACANCY_DESCRIPTION: self.description,
            VACANCY_SKILL: self.skills,
            "body": self.body,
        }[selector]

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.wait_calls.append(milliseconds)


class VacancyViewerTests(unittest.TestCase):
    def test_rejected_vacancy_is_scored_before_scroll_and_not_viewed(self) -> None:
        page = FakeVacancyPage()
        matcher = Mock()
        matcher.evaluate.return_value = MatchAssessment(
            score=12,
            accepted=False,
            applied=True,
            title_similarity=0.0,
            bm25f_relevance=0.0,
            skills_coverage=0.0,
            lexical_similarity=0.0,
        )

        with patch("hh_raiser.activities.vacancy_viewer.dismiss_hh_pro_modal"):
            outcomes = list(
                view_vacancies(
                    page,
                    ["https://hh.ru/vacancy/123"],
                    ActivityPolicy(
                        vacancy_scrolls=3,
                        scroll_pause_seconds=0,
                        vacancy_view_seconds=0,
                    ),
                    matcher=matcher,
                )
            )

        self.assertEqual(outcomes[0].result.status, ActivityStatus.SKIPPED)
        self.assertEqual(outcomes[0].result.metadata["scrolls_completed"], 0)
        self.assertEqual(page.body.press_calls, [])
        self.assertEqual(page.wait_calls, [])

    def test_title_is_safe_for_single_line_log(self) -> None:
        self.assertEqual(
            normalize_vacancy_title("  Senior Python\nDeveloper  "),
            "Senior Python Developer",
        )

    def test_empty_title_has_readable_fallback(self) -> None:
        self.assertEqual(normalize_vacancy_title("  \n "), "название не распознано")

    def test_reads_zero_based_page_count_from_pagination_links(self) -> None:
        self.assertEqual(
            pagination_page_count(
                ["/search/vacancy?text=Python&page=1", "/search/vacancy?page=7"],
                current_page=0,
            ),
            8,
        )
