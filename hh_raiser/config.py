from __future__ import annotations

import argparse
import configparser
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("hh-config.ini")


@dataclass(frozen=True)
class FileConfig:
    resume_title: str | None = None
    search_queries: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeSettings:
    resume_title: str
    search_queries: tuple[str, ...]


def parse_search_queries(value: str, *, separator: str = "\n") -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip() for item in value.split(separator) if item.strip()))


def read_file_config(path: Path) -> FileConfig:
    parser = configparser.RawConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
    except OSError as error:
        raise ValueError(f"Не удалось прочитать файл настроек: {path}") from error
    except configparser.Error as error:
        raise ValueError(f"Некорректный INI-файл настроек: {path}") from error

    resume_title = parser.get("resume", "title", fallback="").strip() or None
    search_queries = parse_search_queries(parser.get("activity", "search_queries", fallback=""))
    return FileConfig(resume_title=resume_title, search_queries=search_queries)


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
    return RuntimeSettings(resume_title=resume_title, search_queries=search_queries)
