from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from hh_raiser.config import read_file_config
from hh_raiser.models import MOSCOW

_SECTION_PATTERN = re.compile(r"^\s*\[([^]]+)]\s*$")
_OPTION_PATTERN = re.compile(r"^\s*([^#;][^=:#]*?)\s*[=:]")
_MAX_BACKUPS = 10


class ConfigEditError(ValueError):
    """Raised when an allow-listed setting cannot be safely read or changed."""


class SettingKind(StrEnum):
    TEXT = "text"
    LIST = "list"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    CHOICE_LIST = "choice_list"


@dataclass(frozen=True)
class EditableSetting:
    """One explicitly allow-listed HHRaiser INI option exposed in Telegram."""

    key: str
    category: str
    label: str
    section: str
    option: str
    kind: SettingKind
    help_text: str
    minimum: int = 0
    maximum: int = 0
    max_items: int = 0
    choices: tuple[str, ...] = ()
    required: bool = False


@dataclass(frozen=True)
class ConfigChange:
    """Validated proposed change with optimistic-concurrency information."""

    setting: EditableSetting
    old_value: str
    new_value: str


CATEGORY_LABELS: dict[str, str] = {
    "resume": "📄 Резюме",
    "search": "🔎 Поиск",
    "filters": "🧰 Фильтры",
    "matching": "🎯 Соответствие",
    "responses": "✉️ Отклики",
}

EDITABLE_SETTINGS: tuple[EditableSetting, ...] = (
    EditableSetting(
        "resume_title",
        "resume",
        "Название резюме",
        "resume",
        "title",
        SettingKind.TEXT,
        "Введите точное название резюме на HH.",
        required=True,
    ),
    EditableSetting(
        "search_queries",
        "search",
        "Поисковые запросы",
        "activity",
        "search_queries",
        SettingKind.LIST,
        "Отправьте запросы, каждый с новой строки.",
        max_items=30,
        required=True,
    ),
    EditableSetting(
        "search_pages",
        "search",
        "Страниц за цикл",
        "activity",
        "search_pages_per_cycle",
        SettingKind.INTEGER,
        "Введите число от 1 до 200.",
        minimum=1,
        maximum=200,
    ),
    EditableSetting(
        "unique_limit",
        "search",
        "Лимит уникальных вакансий",
        "activity",
        "unique_vacancy_limit",
        SettingKind.INTEGER,
        "Введите число от 0 до 100000; 0 отключает лимит.",
        minimum=0,
        maximum=100_000,
    ),
    EditableSetting(
        "revisit_days",
        "search",
        "Повторный просмотр через дней",
        "activity",
        "revisit_after_days",
        SettingKind.INTEGER,
        "Введите число от 0 до 3650; 0 разрешает повтор без паузы.",
        minimum=0,
        maximum=3_650,
    ),
    EditableSetting(
        "reset_exhaustion",
        "search",
        "Новый цикл после исчерпания",
        "activity",
        "reset_on_exhaustion",
        SettingKind.BOOLEAN,
        "Включает новый цикл после проверки всех известных страниц.",
    ),
    EditableSetting(
        "excluded_words",
        "filters",
        "Исключающие слова",
        "search_filters",
        "excluded_words",
        SettingKind.LIST,
        "Отправьте слова, каждое с новой строки. Отправьте один дефис, чтобы очистить.",
        max_items=50,
    ),
    EditableSetting(
        "areas",
        "filters",
        "Регионы поиска",
        "search_filters",
        "areas",
        SettingKind.LIST,
        "Отправьте названия регионов, каждое с новой строки; например Москва.",
        max_items=50,
    ),
    EditableSetting(
        "search_fields",
        "filters",
        "Области поиска текста",
        "search_filters",
        "search_fields",
        SettingKind.CHOICE_LIST,
        "Допустимые значения: name, company_name, description. По одному на строку.",
        max_items=3,
        choices=("name", "company_name", "description"),
    ),
    EditableSetting(
        "experience",
        "filters",
        "Опыт работы",
        "search_filters",
        "experience",
        SettingKind.CHOICE_LIST,
        "Допустимо: noExperience, between1And3, between3And6, moreThan6.",
        max_items=4,
        choices=("noExperience", "between1And3", "between3And6", "moreThan6"),
    ),
    EditableSetting(
        "matching_enabled",
        "matching",
        "Оценивать соответствие",
        "matching",
        "enabled",
        SettingKind.BOOLEAN,
        "Включает оценку соответствия вакансии резюме.",
    ),
    EditableSetting(
        "match_threshold",
        "matching",
        "Порог соответствия",
        "matching",
        "threshold",
        SettingKind.INTEGER,
        "Введите процент от 0 до 100.",
        minimum=0,
        maximum=100,
    ),
    EditableSetting(
        "responses_enabled",
        "responses",
        "Автоматические отклики",
        "responses",
        "enabled",
        SettingKind.BOOLEAN,
        "Разрешает только простые отклики без анкет, тестов и обязательных писем.",
    ),
    EditableSetting(
        "daily_response_limit",
        "responses",
        "Откликов в день",
        "responses",
        "daily_limit",
        SettingKind.INTEGER,
        "Введите максимум успешных откликов от 0 до 1000; 0 означает без ограничения.",
        minimum=0,
        maximum=1_000,
    ),
)

SETTINGS_BY_KEY = {setting.key: setting for setting in EDITABLE_SETTINGS}


class IniConfigStore:
    """Safely edit one allow-listed INI file and retain rollback information."""

    def __init__(self, config_file: Path, state_dir: Path) -> None:
        self.config_file = config_file.resolve()
        self.state_dir = state_dir.resolve()
        if not self.config_file.is_relative_to(self.state_dir):
            raise ConfigEditError("Изменяемый конфиг должен находиться внутри state_dir.")
        self._lock = threading.RLock()

    def read_value(self, setting: EditableSetting) -> str:
        parser = self._read_parser()
        return parser.get(setting.section, setting.option, fallback="").strip()

    def prepare_change(self, setting_key: str, raw_value: str) -> ConfigChange:
        setting = SETTINGS_BY_KEY.get(setting_key)
        if setting is None:
            raise ConfigEditError("Эта настройка не разрешена для изменения через Telegram.")
        new_value = normalize_setting_value(setting, raw_value)
        return ConfigChange(
            setting=setting,
            old_value=self.read_value(setting),
            new_value=new_value,
        )

    def prepare_toggle(self, setting_key: str) -> ConfigChange:
        setting = SETTINGS_BY_KEY.get(setting_key)
        if setting is None or setting.kind is not SettingKind.BOOLEAN:
            raise ConfigEditError("Эта настройка не является переключателем.")
        old_value = self.read_value(setting)
        new_value = "false" if _parse_boolean(old_value) else "true"
        return ConfigChange(setting=setting, old_value=old_value, new_value=new_value)

    def apply(self, change: ConfigChange) -> Path:
        """Apply a validated change atomically and return the created backup path."""

        with self._lock:
            current_value = self.read_value(change.setting)
            if current_value != change.old_value:
                raise ConfigEditError(
                    "Конфигурация уже изменилась. Откройте настройки и повторите действие."
                )
            source = self._read_text()
            updated = _replace_ini_option(source, change.setting, change.new_value)
            backup = self._validated_atomic_replace(updated)
            self._write_audit("changed", change.setting.key)
            return backup

    def restore_latest(self) -> Path:
        """Restore the most recent valid backup and preserve the current version."""

        with self._lock:
            backups = self.backups()
            if not backups:
                raise ConfigEditError("Резервных копий конфигурации пока нет.")
            restored_from = backups[0]
            restored_text = restored_from.read_text(encoding="utf-8")
            self._validated_atomic_replace(restored_text)
            self._write_audit("restored", "latest_backup")
            return restored_from

    def backups(self) -> list[Path]:
        backup_dir = self.state_dir / "config-backups"
        return sorted(backup_dir.glob("*.ini"), reverse=True) if backup_dir.is_dir() else []

    def _read_text(self) -> str:
        try:
            return self.config_file.read_text(encoding="utf-8")
        except OSError as error:
            raise ConfigEditError(f"Не удалось прочитать конфигурацию: {error}") from error

    def _read_parser(self) -> configparser.RawConfigParser:
        parser = configparser.RawConfigParser(interpolation=None)
        try:
            parser.read_string(self._read_text())
        except configparser.Error as error:
            raise ConfigEditError(f"Конфигурация содержит ошибку: {error}") from error
        return parser

    def _validated_atomic_replace(self, content: str) -> Path:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(
                prefix=".hh-config-",
                suffix=".ini",
                dir=self.config_file.parent,
                text=True,
            )
            temporary_path = Path(raw_path)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            read_file_config(temporary_path)
            backup = self._create_backup()
            if self.config_file.exists():
                os.chmod(temporary_path, self.config_file.stat().st_mode & 0o777)
            os.replace(temporary_path, self.config_file)
            temporary_path = None
            return backup
        except (OSError, ValueError) as error:
            raise ConfigEditError(f"Настройка не сохранена: {error}") from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _create_backup(self) -> Path:
        backup_dir = self.state_dir / "config-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(MOSCOW).strftime("%Y%m%d-%H%M%S-%f")
        backup = backup_dir / f"hh-config-{timestamp}.ini"
        shutil.copy2(self.config_file, backup)
        for obsolete in self.backups()[_MAX_BACKUPS:]:
            obsolete.unlink(missing_ok=True)
        return backup

    def _write_audit(self, action: str, setting_key: str) -> None:
        record = {
            "occurred_at": datetime.now(MOSCOW).isoformat(),
            "actor": "authorized_telegram_owner",
            "action": action,
            "setting": setting_key,
        }
        audit_path = self.state_dir / "config-audit.jsonl"
        try:
            with audit_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as error:
            raise ConfigEditError(f"Настройка сохранена, но аудит не записан: {error}") from error


def settings_for_category(category: str) -> tuple[EditableSetting, ...]:
    return tuple(setting for setting in EDITABLE_SETTINGS if setting.category == category)


def normalize_setting_value(setting: EditableSetting, raw_value: str) -> str:
    value = raw_value.strip()
    if setting.kind is SettingKind.TEXT:
        if not value and setting.required:
            raise ConfigEditError("Значение не может быть пустым.")
        if len(value) > 150 or "\n" in value or "\r" in value:
            raise ConfigEditError("Введите одну строку длиной не более 150 символов.")
        return value
    if setting.kind is SettingKind.INTEGER:
        try:
            number = int(value)
        except ValueError as error:
            raise ConfigEditError("Введите целое число.") from error
        if not setting.minimum <= number <= setting.maximum:
            raise ConfigEditError(
                f"Допустимое значение: от {setting.minimum} до {setting.maximum}."
            )
        return str(number)
    if setting.kind is SettingKind.BOOLEAN:
        return "true" if _parse_boolean(value) else "false"
    values = _normalize_list(value)
    if setting.required and not values:
        raise ConfigEditError("Список не может быть пустым.")
    if len(values) > setting.max_items:
        raise ConfigEditError(f"Допустимо не более {setting.max_items} значений.")
    if any(len(item) > 150 for item in values):
        raise ConfigEditError("Каждое значение должно быть не длиннее 150 символов.")
    if setting.kind is SettingKind.CHOICE_LIST:
        unknown = [item for item in values if item not in setting.choices]
        if unknown:
            raise ConfigEditError(
                "Неизвестное значение. Допустимо: " + ", ".join(setting.choices) + "."
            )
    return "\n".join(values)


def display_setting_value(setting: EditableSetting, value: str) -> str:
    if setting.kind is SettingKind.BOOLEAN:
        return "включено" if _parse_boolean(value) else "выключено"
    if not value:
        return "не задано"
    if setting.kind in {SettingKind.LIST, SettingKind.CHOICE_LIST}:
        return "; ".join(_normalize_list(value)) or "не задано"
    if setting.key == "daily_response_limit" and value == "0":
        return "без ограничения"
    return value


def _normalize_list(value: str) -> tuple[str, ...]:
    if value.strip().casefold() in {"-", "пусто", "очистить"}:
        return ()
    return tuple(dict.fromkeys(item.strip() for item in value.splitlines() if item.strip()))


def _parse_boolean(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"1", "yes", "true", "on", "да", "включено"}:
        return True
    if normalized in {"", "0", "no", "false", "off", "нет", "выключено"}:
        return False
    raise ConfigEditError("Допустимо: включено или выключено.")


def _replace_ini_option(source: str, setting: EditableSetting, value: str) -> str:
    newline = "\r\n" if "\r\n" in source else "\n"
    lines = source.splitlines()
    section_start, section_end = _find_section(lines, setting.section)
    rendered = _render_option(setting.option, value)
    if section_start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([f"[{setting.section}]", *rendered])
        return newline.join(lines) + newline

    option_start, option_end = _find_option(
        lines,
        section_start + 1,
        section_end if section_end is not None else len(lines),
        setting.option,
    )
    if option_start is None:
        insertion = section_end if section_end is not None else len(lines)
        if insertion > section_start + 1 and lines[insertion - 1].strip():
            rendered.insert(0, "")
        lines[insertion:insertion] = rendered
    else:
        lines[option_start:option_end] = rendered
    return newline.join(lines) + newline


def _find_section(lines: list[str], name: str) -> tuple[int | None, int | None]:
    start: int | None = None
    for index, line in enumerate(lines):
        match = _SECTION_PATTERN.match(line)
        if match and match.group(1).strip().casefold() == name.casefold():
            start = index
            continue
        if start is not None and match:
            return start, index
    return start, None


def _find_option(
    lines: list[str], start: int, end: int, option: str
) -> tuple[int | None, int | None]:
    for index in range(start, end):
        match = _OPTION_PATTERN.match(lines[index])
        if not match or match.group(1).strip().casefold() != option.casefold():
            continue
        block_end = index + 1
        while block_end < end and lines[block_end].strip() and lines[block_end][0].isspace():
            block_end += 1
        return index, block_end
    return None, None


def _render_option(option: str, value: str) -> list[str]:
    values = value.splitlines()
    if len(values) <= 1:
        return [f"{option} = {values[0] if values else ''}"]
    return [f"{option} =", *(f"    {item}" for item in values)]
