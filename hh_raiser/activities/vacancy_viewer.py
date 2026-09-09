from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hh_raiser.browser import is_closed_playwright_error
from hh_raiser.domain.action import ActivityKind
from hh_raiser.domain.policies import ActivityPolicy
from hh_raiser.domain.result import ActivityResult, ActivityStatus
from hh_raiser.infrastructure.browser.modal_guard import dismiss_hh_pro_modal
from hh_raiser.infrastructure.browser.page_state_reader import canonical_vacancy_url
from hh_raiser.infrastructure.hh.selectors import VACANCY_DESCRIPTION, VACANCY_HEADING
from hh_raiser.logging_config import LOGGER

if TYPE_CHECKING:
    from playwright.sync_api import Page

from playwright.sync_api import Error as PlaywrightError


@dataclass(frozen=True)
class VacancyViewOutcome:
    url: str
    result: ActivityResult


def normalize_vacancy_title(value: str) -> str:
    return " ".join(value.split())[:200] or "название не распознано"


def view_vacancies(
    page: Page, vacancy_urls: list[str], policy: ActivityPolicy
) -> Iterator[VacancyViewOutcome]:
    yielded = False
    for index, url in enumerate(vacancy_urls, start=1):
        canonical = canonical_vacancy_url(url)
        if canonical is None:
            continue
        try:
            page.bring_to_front()
            page.goto(canonical, wait_until="domcontentloaded")
            dismiss_hh_pro_modal(page)
            heading = page.locator(VACANCY_HEADING)
            if not heading.count():
                heading = page.get_by_role("heading", level=1)
            if heading.count():
                heading.first.wait_for(state="visible", timeout=10_000)
            description = page.locator(VACANCY_DESCRIPTION)
            recognized = heading.count() > 0 and heading.first.is_visible()
            vacancy_title = (
                normalize_vacancy_title(heading.first.inner_text())
                if recognized
                else normalize_vacancy_title("")
            )
            LOGGER.info(
                "Открыта вакансия %s из %s: «%s».",
                index,
                len(vacancy_urls),
                vacancy_title,
            )
            description_visible = description.count() > 0 and description.first.is_visible()
            content_recognized = recognized and description_visible
            scrolls_completed = 0
            if description_visible:
                LOGGER.info(
                    "Просматриваю вакансию %s из %s: «%s».",
                    index,
                    len(vacancy_urls),
                    vacancy_title,
                )
                for _ in range(policy.vacancy_scrolls):
                    page.locator("body").press("PageDown")
                    scrolls_completed += 1
                    page.wait_for_timeout(round(policy.scroll_pause_seconds * 1_000))
            page.wait_for_timeout(round(policy.vacancy_view_seconds * 1_000))
            yielded = True
            yield VacancyViewOutcome(
                url=canonical,
                result=ActivityResult(
                    action=ActivityKind.VIEW_VACANCY,
                    status=(
                        ActivityStatus.SUCCESS if content_recognized else ActivityStatus.UNKNOWN
                    ),
                    detail=(
                        "Страница вакансии содержательно просмотрена."
                        if content_recognized
                        else "Страница открыта, но содержимое вакансии распознано не полностью."
                    ),
                    metadata={
                        "description_visible": description_visible,
                        "scrolls_completed": scrolls_completed,
                    },
                ),
            )
        except PlaywrightError as error:
            if is_closed_playwright_error(error):
                raise
            yielded = True
            yield VacancyViewOutcome(
                url=canonical,
                result=ActivityResult(
                    action=ActivityKind.VIEW_VACANCY,
                    status=ActivityStatus.ERROR,
                    detail=f"Не удалось просмотреть вакансию: {error.__class__.__name__}",
                ),
            )
    if not yielded:
        yield VacancyViewOutcome(
            url="",
            result=ActivityResult(
                action=ActivityKind.VIEW_VACANCY,
                status=ActivityStatus.SKIPPED,
                detail="Подходящие ссылки на вакансии не найдены.",
            ),
        )
