from __future__ import annotations

import unittest

from hh_raiser.activities.vacancy_viewer import normalize_vacancy_title


class VacancyViewerTests(unittest.TestCase):
    def test_title_is_safe_for_single_line_log(self) -> None:
        self.assertEqual(
            normalize_vacancy_title("  Senior Python\nDeveloper  "),
            "Senior Python Developer",
        )

    def test_empty_title_has_readable_fallback(self) -> None:
        self.assertEqual(normalize_vacancy_title("  \n "), "название не распознано")
