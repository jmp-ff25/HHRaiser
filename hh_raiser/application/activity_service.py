from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from hh_raiser.activities.resume_review import review_resume
from hh_raiser.activities.search_page_viewer import view_search_page
from hh_raiser.activities.vacancy_responder import respond_to_vacancy
from hh_raiser.activities.vacancy_viewer import view_vacancies
from hh_raiser.application.vacancy_rotation import VacancyRotation
from hh_raiser.domain.matching import VacancyCompatibilityMatcher
from hh_raiser.domain.policies import ActivityPolicy
from hh_raiser.domain.result import ActivityResult, ActivityStatus
from hh_raiser.domain.vacancy_response import (
    ManualResponseReason,
    VacancyResponseRecord,
    VacancyResponseStatus,
)
from hh_raiser.infrastructure.browser.captcha_guard import CaptchaGuard
from hh_raiser.infrastructure.hh.resume_reader import read_resume_text
from hh_raiser.infrastructure.storage.vacancy_history import VacancyHistory
from hh_raiser.logging_config import LOGGER, LogEvent, event_data

if TYPE_CHECKING:
    from playwright.sync_api import Page


def run_permitted_activities(
    page: Page,
    policy: ActivityPolicy,
    rotation: VacancyRotation,
    history: VacancyHistory,
    resume_title: str,
    *,
    captcha_guard: CaptchaGuard | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> list[ActivityResult]:
    results: list[ActivityResult] = []
    completed_urls: set[str] = set()
    viewed_count = 0
    generation_advanced = False
    daily_response_limit_reported = False
    viewed_by_query: Counter[str] = Counter()
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
            extra=event_data(LogEvent.SEARCH, vacancy_generation=generation),
        )

    rotation.begin_cycle()
    query_quotas = rotation.allocate_slots(policy.vacancies_per_cycle)

    for _ in range(policy.search_pages_per_cycle):
        if viewed_count >= policy.vacancies_per_cycle:
            break
        quota_queries = {
            query for query, quota in query_quotas.items() if viewed_by_query[query] < quota
        }
        search = rotation.next_search(quota_queries)
        quota_limited = search is not None
        if search is None:
            # A narrow query may run out of pages before filling its fair share.
            # Borrow the unused slots so the activity cycle can still reach its target.
            search = rotation.next_search()
        if search is None:
            if policy.reset_on_exhaustion and not generation_advanced:
                generation = history.advance_generation()
                rotation.reset_coverage()
                rotation.begin_cycle()
                generation_advanced = True
                LOGGER.info(
                    "Все известные страницы проверены; начат цикл уникальных просмотров № %s.",
                    generation,
                    extra=event_data(LogEvent.SEARCH, vacancy_generation=generation),
                )
                continue
            break

        query, search_page = search
        search_options = {"query": query, "search_page": search_page}
        if captcha_guard is not None or stop_requested is not None:
            search_options.update(
                captcha_guard=captcha_guard,
                stop_requested=stop_requested,
            )
        search_result, vacancy_urls, page_count = view_search_page(
            page,
            policy,
            **search_options,
        )
        search_result = replace(
            search_result,
            metadata={**search_result.metadata, "search_query": query},
        )
        results.append(search_result)
        rotation.observe_search(query, search_page, page_count)
        remaining_total = policy.vacancies_per_cycle - viewed_count
        query_limit = remaining_total
        if quota_limited:
            query_limit = min(
                query_limit,
                max(query_quotas[query] - viewed_by_query[query], 0),
            )
        reservation = history.reserve_candidates(
            vacancy_urls,
            search_query=query,
            limit=query_limit,
            revisit_after_days=policy.revisit_after_days,
        )
        history.record_search_page(
            search_query=query,
            page=search_page,
            page_count=page_count,
            reservation=reservation,
        )
        LOGGER.info(
            "Поиск «%s»: страница %s из %s; найдено %s, впервые обнаружено %s, "
            "доступно по истории %s, отобрано %s.",
            query,
            search_page + 1,
            page_count,
            reservation.discovered_count,
            reservation.newly_discovered_count,
            reservation.eligible_count,
            len(reservation.urls),
            extra=event_data(
                LogEvent.SEARCH,
                search_query=query,
                search_page=search_page,
                search_page_count=page_count,
                discovered_count=reservation.discovered_count,
                newly_discovered_count=reservation.newly_discovered_count,
                eligible_count=reservation.eligible_count,
                selected_count=len(reservation.urls),
            ),
        )
        candidates = list(reservation.urls)
        try:
            view_options = {"matcher": matcher}
            if captcha_guard is not None or stop_requested is not None:
                view_options.update(
                    captcha_guard=captcha_guard,
                    stop_requested=stop_requested,
                )
            for outcome in view_vacancies(page, candidates, policy, **view_options):
                outcome = replace(
                    outcome,
                    result=replace(
                        outcome.result,
                        metadata={**outcome.result.metadata, "search_query": query},
                    ),
                )
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
                    viewed_by_query[query] += 1
                    daily_limit_reached = (
                        policy.auto_respond
                        and policy.daily_response_limit > 0
                        and history.sent_response_count_today() >= policy.daily_response_limit
                    )
                    if daily_limit_reached and not daily_response_limit_reported:
                        daily_response_limit_reported = True
                        LOGGER.info(
                            "Достигнут дневной лимит успешных откликов: %s. "
                            "Просмотр вакансий продолжается без новых откликов до следующего дня.",
                            policy.daily_response_limit,
                            extra=event_data(
                                LogEvent.SYSTEM,
                                daily_response_limit=policy.daily_response_limit,
                            ),
                        )
                    if (
                        policy.auto_respond
                        and not daily_limit_reached
                        and not history.has_response_record(outcome.url)
                    ):
                        response_result = respond_to_vacancy(
                            page,
                            vacancy_url=outcome.url,
                            vacancy_title=str(
                                outcome.result.metadata.get("vacancy_title")
                                or "название не распознано"
                            ),
                        )
                        response_result = replace(
                            response_result,
                            metadata={
                                **response_result.metadata,
                                "company_name": str(
                                    outcome.result.metadata.get("company_name")
                                    or "компания не распознана"
                                ),
                                "search_query": query,
                                "match_score": outcome.result.metadata.get("match_score"),
                            },
                        )
                        results.append(response_result)
                        _record_response(history, response_result)
                elif not match_evaluated:
                    history.release(outcome.url)
        finally:
            for url in candidates:
                if url not in completed_urls:
                    history.release(url)
    if captcha_guard is None and stop_requested is None:
        results.append(review_resume(page))
    else:
        results.append(
            review_resume(
                page,
                captcha_guard=captcha_guard,
                stop_requested=stop_requested,
            )
        )
    return results


def _record_response(history: VacancyHistory, result: ActivityResult) -> None:
    metadata = result.metadata
    vacancy_id = str(metadata.get("vacancy_id") or "")
    response_status = VacancyResponseStatus(str(metadata["response_status"]))
    if response_status not in {
        VacancyResponseStatus.SENT,
        VacancyResponseStatus.MANUAL_REQUIRED,
        VacancyResponseStatus.ALREADY_SENT,
        VacancyResponseStatus.UNKNOWN,
    }:
        return
    if not vacancy_id:
        return
    manual_reason_value = metadata.get("manual_reason")
    manual_reason = ManualResponseReason(str(manual_reason_value)) if manual_reason_value else None
    match_score_value = metadata.get("match_score")
    history.record_response(
        VacancyResponseRecord(
            vacancy_id=vacancy_id,
            occurred_at=result.occurred_at,
            status=response_status,
            detail=result.detail,
            vacancy_title=str(metadata.get("vacancy_title") or "название не распознано"),
            company_name=str(metadata.get("company_name") or "компания не распознана"),
            search_query=str(metadata.get("search_query") or ""),
            match_score=int(match_score_value) if match_score_value is not None else None,
            manual_reason=manual_reason,
            post_response_modal_text=(
                str(metadata["post_response_modal_text"])
                if metadata.get("post_response_modal_text")
                else None
            ),
        )
    )
