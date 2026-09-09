from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class VacancyRotation:
    """Keeps one continuous run varied without persisting vacancy identifiers."""

    queries: tuple[str, ...]
    pages_per_query: int = 20
    _position: int = 0
    _seen: set[str] = field(default_factory=set)

    def next_search(self) -> tuple[str, int]:
        if not self.queries:
            raise ValueError("at least one search query is required")
        query_index = self._position % len(self.queries)
        page = (self._position // len(self.queries)) % self.pages_per_query
        self._position = (self._position + 1) % (len(self.queries) * self.pages_per_query)
        return self.queries[query_index % len(self.queries)], page

    def select(self, urls: list[str], limit: int) -> list[str]:
        if limit <= 0:
            return []
        unique_urls = list(dict.fromkeys(urls))
        unseen = [url for url in unique_urls if url not in self._seen]
        if not unseen and unique_urls:
            self._seen.clear()
            unseen = unique_urls
        random.shuffle(unseen)
        selected = unseen[:limit]
        self._seen.update(selected)
        return selected

    def reset_pages(self) -> None:
        self._position = 0
