from __future__ import annotations

import json
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import Request, urlopen

HH_AREAS_URL = "https://api.hh.ru/areas?locale=RU&host=hh.ru"
HH_USER_AGENT = "HHRaiser/0.1 (https://github.com/jmp-ff25/HHRaiser)"
MAX_CATALOG_BYTES = 5_000_000


class AreaResolutionError(ValueError):
    """Raised when current HH area data cannot resolve a configured region safely."""


@dataclass(frozen=True)
class AreaEntry:
    area_id: str
    name: str
    path: str


def _normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return " ".join(normalized.split())


def _flatten_areas(
    nodes: Sequence[Mapping[str, object]],
    *,
    parents: tuple[str, ...] = (),
) -> Iterable[AreaEntry]:
    for node in nodes:
        name = str(node.get("name") or "").strip()
        area_id = str(node.get("id") or "").strip()
        if not name or not area_id:
            continue
        path_parts = (*parents, name)
        yield AreaEntry(area_id=area_id, name=name, path=" / ".join(path_parts))
        children = node.get("areas")
        if isinstance(children, list):
            yield from _flatten_areas(children, parents=path_parts)


def resolve_area_names(
    names: tuple[str, ...],
    area_tree: Sequence[Mapping[str, object]],
) -> tuple[AreaEntry, ...]:
    """Resolve user-facing names against one current HH area tree."""
    entries = tuple(_flatten_areas(area_tree))
    resolved: list[AreaEntry] = []
    for requested_name in names:
        normalized = _normalize_name(requested_name)
        matches = [entry for entry in entries if _normalize_name(entry.name) == normalized]
        if not matches:
            matches = [entry for entry in entries if _normalize_name(entry.path) == normalized]
        if not matches:
            raise AreaResolutionError(
                f"Регион «{requested_name}» не найден в актуальном справочнике HH."
            )
        if len(matches) > 1:
            variants = "; ".join(entry.path for entry in matches[:5])
            raise AreaResolutionError(
                f"Название региона «{requested_name}» неоднозначно. "
                f"Укажите полный путь из справочника: {variants}."
            )
        if matches[0].area_id not in {entry.area_id for entry in resolved}:
            resolved.append(matches[0])
    return tuple(resolved)


def fetch_area_tree(*, timeout_seconds: float = 15.0) -> list[Mapping[str, object]]:
    """Download the current public HH area directory without credentials."""
    request = Request(
        HH_AREAS_URL,
        headers={"HH-User-Agent": HH_USER_AGENT, "User-Agent": HH_USER_AGENT},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read(MAX_CATALOG_BYTES + 1)
    except (OSError, URLError) as error:
        raise AreaResolutionError(
            "Не удалось получить актуальный справочник регионов HH. "
            "Проверьте подключение к интернету и повторите запуск."
        ) from error
    if len(payload) > MAX_CATALOG_BYTES:
        raise AreaResolutionError("Справочник регионов HH оказался неожиданно большим.")
    try:
        parsed = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AreaResolutionError("HH вернул некорректный справочник регионов.") from error
    if not isinstance(parsed, list):
        raise AreaResolutionError("HH вернул справочник регионов неизвестного формата.")
    return parsed


def resolve_current_areas(
    names: tuple[str, ...],
    *,
    fetcher: Callable[[], list[Mapping[str, object]]] = fetch_area_tree,
) -> tuple[AreaEntry, ...]:
    """Resolve configured names using a freshly downloaded HH directory."""
    return resolve_area_names(names, fetcher()) if names else ()
