from __future__ import annotations

from typing import TYPE_CHECKING

from hh_raiser.logging_config import LOGGER, LogEvent, event_data

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page

from playwright.sync_api import Error as PlaywrightError


def maximize_browser_window(context: BrowserContext, page: Page, *, headless: bool) -> bool:
    """Maximize a headed Chromium window using the current monitor's available area."""
    if headless:
        return False
    session = None
    try:
        page.bring_to_front()
        session = context.new_cdp_session(page)
        window = session.send("Browser.getWindowForTarget")
        session.send(
            "Browser.setWindowBounds",
            {
                "windowId": window["windowId"],
                "bounds": {"windowState": "maximized"},
            },
        )
        LOGGER.info(
            "Окно Chromium развёрнуто по доступной области монитора.",
            extra=event_data(LogEvent.BROWSER),
        )
        return True
    except (KeyError, PlaywrightError) as error:
        LOGGER.warning(
            "Не удалось автоматически развернуть окно Chromium: %s",
            error,
            extra=event_data(LogEvent.BROWSER),
        )
        return False
    finally:
        if session is not None:
            try:
                session.detach()
            except PlaywrightError:
                pass
