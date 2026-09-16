from __future__ import annotations

import unittest

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
