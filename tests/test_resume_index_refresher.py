from __future__ import annotations

import unittest

from hh_raiser.activities.resume_index_refresher import (
    ResumeMarkerState,
    _direct_resume_url,
    _edit_button_state,
    _expand_experience_if_collapsed,
    _profile_resume_url,
    _timeout_detail,
    build_marked_description,
    description_matches_marker_base,
    remove_one_trailing_period,
    restore_marked_description,
)
from hh_raiser.infrastructure.hh.selectors import EXPERIENCE_EDIT_BUTTON


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

    def test_targets_experience_edit_buttons_not_nested_icons(self) -> None:
        self.assertEqual(EXPERIENCE_EDIT_BUTTON, 'button[data-qa^="edit-experience-button-"]')

    def test_timeout_detail_identifies_unconfirmed_stage(self) -> None:
        self.assertEqual(
            _timeout_detail("ожидание кнопок редактирования опыта"),
            "Интерфейс не подтвердил этап «ожидание кнопок редактирования опыта»; "
            "сохранение автоматически не повторяется.",
        )

    def test_edit_button_state_reports_dom_and_visible_counts(self) -> None:
        class Locator:
            def count(self) -> int:
                return 2

            def nth(self, index: int) -> Locator:
                self.index = index
                return self

            def is_visible(self, *, timeout: int) -> bool:
                return self.index == 0 and timeout == 0

        class Page:
            def locator(self, selector: str) -> Locator:
                self.selector = selector
                return Locator()

        self.assertEqual(_edit_button_state(Page()), "кнопок в DOM: 2; видимых: 1")

    def test_accepts_only_relative_hh_resume_link(self) -> None:
        self.assertEqual(
            _direct_resume_url("/resume/example"),
            "https://hh.ru/resume/example",
        )
        self.assertIsNone(_direct_resume_url("https://example.com/resume/example"))

    def test_uses_resume_link_from_card_with_configured_title(self) -> None:
        class Link:
            def get_attribute(self, name: str) -> str | None:
                return "/resume/example" if name == "href" else None

        class Links:
            count = lambda self: 1
            first = Link()

        class Card:
            def get_by_role(self, role: str, *, name: str, exact: bool) -> Headings:
                self.role = role
                self.name = name
                self.exact = exact
                return Headings(name == "Python-разработчик")

            def locator(self, selector: str) -> Links:
                return Links()

        class Headings:
            def __init__(self, matches: bool) -> None:
                self.matches = matches

            def count(self) -> int:
                return int(self.matches)

        class Cards:
            def __init__(self) -> None:
                self.card = Card()

            def count(self) -> int:
                return 1

            def nth(self, index: int) -> Card:
                self.index = index
                return self.card

        class Page:
            def locator(self, selector: str) -> Cards:
                return Cards()

        self.assertEqual(
            _profile_resume_url(Page(), "Python-разработчик"),
            "https://hh.ru/resume/example",
        )
        self.assertIsNone(_profile_resume_url(Page(), "Другое резюме"))

    def test_expands_collapsed_experience_list_before_editing(self) -> None:
        class ViewAll:
            def __init__(self) -> None:
                self.clicked = False

            @property
            def first(self) -> ViewAll:
                return self

            def count(self) -> int:
                return 1

            def is_visible(self, *, timeout: int) -> bool:
                return timeout == 0

            def click(self) -> None:
                self.clicked = True

        class Page:
            def __init__(self) -> None:
                self.view_all = ViewAll()

            def locator(self, selector: str) -> ViewAll:
                return self.view_all

        page = Page()

        self.assertTrue(_expand_experience_if_collapsed(page))
        self.assertTrue(page.view_all.clicked)
