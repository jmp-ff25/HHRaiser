from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from hh_raiser.models import MOSCOW


class VacancyResponseStatus(StrEnum):
    SENT = "sent"
    MANUAL_REQUIRED = "manual_required"
    ALREADY_SENT = "already_sent"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    ERROR = "error"


class ManualResponseReason(StrEnum):
    QUESTIONNAIRE = "questionnaire"
    COVER_LETTER = "cover_letter"
    EXTERNAL_SITE = "external_site"
    TEST = "test"
    RESUME_SELECTION = "resume_selection"
    AUTHENTICATION = "authentication"
    OTHER_FORM = "other_form"


@dataclass(frozen=True)
class VacancyResponseRecord:
    vacancy_id: str
    occurred_at: datetime
    status: VacancyResponseStatus
    detail: str
    vacancy_title: str
    company_name: str
    search_query: str
    match_score: int | None
    manual_reason: ManualResponseReason | None = None

    @classmethod
    def now(
        cls,
        *,
        vacancy_id: str,
        status: VacancyResponseStatus,
        detail: str,
        vacancy_title: str,
        company_name: str,
        search_query: str,
        match_score: int | None,
        manual_reason: ManualResponseReason | None = None,
    ) -> VacancyResponseRecord:
        return cls(
            vacancy_id=vacancy_id,
            occurred_at=datetime.now(MOSCOW),
            status=status,
            detail=detail,
            vacancy_title=vacancy_title,
            company_name=company_name,
            search_query=search_query,
            match_score=match_score,
            manual_reason=manual_reason,
        )
