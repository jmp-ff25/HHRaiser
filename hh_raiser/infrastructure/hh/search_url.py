from __future__ import annotations

from urllib.parse import urlencode

from hh_raiser.domain.search_filters import SearchFilters
from hh_raiser.infrastructure.hh.selectors import SEARCH_URL


def build_search_url(
    *,
    query: str,
    page: int,
    filters: SearchFilters,
) -> str:
    """Build an HH search URL from an allowlisted set of observed parameters."""
    if filters.areas and not filters.area_ids:
        raise ValueError("area names must be resolved to current HH IDs before search")
    parameters: list[tuple[str, str | int]] = [("text", query), ("page", page)]
    if filters.excluded_words:
        parameters.append(("excluded_text", ", ".join(filters.excluded_words)))
    parameters.extend(("search_field", field.value) for field in filters.search_fields)
    parameters.extend(("experience", level.value) for level in filters.experience)
    parameters.extend(("area", area_id) for area_id in filters.area_ids)
    return f"{SEARCH_URL}?{urlencode(parameters)}"
