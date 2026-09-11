from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlsplit

from hh_raiser.domain.search_filters import ExperienceLevel, SearchField, SearchFilters
from hh_raiser.infrastructure.hh.search_url import build_search_url


class SearchUrlTests(unittest.TestCase):
    def test_builds_observed_repeatable_hh_filter_parameters(self) -> None:
        url = build_search_url(
            query="Python разработчик",
            page=3,
            filters=SearchFilters(
                excluded_words=("senior", "аналитик"),
                search_fields=(SearchField.VACANCY_NAME,),
                experience=(
                    ExperienceLevel.BETWEEN_ONE_AND_THREE,
                    ExperienceLevel.BETWEEN_THREE_AND_SIX,
                ),
                areas=("1", "2019"),
            ),
        )

        parameters = parse_qs(urlsplit(url).query)
        self.assertEqual(parameters["text"], ["Python разработчик"])
        self.assertEqual(parameters["page"], ["3"])
        self.assertEqual(parameters["excluded_text"], ["senior, аналитик"])
        self.assertEqual(parameters["search_field"], ["name"])
        self.assertEqual(
            parameters["experience"],
            ["between1And3", "between3And6"],
        )
        self.assertEqual(parameters["area"], ["1", "2019"])

    def test_empty_filters_preserve_legacy_search_url(self) -> None:
        url = build_search_url(query="Backend", page=0, filters=SearchFilters())

        self.assertEqual(
            parse_qs(urlsplit(url).query),
            {"text": ["Backend"], "page": ["0"]},
        )

    def test_rejects_multiline_excluded_word(self) -> None:
        with self.assertRaises(ValueError):
            SearchFilters(excluded_words=("senior\nlead",))

    def test_rejects_invalid_area_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive HH region IDs"):
            SearchFilters(areas=("Москва",))
