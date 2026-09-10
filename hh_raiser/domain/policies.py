from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActivityPolicy:
    vacancies_per_cycle: int = 10
    search_pages_per_cycle: int = 25
    unique_vacancy_limit: int = 1_000
    revisit_after_days: int = 14
    reset_on_exhaustion: bool = True
    search_scrolls: int = 3
    vacancy_scrolls: int = 2
    scroll_pause_seconds: float = 1.5
    vacancy_view_seconds: float = 12.0
    vacancy_matching: bool = True
    match_threshold: int = 55

    def __post_init__(self) -> None:
        if not 0 <= self.vacancies_per_cycle <= 25:
            raise ValueError("vacancies_per_cycle must be between 0 and 25")
        if not 1 <= self.search_pages_per_cycle <= 200:
            raise ValueError("search_pages_per_cycle must be between 1 and 200")
        if not 0 <= self.unique_vacancy_limit <= 100_000:
            raise ValueError("unique_vacancy_limit must be between 0 and 100000")
        if not 0 <= self.revisit_after_days <= 3_650:
            raise ValueError("revisit_after_days must be between 0 and 3650")
        if not 0 <= self.search_scrolls <= 20:
            raise ValueError("search_scrolls must be between 0 and 20")
        if not 0 <= self.vacancy_scrolls <= 10:
            raise ValueError("vacancy_scrolls must be between 0 and 10")
        if not 0 <= self.scroll_pause_seconds <= 60:
            raise ValueError("scroll_pause_seconds must be between 0 and 60")
        if not 0 <= self.vacancy_view_seconds <= 300:
            raise ValueError("vacancy_view_seconds must be between 0 and 300")
        if not 0 <= self.match_threshold <= 100:
            raise ValueError("match_threshold must be between 0 and 100")
