from __future__ import annotations

import random
import unittest

from hh_raiser.application.vacancy_traversal import VacancyTraversal


class VacancyTraversalTests(unittest.TestCase):
    def test_splits_nine_vacancies_into_balanced_groups_of_five_and_four(self) -> None:
        traversal = VacancyTraversal(("Python",), randomizer=random.Random(3))
        request = traversal.next_search()
        urls = [f"https://hh.ru/vacancy/{index}" for index in range(1, 10)]

        group_count = traversal.observe_search(
            request,
            page_count=1,
            urls=urls,
            group_size=5,
        )
        first = traversal.pop_group()
        second = traversal.pop_group()

        self.assertEqual(group_count, 2)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None and second is not None
        self.assertEqual([len(first.urls), len(second.urls)], [5, 4])
        self.assertEqual(set(first.urls + second.urls), set(urls))

    def test_finishes_shuffled_pages_before_advancing_to_next_query(self) -> None:
        traversal = VacancyTraversal(
            ("Python", "Backend"),
            randomizer=random.Random(7),
        )
        first = traversal.next_search()
        self.assertEqual((first.query, first.page), ("Python", 0))
        traversal.observe_search(first, page_count=4, urls=[], group_size=5)

        python_pages: list[int] = []
        for _ in range(3):
            request = traversal.next_search()
            python_pages.append(request.page)
            self.assertEqual(request.query, "Python")
            traversal.observe_search(request, page_count=4, urls=[], group_size=5)

        backend = traversal.next_search()

        self.assertEqual(set(python_pages), {1, 2, 3})
        self.assertEqual((backend.query, backend.page), ("Backend", 0))

    def test_limits_pages_for_each_query_in_cycle(self) -> None:
        traversal = VacancyTraversal(("Python",), page_limit=2, randomizer=random.Random(7))

        first = traversal.next_search()
        assert first is not None
        traversal.observe_search(first, page_count=4, urls=[], group_size=5)
        second = traversal.next_search()
        assert second is not None
        traversal.observe_search(second, page_count=4, urls=[], group_size=5)
        repeated = traversal.next_search()

        self.assertEqual((first.page, second.page), (0, 1))
        self.assertIsNotNone(repeated)
        assert repeated is not None
        self.assertEqual((repeated.page, repeated.cycle), (0, 2))

    def test_starts_a_new_cycle_after_all_queries(self) -> None:
        traversal = VacancyTraversal(("Python", "Backend"), randomizer=random.Random(1))
        first = traversal.next_search()
        traversal.observe_search(first, page_count=1, urls=[], group_size=5)
        second = traversal.next_search()
        traversal.observe_search(second, page_count=1, urls=[], group_size=5)

        repeated = traversal.next_search()

        self.assertEqual((repeated.query, repeated.page, repeated.cycle), ("Python", 0, 2))


if __name__ == "__main__":
    unittest.main()
