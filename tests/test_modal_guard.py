from __future__ import annotations

import unittest

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from hh_raiser.infrastructure.browser.modal_guard import (
    dismiss_hh_pro_modal,
    hh_pro_modal_visible,
)
from hh_raiser.infrastructure.hh.selectors import HH_PRO_PAYMENT_BUTTON


class PaymentButton:
    pass


class CloseButton:
    def __init__(self, dialog: Dialog) -> None:
        self.dialog = dialog

    def is_visible(self, *, timeout: int) -> bool:
        return True

    def click(self, *, timeout: int) -> None:
        self.dialog.visible = False


class CloseButtons:
    def __init__(self, dialog: Dialog, count: int = 1) -> None:
        self._count = count
        self.first = CloseButton(dialog)

    def count(self) -> int:
        return self._count


class Dialog:
    def __init__(self, *, visible: bool, close_count: int = 1) -> None:
        self.visible = visible
        self.close_buttons = CloseButtons(self, close_count)
        self.first = self

    def filter(self, *, has: object) -> Dialog:
        return self

    def count(self) -> int:
        return int(self.visible)

    def is_visible(self, *, timeout: int) -> bool:
        return self.visible

    def wait_for(self, *, state: str, timeout: int) -> None:
        if state != "hidden" or self.visible:
            raise PlaywrightTimeoutError("still visible")

    def locator(self, selector: str) -> CloseButtons:
        if selector != "button:not([data-qa])":
            raise AssertionError(f"Unexpected selector: {selector}")
        return self.close_buttons


class Keyboard:
    def __init__(self, dialog: Dialog, *, escape_works: bool) -> None:
        self.dialog = dialog
        self.escape_works = escape_works
        self.pressed: list[str] = []

    def press(self, key: str) -> None:
        self.pressed.append(key)
        if key == "Escape" and self.escape_works:
            self.dialog.visible = False


class Page:
    def __init__(self, *, visible: bool, escape_works: bool = True) -> None:
        self.dialog = Dialog(visible=visible)
        self.keyboard = Keyboard(self.dialog, escape_works=escape_works)

    def locator(self, selector: str) -> PaymentButton:
        if selector != HH_PRO_PAYMENT_BUTTON:
            raise AssertionError(f"Unexpected selector: {selector}")
        return PaymentButton()

    def get_by_role(self, role: str) -> Dialog:
        if role != "dialog":
            raise AssertionError(f"Unexpected role: {role}")
        return self.dialog


class ModalGuardTests(unittest.TestCase):
    def test_no_modal_is_a_noop(self) -> None:
        page = Page(visible=False)

        self.assertFalse(hh_pro_modal_visible(page))
        self.assertFalse(dismiss_hh_pro_modal(page))
        self.assertEqual(page.keyboard.pressed, [])

    def test_closes_known_modal_with_escape(self) -> None:
        page = Page(visible=True)

        self.assertTrue(dismiss_hh_pro_modal(page))

        self.assertFalse(page.dialog.visible)
        self.assertEqual(page.keyboard.pressed, ["Escape"])

    def test_uses_unique_close_button_when_escape_does_not_work(self) -> None:
        page = Page(visible=True, escape_works=False)

        self.assertTrue(dismiss_hh_pro_modal(page))

        self.assertFalse(page.dialog.visible)
