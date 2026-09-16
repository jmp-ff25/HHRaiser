"""Сохранение итогов отклика независимо от сценария обхода вакансий."""

from hh_raiser.domain.result import ActivityResult
from hh_raiser.domain.vacancy_response import (
    ManualResponseReason,
    VacancyResponseRecord,
    VacancyResponseStatus,
)
from hh_raiser.infrastructure.storage.vacancy_history import VacancyHistory

_TERMINAL_RESPONSE_STATUSES = frozenset(
    {
        VacancyResponseStatus.SENT,
        VacancyResponseStatus.MANUAL_REQUIRED,
        VacancyResponseStatus.ALREADY_SENT,
        VacancyResponseStatus.UNKNOWN,
    }
)


def record_terminal_response(history: VacancyHistory, result: ActivityResult) -> None:
    """Сохранить исход, при котором повторный автоматический отклик запрещён.

    Результат отклика формируется Playwright-сценарием, а SQLite-хранилище не должно
    знать о его metadata. Эта функция является явной границей между ними и одинакова
    для обычного и группового обхода вакансий.
    """

    metadata = result.metadata
    vacancy_id = str(metadata.get("vacancy_id") or "")
    response_status = VacancyResponseStatus(str(metadata["response_status"]))
    if response_status not in _TERMINAL_RESPONSE_STATUSES or not vacancy_id:
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
