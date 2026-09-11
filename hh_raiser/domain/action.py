from __future__ import annotations

from enum import StrEnum


class ActivityKind(StrEnum):
    OPEN_SEARCH = "open_search"
    REVIEW_SEARCH = "review_search"
    VIEW_VACANCY = "view_vacancy"
    RESPOND_VACANCY = "respond_vacancy"
    REVIEW_RESUME = "review_resume"
    REFRESH_RESUME_INDEX = "refresh_resume_index"
