from __future__ import annotations

from dataclasses import dataclass, replace
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
    area_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.excluded_words) > 50:
            raise ValueError("excluded_words cannot contain more than 50 values")
        for word in self.excluded_words:
            if not word.strip() or len(word) > 100 or any(char in word for char in "\r\n"):
                raise ValueError("excluded_words must contain short single-line values")
        if len(self.areas) > 50:
            raise ValueError("areas cannot contain more than 50 values")
        for area in self.areas:
            if not area.strip() or len(area) > 150 or any(char in area for char in "\r\n"):
                raise ValueError("areas must contain short single-line region names")
        if any(not area_id.isdecimal() or int(area_id) <= 0 for area_id in self.area_ids):
            raise ValueError("area_ids must contain positive HH region IDs")

    def with_area_ids(self, area_ids: tuple[str, ...]) -> SearchFilters:
        """Return the same user filters with runtime-resolved HH region IDs."""
        return replace(self, area_ids=area_ids)
