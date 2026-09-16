from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from hh_raiser.infrastructure.browser.captcha_guard import (
    CaptchaGuard,
    ManualCaptchaGuard,
    ManualCaptchaRequired,
    captcha_controls,
)
from hh_raiser.infrastructure.storage.owner_interventions import OwnerInterventionStore


class OwnerInterventionStoreTests(unittest.TestCase):
    def test_delivers_reply_only_to_matching_pending_challenge(self) -> None:
        with TemporaryDirectory() as directory:
            store = OwnerInterventionStore(Path(directory))
            screenshot = store.screenshot_dir / "captcha-one.png"
            screenshot.write_bytes(b"png")
            store.create_captcha("one", screenshot, "Введите текст")

            pending = store.pending_for_user(100)
            self.assertEqual([item.challenge_id for item in pending], ["one"])
            store.mark_notified("one", 100, 55)

            self.assertEqual(store.pending_for_user(100), [])
            self.assertFalse(store.submit_reply(200, 55, "чужой ответ"))
            self.assertTrue(store.submit_reply(100, 55, "верный ответ"))
            self.assertFalse(store.submit_reply(100, 55, "повтор"))
            self.assertEqual(store.take_answer("one"), "верный ответ")
            self.assertIsNone(store.take_answer("one"))

            store.finish("one", "resolved")
            self.assertFalse(screenshot.exists())

    def test_each_allowed_owner_receives_the_same_pending_challenge_once(self) -> None:
        with TemporaryDirectory() as directory:
            store = OwnerInterventionStore(Path(directory))
            screenshot = store.screenshot_dir / "captcha-two.png"
            screenshot.write_bytes(b"png")
            store.create_captcha("two", screenshot, "Введите текст")

            store.mark_notified("two", 100, 10)

            self.assertEqual(store.pending_for_user(100), [])
            self.assertEqual(
                [item.challenge_id for item in store.pending_for_user(200)],
                ["two"],
            )

            store.cancel_pending()
            self.assertEqual(store.pending_for_user(200), [])
            self.assertFalse(screenshot.exists())


class CaptchaRecognitionTests(unittest.TestCase):
    def test_recognizes_hh_captcha_by_stable_path(self) -> None:
        page = MagicMock()
        page.url = "https://hh.ru/account/captcha?backurl=private"

        self.assertTrue(CaptchaGuard.is_present(page))
        page.get_by_role.assert_not_called()

    def test_does_not_treat_vacancy_title_as_captcha_without_heading(self) -> None:
        page = MagicMock()
        page.url = "https://hh.ru/vacancy/123"
        heading = page.get_by_role.return_value.first
        heading.count.return_value = 0

        self.assertFalse(CaptchaGuard.is_present(page))

    def test_uses_accessible_captcha_controls_with_stable_fallbacks(self) -> None:
        page = MagicMock()
        role_image = MagicMock()
        role_input = MagicMock()
        role_submit = MagicMock()
        combined_image = MagicMock()
        combined_input = MagicMock()
        combined_submit = MagicMock()
        page.get_by_role.side_effect = [role_image, role_input, role_submit]
        role_image.or_.return_value = combined_image
        role_input.or_.return_value = combined_input
        role_submit.or_.return_value = combined_submit

        controls = captcha_controls(page)

        self.assertEqual(
            controls,
            (combined_image.first, combined_input.first, combined_submit.first),
        )
        page.get_by_role.assert_any_call("img", name="captcha", exact=True)
        page.get_by_role.assert_any_call(
            "textbox",
            name="Текст с картинки",
            exact=True,
        )
        page.get_by_role.assert_any_call("button", name="Отправить", exact=True)
        selectors = [call.args[0] for call in page.locator.call_args_list]
        self.assertEqual(
            selectors,
            [
                '[data-qa="account-captcha-picture"]',
                '[data-qa="account-captcha-input"]',
                '[data-qa="account-captcha-submit"]',
            ],
        )

    def test_visible_browser_waits_for_owner_to_complete_captcha(self) -> None:
        page = MagicMock()
        page.is_closed.return_value = False
        guard = ManualCaptchaGuard(headless=False)

        with patch(
            "hh_raiser.infrastructure.browser.captcha_guard.CaptchaGuard.is_present",
            side_effect=[True, True, False],
        ):
            self.assertTrue(guard.resolve_if_present(page))

        page.wait_for_timeout.assert_called_once()

    def test_headless_browser_stops_for_manual_captcha(self) -> None:
        page = MagicMock()
        guard = ManualCaptchaGuard(headless=True)

        with (
            patch(
                "hh_raiser.infrastructure.browser.captcha_guard.CaptchaGuard.is_present",
                return_value=True,
            ),
            self.assertRaisesRegex(ManualCaptchaRequired, "без окна"),
        ):
            guard.resolve_if_present(page)


if __name__ == "__main__":
    unittest.main()
