"""Опциональная проверка открытой CAPTCHA HH через локальный CDP без действий на странице."""

from __future__ import annotations

import os
import unittest
from urllib.parse import urlsplit

from hh_raiser.infrastructure.browser.captcha_guard import CaptchaGuard, captcha_controls


@unittest.skipUnless(
    os.environ.get("HH_LIVE_LOGIN_CAPTCHA_E2E") == "1",
    "Живой тест CAPTCHA отключён. Укажите HH_LIVE_LOGIN_CAPTCHA_E2E=1 для открытого CDP-сеанса.",
)
class LoginCaptchaLiveTests(unittest.TestCase):
    """Проверить реальные локаторы CAPTCHA в уже открытом браузере без кликов и ввода."""

    @classmethod
    def setUpClass(cls) -> None:
        from playwright.sync_api import sync_playwright

        port = os.environ.get("HH_LIVE_CDP_PORT", "9222")
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        pages = [page for context in cls.browser.contexts for page in context.pages]
        cls.page = next(
            (page for page in pages if urlsplit(page.url).path == "/account/login"),
            None,
        )
        if cls.page is None:
            cls.playwright.stop()
            raise unittest.SkipTest("Не найдена открытая страница входа HH на локальном CDP.")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.playwright.stop()

    def test_recognizes_open_login_captcha_modal_without_interaction(self) -> None:
        image, input_field, submit = captcha_controls(self.page)

        self.assertTrue(CaptchaGuard.is_present(self.page))
        for control in (image, input_field, submit):
            self.assertGreater(control.count(), 0)
            self.assertTrue(control.is_visible(timeout=0))
