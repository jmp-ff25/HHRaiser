from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from hh_raiser.browser import is_closed_playwright_error
from hh_raiser.domain.action import ActivityKind
from hh_raiser.domain.policies import ActivityPolicy
from hh_raiser.domain.result import ActivityResult, ActivityStatus
from hh_raiser.infrastructure.browser.modal_guard import dismiss_hh_pro_modal
from hh_raiser.infrastructure.browser.page_state_reader import canonical_vacancy_url
from hh_raiser.infrastructure.hh.search_url import build_search_url
from hh_raiser.infrastructure.hh.selectors import (
    PAGINATION_LINK,
    VACANCY_CARD,
    VACANCY_TITLE_LINK,
)

if TYPE_CHECKING:
    from playwright.sync_api import Page

from playwright.sync_api import Error as PlaywrightError


def pagination_page_count(hrefs: list[str], *, current_page: int) -> int:
    """Read the largest zero-based HH page parameter from semantic pagination links."""
    page_indexes = [current_page]
    for href in hrefs:
        try:
            page_value = parse_qs(urlsplit(href).query).get("page", [""])[0]
            page_indexes.append(int(page_value))
        except (TypeError, ValueError):
            continue
    return max(page_indexes) + 1


def view_search_page(
    page: Page, policy: ActivityPolicy, *, query: str, search_page: int
) -> tuple[ActivityResult, list[str], int]:
    try:
        search_url = build_search_url(
            query=query,
            page=search_page,
            filters=policy.search_filters,
        )
        page.goto(search_url, wait_until="domcontentloaded")
        dismiss_hh_pro_modal(page)
        cards = page.locator(VACANCY_CARD)
        links = page.locator(VACANCY_TITLE_LINK)
        collected: list[str] = []
        for index in range(policy.search_scrolls + 1):
            for link in links.all():
                href = link.get_attribute("href")
                canonical = canonical_vacancy_url(href or "")
                if canonical and canonical not in collected:
                    collected.append(canonical)
            if index < policy.search_scrolls:
                page.locator("body").press("PageDown")
                page.wait_for_timeout(round(policy.scroll_pause_seconds * 1_000))
        card_count = cards.count()
        pagination_hrefs = [
            href
            for link in page.locator(PAGINATION_LINK).all()
            if (href := link.get_attribute("href"))
        ]
        page_count = pagination_page_count(pagination_hrefs, current_page=search_page)
        status = ActivityStatus.SUCCESS if card_count and collected else ActivityStatus.UNKNOWN
        detail = (
            "Выдача открыта и карточки вакансий распознаны."
            if status is ActivityStatus.SUCCESS
            else "Выдача открыта, но карточки вакансий не распознаны."
        )
        return (
            ActivityResult(
                action=ActivityKind.REVIEW_SEARCH,
                status=status,
                detail=detail,
                metadata={
                    "card_count": card_count,
                    "vacancies_collected": len(collected),
                    "scrolls_completed": policy.search_scrolls,
                    "search_page": search_page,
                    "search_page_count": page_count,
                },
            ),
            collected,
            page_count,
        )
    except PlaywrightError as error:
        if is_closed_playwright_error(error):
            raise
        return (
            ActivityResult(
                action=ActivityKind.REVIEW_SEARCH,
                status=ActivityStatus.ERROR,
                detail=f"Не удалось исследовать выдачу: {error.__class__.__name__}",
            ),
            [],
            search_page + 1,
        )
