from __future__ import annotations

import configparser
import os
import re
from collections.abc import Mapping
from pathlib import Path

from hh_raiser.bot.models import BotSettings, ManagedInstance

TOKEN_ENVIRONMENT_VARIABLE = "HHRAISER_BOT_TOKEN"
USER_IDS_ENVIRONMENT_VARIABLE = "HHRAISER_BOT_ALLOWED_USER_IDS"
_INSTANCE_PREFIX = "instance:"
_INSTANCE_KEY = re.compile(r"[a-zA-Z0-9_-]{1,24}\Z")
_SERVICE_NAME = re.compile(r"[a-zA-Z0-9_.@-]+\.service\Z")


class BotConfigError(ValueError):
    """Raised when the local bot configuration is incomplete or unsafe."""


def load_bot_settings(
    path: Path,
    *,
    environment: Mapping[str, str] | None = None,
) -> BotSettings:
    """Read bot settings while keeping the Telegram token outside the INI file."""

    source_environment = environment if environment is not None else os.environ
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
    except OSError as error:
        raise BotConfigError(f"Не удалось прочитать настройки Telegram-бота: {path}") from error
    except configparser.Error as error:
        raise BotConfigError(f"Некорректный INI-файл Telegram-бота: {path}") from error

    token = source_environment.get(TOKEN_ENVIRONMENT_VARIABLE, "").strip()
    if not token:
        raise BotConfigError(
            f"Не задан токен Telegram-бота в переменной {TOKEN_ENVIRONMENT_VARIABLE}."
        )
    if not parser.has_section("telegram"):
        raise BotConfigError("В настройках отсутствует секция [telegram].")

    raw_user_ids = source_environment.get(USER_IDS_ENVIRONMENT_VARIABLE, "").strip()
    if not raw_user_ids:
        raw_user_ids = parser.get("telegram", "allowed_user_ids", fallback="")
    allowed_user_ids = _parse_user_ids(raw_user_ids)
    if not allowed_user_ids:
        raise BotConfigError("Не указан ни один разрешённый Telegram user ID.")

    try:
        log_lines = parser.getint("telegram", "log_lines", fallback=25)
        summary_interval_minutes = parser.getint("telegram", "summary_interval_minutes", fallback=0)
        user_systemd = parser.getboolean("telegram", "user_systemd", fallback=True)
    except ValueError as error:
        raise BotConfigError("Некорректное значение в секции [telegram].") from error
    if not 5 <= log_lines <= 100:
        raise BotConfigError("telegram.log_lines должно быть от 5 до 100.")
    if not 0 <= summary_interval_minutes <= 10_080:
        raise BotConfigError("telegram.summary_interval_minutes должно быть от 0 до 10080.")

    instances = _read_instances(parser, config_path=path.resolve())
    if not instances:
        raise BotConfigError("Добавьте хотя бы одну секцию [instance:имя].")
    return BotSettings(
        token=token,
        allowed_user_ids=allowed_user_ids,
        instances=instances,
        log_lines=log_lines,
        summary_interval_minutes=summary_interval_minutes,
        user_systemd=user_systemd,
    )


def _parse_user_ids(value: str) -> frozenset[int]:
    values = value.replace(",", " ").split()
    try:
        identifiers = frozenset(int(item) for item in values)
    except ValueError as error:
        raise BotConfigError("Telegram user ID должен быть целым положительным числом.") from error
    if any(identifier <= 0 for identifier in identifiers):
        raise BotConfigError("Telegram user ID должен быть целым положительным числом.")
    return identifiers


def _read_instances(
    parser: configparser.ConfigParser,
    *,
    config_path: Path,
) -> dict[str, ManagedInstance]:
    instances: dict[str, ManagedInstance] = {}
    for section in parser.sections():
        if not section.startswith(_INSTANCE_PREFIX):
            continue
        key = section.removeprefix(_INSTANCE_PREFIX).strip()
        if not _INSTANCE_KEY.fullmatch(key):
            raise BotConfigError(
                f"Некорректный идентификатор экземпляра {key!r}: используйте латиницу, "
                "цифры, дефис или подчёркивание."
            )
        name = parser.get(section, "name", fallback=key).strip()
        service_name = parser.get(section, "service", fallback="").strip()
        raw_state_dir = parser.get(section, "state_dir", fallback="").strip()
        if not name or len(name) > 60:
            raise BotConfigError(f"{section}.name должно содержать от 1 до 60 символов.")
        if not _SERVICE_NAME.fullmatch(service_name):
            raise BotConfigError(f"{section}.service должно быть допустимым именем .service.")
        if not raw_state_dir:
            raise BotConfigError(f"Не задано {section}.state_dir.")
        state_dir = Path(raw_state_dir).expanduser()
        if not state_dir.is_absolute():
            state_dir = config_path.parent / state_dir
        instances[key] = ManagedInstance(
            key=key,
            name=name,
            service_name=service_name,
            state_dir=state_dir.resolve(),
        )
    return instances
