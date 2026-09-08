from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlencode

from hh_raiser.domain.action import ActivityKind
from hh_raiser.domain.policies import ActivityPolicy
from hh_raiser.domain.result import ActivityResult, ActivityStatus
from hh_raiser.infrastructure.browser.modal_guard import dismiss_hh_pro_modal
from hh_raiser.infrastructure.browser.page_state_reader import canonical_vacancy_url
from hh_raiser.infrastructure.hh.selectors import SEARCH_URL, VACANCY_CARD, VACANCY_TITLE_LINK

if TYPE_CHECKING:
    from playwright.sync_api import Page

from playwright.sync_api import Error as PlaywrightError


def view_search_page(
    page: Page, policy: ActivityPolicy, *, query: str, search_page: int
) -> tuple[ActivityResult, list[str]]:
    try:
        search_url = f"{SEARCH_URL}?{urlencode({'text': query, 'page': search_page})}"
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
                },
            ),
            collected,
        )
    except PlaywrightError as error:
        return (
            ActivityResult(
                action=ActivityKind.REVIEW_SEARCH,
                status=ActivityStatus.ERROR,
                detail=f"Не удалось исследовать выдачу: {error.__class__.__name__}",
            ),
            [],
        )
