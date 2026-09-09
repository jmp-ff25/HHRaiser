from __future__ import annotations

import unittest

from hh_raiser.activities.resume_index_refresher import (
    ResumeMarkerState,
    build_marked_description,
    description_matches_marker_base,
    remove_one_trailing_period,
    restore_marked_description,
)


class ResumeIndexRefresherTests(unittest.TestCase):
    def test_marker_adds_exactly_one_period(self) -> None:
        marked, state = build_marked_description("Описание.", target_index=2)

        self.assertEqual(marked, "Описание..")
        self.assertEqual(state.target_index, 2)

    def test_restores_after_browser_normalizes_line_endings(self) -> None:
        marked, state = build_marked_description("Строка 1\r\nСтрока 2.\n", target_index=0)

        self.assertEqual(marked, "Строка 1\r\nСтрока 2..\n")
        self.assertEqual(
            restore_marked_description("Строка 1\nСтрока 2..\n", state),
            "Строка 1\nСтрока 2.\n",
        )

    def test_rejects_meaningfully_changed_description_and_keeps_marker(self) -> None:
        _, state = build_marked_description("Описание.", target_index=0)

        self.assertIsNone(restore_marked_description("Изменено вручную..", state))

    def test_recognizes_marker_already_removed_after_interrupted_confirmation(self) -> None:
        _, state = build_marked_description("Строка 1\r\nСтрока 2.", target_index=0)

        self.assertTrue(description_matches_marker_base("Строка 1\nСтрока 2.", state))

    def test_legacy_marker_removes_exactly_one_owned_period(self) -> None:
        state = ResumeMarkerState(target_index=0, base_trailing_periods=-1, base_fingerprint="")

        self.assertEqual(restore_marked_description("Описание...", state), "Описание..")

    def test_repairs_one_orphaned_period_at_a_time(self) -> None:
        self.assertEqual(remove_one_trailing_period("Описание...\n"), "Описание..\n")
