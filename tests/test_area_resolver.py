from __future__ import annotations

import unittest

from hh_raiser.infrastructure.hh.area_resolver import (
    AreaResolutionError,
    resolve_area_names,
)

AREA_TREE = [
    {
        "id": "113",
        "name": "Россия",
        "areas": [
            {"id": "1", "name": "Москва", "areas": []},
            {
                "id": "2",
                "name": "Регион А",
                "areas": [{"id": "20", "name": "Советский", "areas": []}],
            },
            {
                "id": "3",
                "name": "Регион Б",
                "areas": [{"id": "30", "name": "Советский", "areas": []}],
            },
        ],
    }
]


class AreaResolverTests(unittest.TestCase):
    def test_resolves_readable_name_to_current_catalog_id(self) -> None:
        resolved = resolve_area_names(("москва",), AREA_TREE)

        self.assertEqual(resolved[0].area_id, "1")
        self.assertEqual(resolved[0].name, "Москва")

    def test_accepts_full_path_for_ambiguous_city(self) -> None:
        resolved = resolve_area_names(("Россия / Регион Б / Советский",), AREA_TREE)

        self.assertEqual(resolved[0].area_id, "30")

    def test_rejects_ambiguous_short_name(self) -> None:
        with self.assertRaisesRegex(AreaResolutionError, "неоднозначно"):
            resolve_area_names(("Советский",), AREA_TREE)

    def test_rejects_unknown_name(self) -> None:
        with self.assertRaisesRegex(AreaResolutionError, "не найден"):
            resolve_area_names(("Неизвестный город",), AREA_TREE)
