from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from hh_raiser.activities.resume_index_refresher import (
    ResumeMarkerState,
    _confirm_saved_experience_description,
    _direct_resume_url,
    _dismiss_cookie_informer,
    _edit_button_state,
    _expand_experience_if_collapsed,
    _fill_stable_experience_description,
    _profile_resume_url,
    _read_saved_experience_description,
    _submit_resume_and_wait,
    _timeout_detail,
    build_marked_description,
    description_matches_marker_base,
    remove_one_trailing_period,
    restore_marked_description,
)
from hh_raiser.infrastructure.hh.selectors import EXPERIENCE_EDIT_BUTTON


class ResumeIndexRefresherTests(unittest.TestCase):
    def test_refills_description_after_hh_replaces_it_during_loading(self) -> None:
        class Description:
            value = "Старый текст."
            fills = 0

            def fill(self, value):
                self.value = value
                self.fills += 1

            def input_value(self):
                return self.value

        class Page:
            waits = 0

            def wait_for_timeout(self, milliseconds):
                self.waits += 1
                if self.waits == 1:
                    description.value = "Старый текст."

        description = Description()
        page = Page()
        self.assertTrue(_fill_stable_experience_description(description, "Новый текст.", page))
        self.assertEqual(description.fills, 2)
        self.assertEqual(page.waits, 2)
        self.assertEqual(description.value, "Новый текст.")

    def test_waits_for_profile_update_response_before_confirming_save(self) -> None:
        from contextlib import contextmanager
        from types import SimpleNamespace

        events = []
        response = SimpleNamespace(status=200)

        class Page:
            @contextmanager
            def expect_response(self, predicate, *, timeout):
                self.assert_predicate = predicate
                self.timeout = timeout
                events.append("listening")
                yield SimpleNamespace(value=response)

            def locator(self, selector):
                if selector == '[data-qa="cookies-policy-informer"]':
                    return SimpleNamespace(count=lambda: 0)
                return SimpleNamespace(click=lambda: events.append("clicked"))

        page = Page()
        self.assertEqual(_submit_resume_and_wait(page), 200)
        self.assertEqual(events, ["listening", "clicked"])
        self.assertEqual(page.timeout, 30_000)
        self.assertTrue(
            page.assert_predicate(
                SimpleNamespace(
                    request=SimpleNamespace(method="POST"),
                    url="https://hh.ru/profile/shards/profile/update",
                )
            )
        )

    def test_retries_cookie_banner_until_it_disappears(self) -> None:
        from types import SimpleNamespace

        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        events = []

        class CookieBanner:
            clicks = 0

            def count(self):
                return 1

            def is_visible(self, *, timeout):
                return self.clicks < 2

            def get_by_role(self, role, *, name, exact):
                def click():
                    self.clicks += 1
                    events.append("cookie_clicked")

                return SimpleNamespace(click=click)

            def wait_for(self, *, state, timeout):
                if self.clicks == 1:
                    raise PlaywrightTimeoutError("Cookie banner still visible")
                events.append("cookie_hidden")

        class Page:
            def locator(self, selector):
                if selector == '[data-qa="cookies-policy-informer"]':
                    return CookieBanner()

        _dismiss_cookie_informer(Page())
        self.assertEqual(events, ["cookie_clicked", "cookie_clicked", "cookie_hidden"])

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

    @patch("hh_raiser.activities.resume_index_refresher._open_resume_experience_controls")
    def test_reloads_resume_before_reading_saved_experience(self, open_controls: MagicMock) -> None:
        page = MagicMock()
        edit_buttons = MagicMock()
        edit_button = edit_buttons.nth.return_value
        description = page.locator.return_value.first
        description.input_value.return_value = "Сохранённый текст."
        open_controls.return_value = (edit_buttons, 3)

        result = _read_saved_experience_description(
            page,
            resume_title="Python-разработчик",
            target_index=1,
            captcha_guard=None,
            stop_requested=None,
        )

        self.assertEqual(result, ("Сохранённый текст.", 3))
        open_controls.assert_called_once_with(
            page,
            resume_title="Python-разработчик",
            captcha_guard=None,
            stop_requested=None,
        )
        edit_buttons.nth.assert_called_once_with(1)
        edit_button.click.assert_called_once_with()
        description.wait_for.assert_called_once_with(state="visible", timeout=15_000)

    @patch("hh_raiser.activities.resume_index_refresher._read_saved_experience_description")
    def test_confirmation_reloads_only_when_hh_still_shows_previous_value(
        self, read_saved: MagicMock
    ) -> None:
        page = MagicMock()
        read_saved.side_effect = [("Описание.", 3), ("Описание..", 3)]

        result = _confirm_saved_experience_description(
            page,
            resume_title="Python-разработчик",
            target_index=1,
            button_count=3,
            previous_value="Описание.",
            expected_value="Описание..",
            captcha_guard=None,
            stop_requested=None,
        )

        self.assertEqual(result, ("Описание..", 3))
        self.assertEqual(read_saved.call_count, 2)
        page.wait_for_timeout.assert_called_once_with(1_000)

    @patch("hh_raiser.activities.resume_index_refresher._read_saved_experience_description")
    def test_confirmation_does_not_retry_unexpected_description(
        self, read_saved: MagicMock
    ) -> None:
        page = MagicMock()
        read_saved.return_value = ("Описание изменено владельцем.", 3)

        result = _confirm_saved_experience_description(
            page,
            resume_title="Python-разработчик",
            target_index=1,
            button_count=3,
            previous_value="Описание.",
            expected_value="Описание..",
            captcha_guard=None,
            stop_requested=None,
        )

        self.assertEqual(result, ("Описание изменено владельцем.", 3))
        read_saved.assert_called_once()
        page.wait_for_timeout.assert_not_called()
