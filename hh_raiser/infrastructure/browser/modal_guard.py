from __future__ import annotations

from typing import TYPE_CHECKING

from hh_raiser.infrastructure.hh.selectors import HH_PRO_PAYMENT_BUTTON
from hh_raiser.logging_config import LOGGER, LogEvent, event_data

if TYPE_CHECKING:
    from playwright.sync_api import Page

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

_DISMISS_TIMEOUT_MS = 2_000
_UNLABELED_BUTTON = "button:not([data-qa])"


def hh_pro_modal_visible(page: Page) -> bool:
    try:
        payment_button = page.locator(HH_PRO_PAYMENT_BUTTON)
        dialog = page.get_by_role("dialog").filter(has=payment_button).first
        return dialog.count() > 0 and dialog.is_visible(timeout=0)
    except PlaywrightError:
        return False


def dismiss_hh_pro_modal(page: Page) -> bool:
    """Dismiss the known HH PRO sales modal and verify that it disappeared."""
    try:
        payment_button = page.locator(HH_PRO_PAYMENT_BUTTON)
        dialog = page.get_by_role("dialog").filter(has=payment_button).first
        if not dialog.count() or not dialog.is_visible(timeout=0):
            return False

        LOGGER.warning(
            "Обнаружено модальное окно hh PRO; закрываю его.",
            extra=event_data(LogEvent.MODAL),
        )
        page.keyboard.press("Escape")
        try:
            dialog.wait_for(state="hidden", timeout=_DISMISS_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            close_buttons = dialog.locator(_UNLABELED_BUTTON)
            if close_buttons.count() != 1 or not close_buttons.first.is_visible(timeout=0):
                LOGGER.warning(
                    "Не удалось однозначно найти кнопку закрытия окна hh PRO.",
                    extra=event_data(LogEvent.MODAL),
                )
                return False
            close_buttons.first.click(timeout=_DISMISS_TIMEOUT_MS)
            dialog.wait_for(state="hidden", timeout=_DISMISS_TIMEOUT_MS)
        LOGGER.info(
            "Модальное окно hh PRO закрыто; продолжаю работу.",
            extra=event_data(LogEvent.MODAL),
        )
        return True
    except PlaywrightError as error:
        LOGGER.warning(
            "Не удалось закрыть модальное окно hh PRO: %s",
            error.__class__.__name__,
            extra=event_data(LogEvent.MODAL),
        )
        return False
