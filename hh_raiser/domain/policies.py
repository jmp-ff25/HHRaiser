from __future__ import annotations

from dataclasses import dataclass, field

from hh_raiser.config import DEFAULT_MATCHING_PROMPT
from hh_raiser.domain.search_filters import SearchFilters


@dataclass(frozen=True)
class ActivityPolicy:
    vacancies_per_cycle: int = 10
    search_pages_per_cycle: int = 25
    search_scrolls: int = 3
    vacancy_scrolls: int = 2
    scroll_pause_seconds: float = 1.5
    vacancy_view_seconds: float = 12.0
    vacancy_matching: bool = True
    match_threshold: int = 55
    matching_mode: str = "lexical"
    local_matching_model: str = "qwen3:1.7b"
    matching_prompt: str = DEFAULT_MATCHING_PROMPT
    matching_excluded_titles: tuple[str, ...] | None = None
    auto_respond: bool = False
    daily_response_limit: int = 0
    search_filters: SearchFilters = field(default_factory=SearchFilters)

    def __post_init__(self) -> None:
        if not 0 <= self.vacancies_per_cycle <= 25:
            raise ValueError("vacancies_per_cycle must be between 0 and 25")
        if not 1 <= self.search_pages_per_cycle <= 200:
            raise ValueError("search_pages_per_cycle must be between 1 and 200")
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
        if self.matching_mode not in {"lexical", "shadow", "semantic"}:
            raise ValueError("matching_mode must be lexical, shadow or semantic")
        if not self.local_matching_model.strip():
            raise ValueError("local_matching_model must not be empty")
        if not self.matching_prompt.strip() or len(self.matching_prompt) > 12_000:
            raise ValueError("matching_prompt must contain 1 to 12000 characters")
        if self.matching_excluded_titles is not None and any(
            not title.strip() or len(title) > 100 for title in self.matching_excluded_titles
        ):
            raise ValueError("matching_excluded_titles entries must contain 1 to 100 characters")
        if not 0 <= self.daily_response_limit <= 1_000:
            raise ValueError("daily_response_limit must be between 0 and 1000")
