from __future__ import annotations

import random
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field


@dataclass
class VacancyRotation:
    """Produces shuffled query/page bags without repeating a page in one pass."""

    queries: tuple[str, ...]
    randomizer: random.Random = field(default_factory=random.Random)
    _query_bag: list[str] = field(default_factory=list)
    _fresh_query_bag: list[str] = field(default_factory=list)
    _known_page_counts: dict[str, int] = field(default_factory=dict)
    _scanned_pages: dict[str, set[int]] = field(default_factory=dict)

    def begin_cycle(self) -> None:
        """Schedule one fresh first-page check for every query in this activity cycle."""

        self._fresh_query_bag = list(self.queries)
        self.randomizer.shuffle(self._fresh_query_bag)

    def allocate_slots(self, slot_count: int) -> dict[str, int]:
        """Distribute vacancy slots fairly while randomizing who receives the remainder."""

        if slot_count < 0:
            raise ValueError("slot_count must not be negative")
        if not self.queries:
            raise ValueError("at least one search query is required")
        ordered_queries = list(self.queries)
        self.randomizer.shuffle(ordered_queries)
        base, remainder = divmod(slot_count, len(ordered_queries))
        return {query: base + int(index < remainder) for index, query in enumerate(ordered_queries)}

    def restore_coverage(
        self,
        coverage: Mapping[str, tuple[int, AbstractSet[int]]],
    ) -> None:
        """Restore current-generation page coverage persisted by the vacancy history."""

        configured = set(self.queries)
        self._known_page_counts = {
            query: max(int(page_count), 1)
            for query, (page_count, _pages) in coverage.items()
            if query in configured
        }
        self._scanned_pages = {
            query: {int(page) for page in pages if int(page) >= 0}
            for query, (_page_count, pages) in coverage.items()
            if query in configured
        }
        self._query_bag.clear()

    def next_search(
        self,
        allowed_queries: AbstractSet[str] | None = None,
    ) -> tuple[str, int] | None:
        if not self.queries:
            raise ValueError("at least one search query is required")
        allowed = set(self.queries) if allowed_queries is None else set(allowed_queries)
        allowed.intersection_update(self.queries)
        if not allowed:
            return None

        fresh_query = self._pop_allowed(self._fresh_query_bag, allowed)
        if fresh_query is not None:
            return fresh_query, 0

        for _ in range(2):
            if not self._query_bag:
                self._refill_query_bag()
            attempts_remaining = sum(query in allowed for query in self._query_bag)
            while attempts_remaining:
                query = self._pop_allowed(self._query_bag, allowed)
                if query is None:
                    break
                attempts_remaining -= 1
                page = self._next_page(query)
                if page is not None:
                    return query, page
        return None

    def observe_search(self, query: str, page: int, page_count: int) -> None:
        self._known_page_counts[query] = max(
            self._known_page_counts.get(query, 1),
            page_count,
            1,
        )
        self._scanned_pages.setdefault(query, set()).add(page)

    def reset_coverage(self) -> None:
        self._query_bag.clear()
        self._fresh_query_bag.clear()
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
        if not candidates:
            return None
        weights = [1 / ((page + 1) ** 1.25) for page in candidates]
        return self.randomizer.choices(candidates, weights=weights, k=1)[0]

    @staticmethod
    def _pop_allowed(values: list[str], allowed: set[str]) -> str | None:
        for index in range(len(values) - 1, -1, -1):
            if values[index] in allowed:
                return values.pop(index)
        return None
