from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from hh_raiser.activities.resume_review import review_resume
from hh_raiser.activities.search_page_viewer import view_search_page
from hh_raiser.activities.vacancy_responder import respond_to_vacancy
from hh_raiser.activities.vacancy_viewer import view_vacancies
from hh_raiser.application.response_history import record_terminal_response
from hh_raiser.application.vacancy_traversal import VacancyGroup, VacancyTraversal
from hh_raiser.domain.matching import VacancyCompatibilityMatcher
from hh_raiser.domain.policies import ActivityPolicy
from hh_raiser.domain.result import ActivityResult, ActivityStatus
from hh_raiser.infrastructure.browser.captcha_guard import CaptchaResolver
from hh_raiser.infrastructure.hh.resume_reader import read_resume_text
from hh_raiser.infrastructure.storage.vacancy_history import VacancyHistory, vacancy_id_from_url
from hh_raiser.logging_config import LOGGER, LogEvent, event_data

if TYPE_CHECKING:
    from playwright.sync_api import Page


_RESPONSE_OUTCOME_MESSAGES = {
    "sent": "отклик отправлен HHRaiser и подтверждён HH",
    "already_sent": "новый отклик не отправлен: HH подтвердил существующий отклик",
    "manual_required": "отклик не отправлен: требуется действие кандидата",
    "unavailable": "отклик недоступен на стороне HH",
    "unknown": "исход отклика не подтверждён, повтор не выполняется",
    "error": "отклик не завершён из-за технической ошибки",
}


def run_vacancy_page_group(
    page: Page,
    policy: ActivityPolicy,
    traversal: VacancyTraversal,
    history: VacancyHistory,
    resume_title: str,
    *,
    captcha_guard: CaptchaResolver | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> list[ActivityResult]:
    """Обработать одну группу, сохранив текущие запрос и позицию в пагинации."""

    if traversal.is_exhausted:
        return []

    results: list[ActivityResult] = []
    resume_text = traversal.resume_text
    if policy.vacancy_matching and not resume_text:
        resume_text = read_resume_text(page, resume_title)
        traversal.resume_text = resume_text
    group = traversal.pop_group()
    if group is None:
        group = _load_next_nonempty_group(
            page,
            policy,
            traversal,
            history,
            results,
            captcha_guard=captcha_guard,
            stop_requested=stop_requested,
        )
    if group is None:
        return _with_resume_review(
            page,
            results,
            captcha_guard=captcha_guard,
            stop_requested=stop_requested,
        )

    matcher = None
    if policy.vacancy_matching:
        matcher = VacancyCompatibilityMatcher(
            resume_title=resume_title,
            resume_text=resume_text,
            threshold=policy.match_threshold,
        )

    LOGGER.info(
        "Обрабатываю группу %s из %s: запрос «%s», страница %s из %s, вакансий — %s.",
        group.group_index,
        group.group_count,
        group.query,
        group.page + 1,
        group.page_count,
        len(group.urls),
        extra=event_data(
            LogEvent.SEARCH,
            search_query=group.query,
            search_page=group.page,
            group_index=group.group_index,
            group_count=group.group_count,
            selected_count=len(group.urls),
        ),
    )

    daily_limit_reported = False
    for position, url in enumerate(group.urls, start=1):
        vacancy_id = vacancy_id_from_url(url)
        known_status = history.response_status(url)
        LOGGER.info(
            "Вакансия %s из %s определена: ID %s, отклик в БД — %s.",
            position,
            len(group.urls),
            vacancy_id or "не распознан",
            known_status.value if known_status else "нет",
            extra=event_data(
                LogEvent.VACANCY_OPEN,
                vacancy_index=position,
                vacancy_id=vacancy_id,
                vacancy_url=url,
                response_in_history=known_status is not None,
            ),
        )

        if known_status is not None:
            LOGGER.info(
                "Анализ вакансии ID %s пропущен: отклик уже учтён в БД (%s).",
                vacancy_id or "не распознан",
                known_status.value,
                extra=event_data(
                    LogEvent.VACANCY_MATCH,
                    vacancy_id=vacancy_id,
                    response_status=known_status.value,
                ),
            )

        view_options: dict[str, object] = {
            "matcher": None if known_status is not None else matcher,
            "view_below_threshold": True,
            "display_index": position,
            "display_total": len(group.urls),
        }
        if captcha_guard is not None or stop_requested is not None:
            view_options.update(
                captcha_guard=captcha_guard,
                stop_requested=stop_requested,
            )
        outcomes = list(view_vacancies(page, [url], policy, **view_options))
        if not outcomes:
            continue
        outcome = outcomes[0]
        result = replace(
            outcome.result,
            metadata={
                **outcome.result.metadata,
                "search_query": group.query,
                "search_page": group.page,
                "group_index": group.group_index,
            },
        )
        results.append(result)
        if result.status is ActivityStatus.SUCCESS:
            history.mark_viewed(outcome.url)
            if result.metadata.get("match_evaluated") is True:
                score = result.metadata.get("match_score")
                accepted = result.metadata.get("match_accepted")
                if isinstance(score, int) and isinstance(accepted, bool):
                    history.mark_evaluated(outcome.url, score=score, accepted=accepted)

        if known_status is not None:
            LOGGER.info(
                "Отклик на вакансию ID %s пропущен: результат уже учтён в БД (%s).",
                vacancy_id or "не распознан",
                known_status.value,
                extra=event_data(
                    LogEvent.RESPONSE_MANUAL,
                    vacancy_id=vacancy_id,
                    response_status=known_status.value,
                ),
            )
            continue

        match_accepted = (
            not policy.vacancy_matching or result.metadata.get("match_accepted") is True
        )
        if result.status is not ActivityStatus.SUCCESS:
            LOGGER.info(
                "Отклик на вакансию ID %s не выполняется: страница не распознана полностью.",
                vacancy_id or "не распознан",
                extra=event_data(LogEvent.RESPONSE_MANUAL, vacancy_id=vacancy_id),
            )
            continue
        if not match_accepted:
            match_score = result.metadata.get("match_score")
            if match_score is None:
                LOGGER.info(
                    "Отклик на вакансию ID %s не выполняется: "
                    "текст резюме недоступен для сопоставления.",
                    vacancy_id or "не распознан",
                    extra=event_data(LogEvent.VACANCY_MATCH, vacancy_id=vacancy_id),
                )
                continue
            LOGGER.info(
                "Отклик на вакансию ID %s не выполняется: соответствие %s%% ниже порога %s%%.",
                vacancy_id or "не распознан",
                match_score,
                policy.match_threshold,
                extra=event_data(
                    LogEvent.VACANCY_MATCH,
                    vacancy_id=vacancy_id,
                    match_score=result.metadata.get("match_score"),
                    match_threshold=policy.match_threshold,
                ),
            )
            continue
        if not policy.auto_respond:
            LOGGER.info(
                "Отклик на вакансию ID %s отключён настройкой responses.enabled.",
                vacancy_id or "не распознан",
                extra=event_data(LogEvent.RESPONSE_MANUAL, vacancy_id=vacancy_id),
            )
            continue

        daily_limit_reached = (
            policy.daily_response_limit > 0
            and history.sent_response_count_today() >= policy.daily_response_limit
        )
        if daily_limit_reached:
            if not daily_limit_reported:
                daily_limit_reported = True
                LOGGER.info(
                    "Достигнут дневной лимит успешных откликов: %s. "
                    "Просмотр вакансий продолжается.",
                    policy.daily_response_limit,
                    extra=event_data(
                        LogEvent.SYSTEM,
                        daily_response_limit=policy.daily_response_limit,
                    ),
                )
            continue

        response_result = respond_to_vacancy(
            page,
            vacancy_url=outcome.url,
            vacancy_title=str(result.metadata.get("vacancy_title") or "название не распознано"),
        )
        response_result = replace(
            response_result,
            metadata={
                **response_result.metadata,
                "company_name": str(
                    result.metadata.get("company_name") or "компания не распознана"
                ),
                "search_query": group.query,
                "match_score": result.metadata.get("match_score"),
            },
        )
        results.append(response_result)
        record_terminal_response(history, response_result)
        response_status = str(response_result.metadata.get("response_status") or "unknown")
        LOGGER.info(
            "Результат отклика на вакансию ID %s: %s.",
            vacancy_id or "не распознан",
            _response_outcome_message(response_status),
            extra=event_data(
                LogEvent.RESPONSE_MANUAL,
                vacancy_id=vacancy_id,
                response_status=response_status,
            ),
        )

    return _with_resume_review(
        page,
        results,
        captcha_guard=captcha_guard,
        stop_requested=stop_requested,
    )


def _load_next_nonempty_group(
    page: Page,
    policy: ActivityPolicy,
    traversal: VacancyTraversal,
    history: VacancyHistory,
    results: list[ActivityResult],
    *,
    captcha_guard: CaptchaResolver | None,
    stop_requested: Callable[[], bool] | None,
) -> VacancyGroup | None:
    initial_cycle = traversal.cycle
    while traversal.cycle == initial_cycle:
        request = traversal.next_search()
        if request is None:
            return None
        if request.cycle != initial_cycle:
            _finish_search_cycle(policy, traversal, history)
            return None
        search_options: dict[str, object] = {
            "query": request.query,
            "search_page": request.page,
        }
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
        results.append(
            replace(
                search_result,
                metadata={
                    **search_result.metadata,
                    "search_query": request.query,
                    "search_cycle": request.cycle,
                },
            )
        )
        reservation = history.reserve_candidates(
            vacancy_urls,
            search_query=request.query,
            limit=policy.unique_vacancy_limit or len(vacancy_urls),
            revisit_after_days=policy.revisit_after_days,
        )
        history.record_search_page(
            search_query=request.query,
            page=request.page,
            page_count=page_count,
            reservation=reservation,
        )
        group_count = traversal.observe_search(
            request,
            page_count=page_count,
            urls=list(reservation.urls),
            group_size=max(policy.vacancies_per_cycle, 1),
        )
        LOGGER.info(
            "Поиск «%s»: страница %s из %s; найдено вакансий — %s, новых — %s, "
            "доступны для обработки — %s, выбраны — %s, сформировано групп — %s.",
            request.query,
            request.page + 1,
            traversal.known_page_count,
            len(vacancy_urls),
            reservation.newly_discovered_count,
            reservation.eligible_count,
            len(reservation.urls),
            group_count,
            extra=event_data(
                LogEvent.SEARCH,
                search_query=request.query,
                search_page=request.page,
                search_page_count=traversal.known_page_count,
                discovered_count=len(vacancy_urls),
                newly_discovered_count=reservation.newly_discovered_count,
                eligible_count=reservation.eligible_count,
                selected_count=len(reservation.urls),
                group_count=group_count,
                search_cycle=request.cycle,
            ),
        )
        group = traversal.pop_group()
        if group is not None:
            return group
    return None


def _finish_search_cycle(
    policy: ActivityPolicy,
    traversal: VacancyTraversal,
    history: VacancyHistory,
) -> None:
    """Закончить полный обход и применить правило автосброса истории."""

    if not policy.reset_on_exhaustion:
        traversal.stop()
        LOGGER.info(
            "Все поисковые запросы и их страницы пройдены; новый обход отключён "
            "настройкой activity.reset_on_exhaustion.",
            extra=event_data(LogEvent.SEARCH, search_cycle=traversal.cycle),
        )
        return

    generation = history.advance_generation()
    LOGGER.info(
        "Все поисковые запросы и их страницы пройдены; начинается обход № %s "
        "с новым поколением истории № %s.",
        traversal.cycle,
        generation,
        extra=event_data(
            LogEvent.SEARCH,
            search_cycle=traversal.cycle,
            vacancy_generation=generation,
        ),
    )


def _with_resume_review(
    page: Page,
    results: list[ActivityResult],
    *,
    captcha_guard: CaptchaResolver | None,
    stop_requested: Callable[[], bool] | None,
) -> list[ActivityResult]:
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


def _response_outcome_message(response_status: str) -> str:
    """Вернуть однозначное объяснение терминального исхода отклика для журнала."""

    return _RESPONSE_OUTCOME_MESSAGES.get(response_status, "получен неизвестный исход")
