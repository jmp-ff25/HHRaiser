from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from playwright.sync_api import Error as PlaywrightError

from hh_raiser.infrastructure.hh.resume_reader import read_resume_text
from hh_raiser.infrastructure.hh.selectors import RESUME_CARD, RESUME_DIRECT_LINK


class ResumeReaderTests(unittest.TestCase):
    def test_reads_only_selected_resume_page(self) -> None:
        page = MagicMock()
        cards = MagicMock()
        cards.count.return_value = 1
        card = cards.nth.return_value
        card.get_by_role.return_value.count.return_value = 1
        links = card.locator.return_value
        links.count.return_value = 1
        links.first.get_attribute.return_value = "/resume/123"
        content = MagicMock()
        content.count.return_value = 1
        content.first.get_by_role.return_value.count.return_value = 1
        content.first.inner_text.return_value = "Python FastAPI PostgreSQL"
        page.locator.side_effect = lambda selector: cards if selector == RESUME_CARD else content

        text = read_resume_text(page, "Python-разработчик")

        self.assertEqual(text, "Python FastAPI PostgreSQL")
        page.goto.assert_called_once_with("https://hh.ru/resume/123", wait_until="domcontentloaded")
        card.locator.assert_called_once_with(RESUME_DIRECT_LINK)
        self.assertNotIn("body", [call.args[0] for call in page.locator.call_args_list])

    def test_ambiguous_resume_does_not_read_or_navigate(self) -> None:
        page = MagicMock()
        cards = page.locator.return_value
        cards.count.return_value = 2
        cards.nth.return_value.get_by_role.return_value.count.return_value = 1

        self.assertEqual(read_resume_text(page, "Python-разработчик"), "")
        page.goto.assert_not_called()

    def test_rejects_external_resume_link(self) -> None:
        page = MagicMock()
        cards = page.locator.return_value
        cards.count.return_value = 1
        card = cards.nth.return_value
        card.get_by_role.return_value.count.return_value = 1
        card.locator.return_value.count.return_value = 1
        card.locator.return_value.first.get_attribute.return_value = (
            "https://example.com/resume/123"
        )

        self.assertEqual(read_resume_text(page, "Python-разработчик"), "")
        page.goto.assert_not_called()

    def test_aborted_resume_navigation_does_not_crash_activity(self) -> None:
        page = MagicMock()
        cards = page.locator.return_value
        cards.count.return_value = 1
        card = cards.nth.return_value
        card.get_by_role.return_value.count.return_value = 1
        card.locator.return_value.count.return_value = 1
        card.locator.return_value.first.get_attribute.return_value = "/resume/123"
        page.goto.side_effect = PlaywrightError("Page.goto: net::ERR_ABORTED")

        self.assertEqual(read_resume_text(page, "Python-разработчик"), "")


if __name__ == "__main__":
    unittest.main()
