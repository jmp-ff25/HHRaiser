"""Загрузка приватных переменных HHRaiser из локального файла ``.env``."""

from __future__ import annotations

import os
import re
from collections.abc import MutableMapping
from pathlib import Path

DEFAULT_ENV_PATH = Path(".env")
_ENVIRONMENT_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class EnvFileError(ValueError):
    """Файл .env невозможно безопасно прочитать."""


def load_env_file(
    path: Path = DEFAULT_ENV_PATH,
    *,
    environment: MutableMapping[str, str] | None = None,
) -> None:
    """Добавить значения из ``path`` в окружение, не заменяя внешние переменные."""
    if not path.exists():
        return
    if not path.is_file():
        raise EnvFileError(f"Файл секретов не является обычным файлом: {path}")
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as error:
        raise EnvFileError(f"Не удалось прочитать файл секретов: {path}") from error

    target = environment if environment is not None else os.environ
    for line_number, source_line in enumerate(source.splitlines(), start=1):
        line = source_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not _ENVIRONMENT_KEY.fullmatch(key):
            raise EnvFileError(f"Некорректная строка {line_number} в файле секретов {path}.")
        target.setdefault(key, _unquote(value.strip()))


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
