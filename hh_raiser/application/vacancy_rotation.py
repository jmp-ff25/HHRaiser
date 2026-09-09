from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class VacancyRotation:
    """Produces shuffled query/page bags without repeating a page in one pass."""

    queries: tuple[str, ...]
    randomizer: random.Random = field(default_factory=random.Random)
    _query_bag: list[str] = field(default_factory=list)
    _known_page_counts: dict[str, int] = field(default_factory=dict)
    _scanned_pages: dict[str, set[int]] = field(default_factory=dict)

    def next_search(self) -> tuple[str, int] | None:
        if not self.queries:
            raise ValueError("at least one search query is required")
        for _ in range(2):
            if not self._query_bag:
                self._refill_query_bag()
            while self._query_bag:
                query = self._query_bag.pop()
                page = self._next_page(query)
                if page is not None:
                    return query, page
        return None

    def observe_search(self, query: str, page: int, page_count: int) -> None:
        self._known_page_counts[query] = max(page_count, 1)
        self._scanned_pages.setdefault(query, set()).add(page)

    def reset_coverage(self) -> None:
        self._query_bag.clear()
        self._scanned_pages.clear()

    def _refill_query_bag(self) -> None:
        self._query_bag = list(self.queries)
        self.randomizer.shuffle(self._query_bag)

    def _next_page(self, query: str) -> int | None:
        scanned = self._scanned_pages.setdefault(query, set())
        page_count = self._known_page_counts.get(query)
        if page_count is None:
            return 0 if 0 not in scanned else None
        candidates = [page for page in range(page_count) if page not in scanned]
        return self.randomizer.choice(candidates) if candidates else None
