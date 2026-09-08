from __future__ import annotations

import unittest
from unittest.mock import patch

from hh_raiser.browser import (
    choose_login_action,
    close_context_quietly,
    is_closed_playwright_error,
    read_page_state,
    wait_for_profile_raise_state,
)
from hh_raiser.models import LoginEvidence, PageState


class BrowserTests(unittest.TestCase):
    def test_read_page_state_checks_button_without_implicit_playwright_wait(self) -> None:
        class Button:
            def __init__(self) -> None:
                self.enabled_timeout: float | None = None

            def is_visible(self, *, timeout: float | None = None) -> bool:
                return True

            def is_enabled(self, *, timeout: float | None = None) -> bool:
                self.enabled_timeout = timeout
                return False

        class Body:
            def inner_text(self) -> str:
                return ""

        class Page:
            def locator(self, selector: str) -> Body:
                if selector != "body":
                    raise AssertionError(f"Unexpected selector: {selector}")
                return Body()

        button = Button()
        with patch("hh_raiser.browser.find_raise_button", return_value=button):
            read_page_state(Page(), "Python-разработчик")

        self.assertEqual(button.enabled_timeout, 0)

    def test_wait_for_profile_raise_state_reloads_after_timeout_and_retries(self) -> None:
        class Page:
            def __init__(self) -> None:
                self.reloads = 0
                self.waits: list[int] = []

            def wait_for_timeout(self, milliseconds: int) -> None:
                self.waits.append(milliseconds)

            def reload(self, *, wait_until: str) -> None:
                if wait_until != "domcontentloaded":
                    raise AssertionError(f"Unexpected wait strategy: {wait_until}")
                self.reloads += 1

        page = Page()
        with (
            patch(
                "hh_raiser.browser.read_page_state",
                side_effect=[
                    (PageState("unknown"), None),
                    (PageState("available"), object()),
                ],
            ),
            patch("hh_raiser.browser.dismiss_hh_pro_modal", return_value=False),
            patch("hh_raiser.browser.time.monotonic", side_effect=[0, 0, 300, 300, 300]),
            patch("hh_raiser.browser.LOGGER.warning"),
        ):
            state, _ = wait_for_profile_raise_state(
                page,
                "Python-разработчик",
                page_refresh_seconds=300,
            )

        self.assertEqual(state.kind, "available")
        self.assertEqual(page.reloads, 1)

    def test_login_landing_requires_opening_login_form(self) -> None:
        self.assertEqual(choose_login_action(LoginEvidence(landing_button=True)), "open-login-form")

    def test_phone_form_is_recognized(self) -> None:
        self.assertEqual(choose_login_action(LoginEvidence(phone_input=True)), "fill-phone")

    def test_password_form_is_recognized(self) -> None:
        self.assertEqual(choose_login_action(LoginEvidence(password_input=True)), "fill-password")

    def test_closed_context_does_not_interrupt_shutdown(self) -> None:
        target_closed_error = type("TargetClosedError", (Exception,), {})

        class ClosedContext:
            def close(self) -> None:
                raise target_closed_error()

        close_context_quietly(ClosedContext())

    def test_closed_driver_does_not_interrupt_shutdown(self) -> None:
        class DisconnectedContext:
            def close(self) -> None:
                raise RuntimeError(": Connection closed while reading from the driver")

        close_context_quietly(DisconnectedContext())

    def test_keyboard_interrupt_during_close_is_suppressed(self) -> None:
        class InterruptedContext:
            def close(self) -> None:
                raise KeyboardInterrupt

        close_context_quietly(InterruptedContext())

    def test_closed_playwright_error_is_recognized_for_restart(self) -> None:
        error = RuntimeError("Target page, context or browser has been closed")
        self.assertTrue(is_closed_playwright_error(error))
