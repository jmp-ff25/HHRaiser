from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Error as PlaywrightError

from hh_raiser.browser import is_closed_playwright_error
from hh_raiser.infrastructure.hh.selectors import PROFILE_URL, RESUME_CARD, RESUME_DIRECT_LINK
from hh_raiser.logging_config import LOGGER, LogEvent, event_data

if TYPE_CHECKING:
    from playwright.sync_api import Page


def read_resume_text(page: Page, resume_title: str) -> str:
    """Open only the uniquely selected resume and read its main content in memory."""
    matching_cards = []
    cards = page.locator(RESUME_CARD)
    for index in range(cards.count()):
        card = cards.nth(index)
        if card.get_by_role("heading", name=resume_title, exact=True).count() == 1:
            matching_cards.append(card)
    if len(matching_cards) != 1:
        LOGGER.warning(
            "Не найдено единственное резюме «%s»; сопоставление вакансий недоступно.",
            resume_title,
            extra=event_data(LogEvent.RESUME, resume_title=resume_title),
        )
        return ""
    links = matching_cards[0].locator(RESUME_DIRECT_LINK)
    if links.count() != 1:
        return ""
    resume_url = urlsplit(urljoin(PROFILE_URL, links.first.get_attribute("href") or ""))
    if (
        resume_url.scheme != "https"
        or resume_url.netloc != "hh.ru"
        or not resume_url.path.startswith("/resume/")
    ):
        return ""
    try:
        page.goto(resume_url.geturl(), wait_until="domcontentloaded")
        content = page.locator("main")
        if not content.count():
            return ""
        heading = content.first.get_by_role("heading", name=resume_title, exact=True)
        return content.first.inner_text() if heading.count() == 1 else ""
    except PlaywrightError as error:
        if is_closed_playwright_error(error):
            raise
        LOGGER.warning(
            "Не удалось прочитать страницу резюме «%s»: %s.",
            resume_title,
            error.__class__.__name__,
            extra=event_data(LogEvent.RESUME, resume_title=resume_title),
        )
        return ""
