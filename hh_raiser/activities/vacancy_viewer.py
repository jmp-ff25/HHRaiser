from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hh_raiser.browser import is_closed_playwright_error
from hh_raiser.domain.action import ActivityKind
from hh_raiser.domain.matching import VacancyCompatibilityMatcher, VacancyDocument
from hh_raiser.domain.policies import ActivityPolicy
from hh_raiser.domain.result import ActivityResult, ActivityStatus
from hh_raiser.infrastructure.browser.modal_guard import dismiss_hh_pro_modal
from hh_raiser.infrastructure.browser.page_state_reader import canonical_vacancy_url
from hh_raiser.infrastructure.hh.selectors import (
    VACANCY_COMPANY_NAME,
    VACANCY_DESCRIPTION,
    VACANCY_HEADING,
    VACANCY_SKILL,
)
from hh_raiser.logging_config import LOGGER, LogEvent, event_data

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
    page: Page,
    vacancy_urls: list[str],
    policy: ActivityPolicy,
    *,
    matcher: VacancyCompatibilityMatcher | None = None,
) -> Iterator[VacancyViewOutcome]:
    yielded = False
    for index, url in enumerate(vacancy_urls, start=1):
        canonical = canonical_vacancy_url(url)
        if canonical is None:
            continue
        vacancy_title = normalize_vacancy_title("")
        company_name = "компания не распознана"
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
            company = page.locator(VACANCY_COMPANY_NAME)
            if company.count() > 0 and company.first.is_visible():
                company_name = normalize_vacancy_title(company.first.inner_text())
            LOGGER.info(
                "Открыта вакансия %s из %s: «%s».",
                index,
                len(vacancy_urls),
                vacancy_title,
                extra=event_data(
                    LogEvent.VACANCY_OPEN,
                    vacancy_index=index,
                    vacancy_title=vacancy_title,
                    vacancy_url=canonical,
                ),
            )
            description_visible = description.count() > 0 and description.first.is_visible()
            content_recognized = recognized and description_visible
            assessment = None
            if content_recognized and matcher is not None:
                assessment = matcher.evaluate(
                    VacancyDocument(
                        title=vacancy_title,
                        description=description.first.inner_text(),
                        skills=tuple(page.locator(VACANCY_SKILL).all_inner_texts()),
                    )
                )
                if assessment.applied:
                    LOGGER.info(
                        "Соответствие вакансии %s из %s «%s»: %s%% (порог %s%%).",
                        index,
                        len(vacancy_urls),
                        vacancy_title,
                        assessment.score,
                        policy.match_threshold,
                        extra=event_data(
                            LogEvent.VACANCY_MATCH,
                            match_score=assessment.score,
                            match_threshold=policy.match_threshold,
                            vacancy_title=vacancy_title,
                            vacancy_url=canonical,
                        ),
                    )
                    if not assessment.accepted:
                        yielded = True
                        yield VacancyViewOutcome(
                            url=canonical,
                            result=ActivityResult(
                                action=ActivityKind.VIEW_VACANCY,
                                status=ActivityStatus.SKIPPED,
                                detail=(
                                    "Вакансия не прошла проверку соответствия и не просмотрена."
                                ),
                                metadata={
                                    "match_evaluated": True,
                                    "match_score": assessment.score,
                                    "match_accepted": False,
                                    "title_similarity": round(
                                        assessment.title_similarity,
                                        4,
                                    ),
                                    "bm25f_relevance": round(
                                        assessment.bm25f_relevance,
                                        4,
                                    ),
                                    "skills_coverage": round(
                                        assessment.skills_coverage,
                                        4,
                                    ),
                                    "lexical_similarity": round(
                                        assessment.lexical_similarity,
                                        4,
                                    ),
                                    "scrolls_completed": 0,
                                    "vacancy_title": vacancy_title,
                                    "company_name": company_name,
                                },
                            ),
                        )
                        continue
                else:
                    LOGGER.warning(
                        "Сопоставление вакансии «%s» пропущено: текст резюме недоступен.",
                        vacancy_title,
                        extra=event_data(
                            LogEvent.VACANCY_MATCH,
                            vacancy_title=vacancy_title,
                            vacancy_url=canonical,
                        ),
                    )
            scrolls_completed = 0
            if description_visible:
                LOGGER.info(
                    "Просматриваю вакансию %s из %s: «%s».",
                    index,
                    len(vacancy_urls),
                    vacancy_title,
                    extra=event_data(
                        LogEvent.VACANCY_VIEW,
                        vacancy_index=index,
                        vacancy_title=vacancy_title,
                        vacancy_url=canonical,
                    ),
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
                        "match_evaluated": assessment is not None and assessment.applied,
                        "match_score": (
                            assessment.score
                            if assessment is not None and assessment.applied
                            else None
                        ),
                        "match_accepted": (
                            assessment.accepted
                            if assessment is not None and assessment.applied
                            else None
                        ),
                        "vacancy_title": vacancy_title,
                        "company_name": company_name,
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
                    metadata={
                        "vacancy_title": vacancy_title,
                        "company_name": company_name,
                    },
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
