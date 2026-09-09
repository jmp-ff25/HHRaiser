from __future__ import annotations

from typing import TYPE_CHECKING

from hh_raiser.activities.resume_review import review_resume
from hh_raiser.activities.search_page_viewer import view_search_page
from hh_raiser.activities.vacancy_viewer import view_vacancies
from hh_raiser.application.vacancy_rotation import VacancyRotation
from hh_raiser.domain.policies import ActivityPolicy
from hh_raiser.domain.result import ActivityResult, ActivityStatus
from hh_raiser.infrastructure.storage.vacancy_history import VacancyHistory
from hh_raiser.logging_config import LOGGER

if TYPE_CHECKING:
    from playwright.sync_api import Page


def run_permitted_activities(
    page: Page,
    policy: ActivityPolicy,
    rotation: VacancyRotation,
    history: VacancyHistory,
) -> list[ActivityResult]:
    results: list[ActivityResult] = []
    selected_urls: list[str] = []
    generation_advanced = False

    if policy.unique_vacancy_limit and history.viewed_count() >= policy.unique_vacancy_limit:
        generation = history.advance_generation()
        rotation.reset_coverage()
        generation_advanced = True
        LOGGER.info(
            "Достигнут лимит уникальных вакансий; начато поколение истории %s.",
            generation,
        )

    for _ in range(policy.search_pages_per_cycle):
        if len(selected_urls) >= policy.vacancies_per_cycle:
            break
        search = rotation.next_search()
        if search is None:
            if policy.reset_on_exhaustion and not generation_advanced:
                generation = history.advance_generation()
                rotation.reset_coverage()
                generation_advanced = True
                LOGGER.info(
                    "Все известные страницы проверены; начато поколение истории %s.",
                    generation,
                )
                continue
            break

        query, search_page = search
        search_result, vacancy_urls, page_count = view_search_page(
            page, policy, query=query, search_page=search_page
        )
        results.append(search_result)
        rotation.observe_search(query, search_page, page_count)
        selected_urls.extend(
            history.reserve_unseen(
                vacancy_urls,
                search_query=query,
                limit=policy.vacancies_per_cycle - len(selected_urls),
                revisit_after_days=policy.revisit_after_days,
            )
        )

    completed_urls: set[str] = set()
    try:
        for outcome in view_vacancies(page, selected_urls, policy):
            results.append(outcome.result)
            if not outcome.url:
                continue
            completed_urls.add(outcome.url)
            if outcome.result.status is ActivityStatus.SUCCESS:
                history.mark_viewed(outcome.url)
            else:
                history.release(outcome.url)
    finally:
        for url in selected_urls:
            if url not in completed_urls:
                history.release(url)
    results.append(review_resume(page))
    return results
