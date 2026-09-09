from __future__ import annotations

import unittest

from hh_raiser.application.vacancy_rotation import VacancyRotation
from hh_raiser.domain.policies import ActivityPolicy
from hh_raiser.infrastructure.browser.page_state_reader import (
    canonical_vacancy_url,
    redact_url,
)


class ActivityPolicyTests(unittest.TestCase):
    def test_rejects_unbounded_activity(self) -> None:
        with self.assertRaises(ValueError):
            ActivityPolicy(vacancies_per_cycle=26)

    def test_default_cycle_views_more_than_two_vacancies(self) -> None:
        self.assertEqual(ActivityPolicy().vacancies_per_cycle, 10)

    def test_accepts_disabled_vacancy_views(self) -> None:
        self.assertEqual(ActivityPolicy(vacancies_per_cycle=0).vacancies_per_cycle, 0)

    def test_rejects_too_many_vacancy_scrolls(self) -> None:
        with self.assertRaises(ValueError):
            ActivityPolicy(vacancy_scrolls=11)


class SafeUrlTests(unittest.TestCase):
    def test_redacts_query_fragment_and_numeric_identifier(self) -> None:
        self.assertEqual(
            redact_url("https://hh.ru/vacancy/12345678?from=private#section"),
            "https://hh.ru/vacancy/<id>",
        )

    def test_canonical_vacancy_url_drops_query(self) -> None:
        self.assertEqual(
            canonical_vacancy_url("https://hh.ru/vacancy/12345678?from=search"),
            "https://hh.ru/vacancy/12345678",
        )

    def test_rejects_non_hh_link(self) -> None:
        self.assertIsNone(canonical_vacancy_url("https://example.com/vacancy/12345678"))

    def test_accepts_relative_hh_vacancy_link(self) -> None:
        self.assertEqual(
            canonical_vacancy_url("/vacancy/12345678?from=search"),
            "https://hh.ru/vacancy/12345678",
        )


class VacancyRotationTests(unittest.TestCase):
    def test_rotates_pages_and_queries(self) -> None:
        rotation = VacancyRotation(queries=("first", "second"), pages_per_query=2)

        self.assertEqual(
            [rotation.next_search() for _ in range(5)],
            [("first", 0), ("second", 0), ("first", 1), ("second", 1), ("first", 0)],
        )

    def test_prefers_unseen_vacancies_and_recycles_after_exhaustion(self) -> None:
        rotation = VacancyRotation(queries=("any profession",))
        urls = ["https://hh.ru/vacancy/1", "https://hh.ru/vacancy/2"]

        first = rotation.select(urls, 1)
        second = rotation.select(urls, 1)
        third = rotation.select(urls, 1)

        self.assertNotEqual(first, second)
        self.assertEqual(len(third), 1)
