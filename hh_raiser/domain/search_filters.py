from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SearchField(StrEnum):
    VACANCY_NAME = "name"
    COMPANY_NAME = "company_name"
    DESCRIPTION = "description"


class ExperienceLevel(StrEnum):
    NO_EXPERIENCE = "noExperience"
    BETWEEN_ONE_AND_THREE = "between1And3"
    BETWEEN_THREE_AND_SIX = "between3And6"
    MORE_THAN_SIX = "moreThan6"


@dataclass(frozen=True)
class SearchFilters:
    """Validated, user-owned filters supported by the HH vacancy search."""

    excluded_words: tuple[str, ...] = ()
    search_fields: tuple[SearchField, ...] = ()
    experience: tuple[ExperienceLevel, ...] = ()
    areas: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.excluded_words) > 50:
            raise ValueError("excluded_words cannot contain more than 50 values")
        for word in self.excluded_words:
            if not word.strip() or len(word) > 100 or any(char in word for char in "\r\n"):
                raise ValueError("excluded_words must contain short single-line values")
        if len(self.areas) > 50:
            raise ValueError("areas cannot contain more than 50 values")
        if any(not area.isdecimal() or int(area) <= 0 for area in self.areas):
            raise ValueError("areas must contain positive HH region IDs")
