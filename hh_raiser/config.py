from __future__ import annotations

import argparse
import configparser
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("hh-config.ini")
DEFAULT_SEARCH_PAGES_PER_CYCLE = 25
DEFAULT_UNIQUE_VACANCY_LIMIT = 1_000
DEFAULT_REVISIT_AFTER_DAYS = 14
DEFAULT_RESET_ON_EXHAUSTION = True
DEFAULT_VACANCY_MATCHING = True
DEFAULT_MATCH_THRESHOLD = 55


@dataclass(frozen=True)
class FileConfig:
    resume_title: str | None = None
    search_queries: tuple[str, ...] = ()
    search_pages_per_cycle: int | None = None
    unique_vacancy_limit: int | None = None
    revisit_after_days: int | None = None
    reset_on_exhaustion: bool | None = None
    vacancy_matching: bool | None = None
    match_threshold: int | None = None


@dataclass(frozen=True)
class RuntimeSettings:
    resume_title: str
    search_queries: tuple[str, ...]
    search_pages_per_cycle: int
    unique_vacancy_limit: int
    revisit_after_days: int
    reset_on_exhaustion: bool
    vacancy_matching: bool
    match_threshold: int


def parse_search_queries(value: str, *, separator: str = "\n") -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip() for item in value.split(separator) if item.strip()))


def _validate_range(name: str, value: int, *, minimum: int, maximum: int) -> int:
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} должно быть от {minimum} до {maximum}")
    return value


def read_file_config(path: Path) -> FileConfig:
    parser = configparser.RawConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
    except OSError as error:
        raise ValueError(f"Не удалось прочитать файл настроек: {path}") from error
    except configparser.Error as error:
        raise ValueError(f"Некорректный INI-файл настроек: {path}") from error

    try:
        resume_title = parser.get("resume", "title", fallback="").strip() or None
        search_queries = parse_search_queries(parser.get("activity", "search_queries", fallback=""))
        search_pages_per_cycle = parser.getint("activity", "search_pages_per_cycle", fallback=None)
        unique_vacancy_limit = parser.getint("activity", "unique_vacancy_limit", fallback=None)
        revisit_after_days = parser.getint("activity", "revisit_after_days", fallback=None)
        reset_on_exhaustion = parser.getboolean("activity", "reset_on_exhaustion", fallback=None)
        vacancy_matching = parser.getboolean("matching", "enabled", fallback=None)
        match_threshold = parser.getint("matching", "threshold", fallback=None)
    except ValueError as error:
        raise ValueError(f"Некорректное значение в INI-файле настроек: {path}") from error
    return FileConfig(
        resume_title=resume_title,
        search_queries=search_queries,
        search_pages_per_cycle=search_pages_per_cycle,
        unique_vacancy_limit=unique_vacancy_limit,
        revisit_after_days=revisit_after_days,
        reset_on_exhaustion=reset_on_exhaustion,
        vacancy_matching=vacancy_matching,
        match_threshold=match_threshold,
    )


def resolve_runtime_settings(args: argparse.Namespace) -> RuntimeSettings:
    config_path = Path(args.config_file).expanduser().resolve()
    file_config = read_file_config(config_path) if config_path.is_file() else FileConfig()
    resume_title = (
        args.resume_title or os.environ.get("HH_RESUME_TITLE") or file_config.resume_title
    )
    cli_queries = tuple(args.search_query or ())
    environment_queries = parse_search_queries(
        os.environ.get("HH_SEARCH_QUERIES", ""), separator="|"
    )
    search_queries = cli_queries or environment_queries or file_config.search_queries
    search_pages_per_cycle = (
        getattr(args, "search_pages_per_cycle", None)
        if getattr(args, "search_pages_per_cycle", None) is not None
        else file_config.search_pages_per_cycle
    )
    unique_vacancy_limit = (
        getattr(args, "unique_vacancy_limit", None)
        if getattr(args, "unique_vacancy_limit", None) is not None
        else file_config.unique_vacancy_limit
    )
    revisit_after_days = (
        getattr(args, "revisit_after_days", None)
        if getattr(args, "revisit_after_days", None) is not None
        else file_config.revisit_after_days
    )
    reset_on_exhaustion = (
        getattr(args, "reset_on_exhaustion", None)
        if getattr(args, "reset_on_exhaustion", None) is not None
        else file_config.reset_on_exhaustion
    )
    vacancy_matching = (
        getattr(args, "vacancy_matching", None)
        if getattr(args, "vacancy_matching", None) is not None
        else file_config.vacancy_matching
    )
    match_threshold = (
        getattr(args, "match_threshold", None)
        if getattr(args, "match_threshold", None) is not None
        else file_config.match_threshold
    )

    if not resume_title:
        raise ValueError(
            "Не задано название резюме: укажите [resume] title в hh-config.ini "
            "или передайте --resume-title."
        )
    if args.full_activity and not search_queries:
        raise ValueError(
            "Для --full-activity задайте [activity] search_queries в hh-config.ini "
            "или передайте один или несколько --search-query."
        )
    resolved_search_pages = (
        search_pages_per_cycle
        if search_pages_per_cycle is not None
        else DEFAULT_SEARCH_PAGES_PER_CYCLE
    )
    resolved_unique_limit = (
        unique_vacancy_limit if unique_vacancy_limit is not None else DEFAULT_UNIQUE_VACANCY_LIMIT
    )
    resolved_revisit_days = (
        revisit_after_days if revisit_after_days is not None else DEFAULT_REVISIT_AFTER_DAYS
    )
    return RuntimeSettings(
        resume_title=resume_title,
        search_queries=search_queries,
        search_pages_per_cycle=_validate_range(
            "activity.search_pages_per_cycle",
            resolved_search_pages,
            minimum=1,
            maximum=200,
        ),
        unique_vacancy_limit=_validate_range(
            "activity.unique_vacancy_limit",
            resolved_unique_limit,
            minimum=0,
            maximum=100_000,
        ),
        revisit_after_days=_validate_range(
            "activity.revisit_after_days",
            resolved_revisit_days,
            minimum=0,
            maximum=3_650,
        ),
        reset_on_exhaustion=(
            reset_on_exhaustion if reset_on_exhaustion is not None else DEFAULT_RESET_ON_EXHAUSTION
        ),
        vacancy_matching=(
            vacancy_matching if vacancy_matching is not None else DEFAULT_VACANCY_MATCHING
        ),
        match_threshold=_validate_range(
            "matching.threshold",
            match_threshold if match_threshold is not None else DEFAULT_MATCH_THRESHOLD,
            minimum=0,
            maximum=100,
        ),
    )
