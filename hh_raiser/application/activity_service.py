from __future__ import annotations

from typing import TYPE_CHECKING

from hh_raiser.activities.resume_review import review_resume
from hh_raiser.activities.search_page_viewer import view_search_page
from hh_raiser.activities.vacancy_viewer import view_vacancies
from hh_raiser.application.vacancy_rotation import VacancyRotation
from hh_raiser.domain.matching import VacancyCompatibilityMatcher
from hh_raiser.domain.policies import ActivityPolicy
from hh_raiser.domain.result import ActivityResult, ActivityStatus
from hh_raiser.infrastructure.hh.resume_reader import read_resume_text
from hh_raiser.infrastructure.storage.vacancy_history import VacancyHistory
from hh_raiser.logging_config import LOGGER

if TYPE_CHECKING:
    from playwright.sync_api import Page


def run_permitted_activities(
    page: Page,
    policy: ActivityPolicy,
    rotation: VacancyRotation,
    history: VacancyHistory,
    resume_title: str,
) -> list[ActivityResult]:
    results: list[ActivityResult] = []
    completed_urls: set[str] = set()
    viewed_count = 0
    generation_advanced = False
    matcher = None
    if policy.vacancy_matching:
        matcher = VacancyCompatibilityMatcher(
            resume_title=resume_title,
            resume_text=read_resume_text(page, resume_title),
            threshold=policy.match_threshold,
        )

    if policy.unique_vacancy_limit and history.viewed_count() >= policy.unique_vacancy_limit:
        generation = history.advance_generation()
        rotation.reset_coverage()
        generation_advanced = True
        LOGGER.info(
            "Достигнут лимит уникальных вакансий; начат цикл уникальных просмотров № %s.",
            generation,
        )

    for _ in range(policy.search_pages_per_cycle):
        if viewed_count >= policy.vacancies_per_cycle:
            break
        search = rotation.next_search()
        if search is None:
            if policy.reset_on_exhaustion and not generation_advanced:
                generation = history.advance_generation()
                rotation.reset_coverage()
                generation_advanced = True
                LOGGER.info(
                    "Все известные страницы проверены; начат цикл уникальных просмотров № %s.",
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
        candidates = history.reserve_unseen(
            vacancy_urls,
            search_query=query,
            limit=policy.vacancies_per_cycle - viewed_count,
            revisit_after_days=policy.revisit_after_days,
        )
        try:
            for outcome in view_vacancies(page, candidates, policy, matcher=matcher):
                results.append(outcome.result)
                if not outcome.url:
                    continue
                completed_urls.add(outcome.url)
                match_evaluated = outcome.result.metadata.get("match_evaluated") is True
                if match_evaluated:
                    history.mark_evaluated(
                        outcome.url,
                        score=int(outcome.result.metadata.get("match_score") or 0),
                        accepted=outcome.result.metadata.get("match_accepted") is True,
                    )
                if outcome.result.status is ActivityStatus.SUCCESS:
                    history.mark_viewed(outcome.url)
                    viewed_count += 1
                elif not match_evaluated:
                    history.release(outcome.url)
        finally:
            for url in candidates:
                if url not in completed_urls:
                    history.release(url)
    results.append(review_resume(page))
    return results
