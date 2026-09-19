from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SearchRequest:
    """Одна страница выдачи, выбранная циклическим обходом."""

    query: str
    page: int
    cycle: int


@dataclass(frozen=True)
class VacancyGroup:
    """Одна группа вакансий из перемешанной страницы выдачи HH."""

    query: str
    page: int
    page_count: int
    group_index: int
    group_count: int
    urls: tuple[str, ...]
    cycle: int


@dataclass
class VacancyTraversal:
    """Циклически обойти запросы и страницы в перемешанном порядке."""

    queries: tuple[str, ...]
    page_limit: int = 200
    randomizer: random.Random = field(default_factory=random.Random)
    resume_text: str = ""
    _query_index: int = 0
    _cycle: int = 1
    _known_page_count: int | None = None
    _seen_pages: set[int] = field(default_factory=set)
    _page_bag: list[int] = field(default_factory=list)
    _groups: list[VacancyGroup] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.queries:
            raise ValueError("at least one search query is required")
        if self.page_limit <= 0:
            raise ValueError("page_limit must be positive")

    @property
    def cycle(self) -> int:
        return self._cycle

    @property
    def current_query(self) -> str:
        return self.queries[self._query_index]

    @property
    def known_page_count(self) -> int:
        """Вернуть число страниц текущего запроса с учётом пользовательского лимита."""

        return self._known_page_count or 1

    def pop_group(self) -> VacancyGroup | None:
        return self._groups.pop(0) if self._groups else None

    def next_search(self) -> SearchRequest:
        if self._groups:
            raise RuntimeError("finish queued vacancy groups before selecting another page")
        if self._known_page_count is None:
            return SearchRequest(self.current_query, 0, self._cycle)
        if not self._page_bag:
            self._advance_query()
            return SearchRequest(self.current_query, 0, self._cycle)
        return SearchRequest(self.current_query, self._page_bag.pop(), self._cycle)

    def observe_search(
        self,
        request: SearchRequest,
        *,
        page_count: int,
        urls: list[str],
        group_size: int,
    ) -> int:
        """Запомнить пагинацию и разделить перемешанные URL на равные группы."""

        if request.query != self.current_query:
            raise ValueError("search result does not belong to the current query")
        if group_size <= 0:
            raise ValueError("group_size must be positive")

        normalized_page_count = min(
            max(int(page_count), request.page + 1, 1),
            self.page_limit,
        )
        self._seen_pages.add(request.page)
        self._known_page_count = max(self._known_page_count or 1, normalized_page_count)
        missing_pages = [
            page
            for page in range(self._known_page_count)
            if page not in self._seen_pages and page not in self._page_bag
        ]
        self.randomizer.shuffle(missing_pages)
        self._page_bag.extend(missing_pages)

        shuffled_urls = list(dict.fromkeys(urls))
        self.randomizer.shuffle(shuffled_urls)
        group_count = math.ceil(len(shuffled_urls) / group_size) if shuffled_urls else 0
        if not group_count:
            return 0

        base_size, larger_groups = divmod(len(shuffled_urls), group_count)
        offset = 0
        for index in range(group_count):
            size = base_size + int(index < larger_groups)
            group_urls = tuple(shuffled_urls[offset : offset + size])
            offset += size
            self._groups.append(
                VacancyGroup(
                    query=request.query,
                    page=request.page,
                    page_count=self._known_page_count,
                    group_index=index + 1,
                    group_count=group_count,
                    urls=group_urls,
                    cycle=request.cycle,
                )
            )
        return group_count

    def _advance_query(self) -> None:
        self._query_index += 1
        if self._query_index >= len(self.queries):
            self._query_index = 0
            self._cycle += 1
        self._known_page_count = None
        self._seen_pages.clear()
        self._page_bag.clear()
