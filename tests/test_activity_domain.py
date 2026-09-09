from __future__ import annotations

import random
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

    def test_rejects_unbounded_search_page_budget(self) -> None:
        with self.assertRaises(ValueError):
            ActivityPolicy(search_pages_per_cycle=201)


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
    def test_starts_each_unknown_query_from_first_page(self) -> None:
        rotation = VacancyRotation(
            queries=("first", "second"),
            randomizer=random.Random(7),
        )

        first = rotation.next_search()
        self.assertIsNotNone(first)
        assert first is not None
        rotation.observe_search(*first, page_count=3)

        second = rotation.next_search()
        self.assertIsNotNone(second)
        assert second is not None
        self.assertNotEqual(first[0], second[0])
        self.assertEqual(second[1], 0)

    def test_randomizes_pages_without_repeating_within_pass(self) -> None:
        rotation = VacancyRotation(queries=("python",), randomizer=random.Random(3))
        rotation.observe_search("python", 0, page_count=4)

        pages: list[int] = []
        for _ in range(3):
            search = rotation.next_search()
            self.assertIsNotNone(search)
            assert search is not None
            query, page = search
            pages.append(page)
            rotation.observe_search(query, page, page_count=4)

        self.assertEqual(set(pages), {1, 2, 3})
        self.assertIsNone(rotation.next_search())

    def test_reset_coverage_starts_a_new_page_pass(self) -> None:
        rotation = VacancyRotation(queries=("python",), randomizer=random.Random(1))
        rotation.observe_search("python", 0, page_count=1)
        self.assertIsNone(rotation.next_search())

        rotation.reset_coverage()

        self.assertEqual(rotation.next_search(), ("python", 0))
