"""Единая неинтерактивная команда управления развёрнутым HHRaiser."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from hh_raiser.bot.config import BotConfigError, load_bot_settings
from hh_raiser.cli import build_parser as build_worker_parser
from hh_raiser.config import resolve_runtime_settings
from hh_raiser.env_file import EnvFileError, load_env_file

MAIN_SERVICE = "hhraiser@main.service"
BOT_SERVICE = "hhraiser-bot.service"


@dataclass(frozen=True)
class ProjectLayout:
    """Пути единственного экземпляра, относительные к корню проекта."""

    root: Path

    @property
    def env_file(self) -> Path:
        return self.root / ".env"

    @property
    def config_file(self) -> Path:
        return self.root / "state" / "main" / "hh-config.ini"


class SetupError(ValueError):
    """Проект не готов к безопасному запуску без ввода секретов."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Управление развёрнутым HHRaiser.")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command in ("setup", "reconfigure"):
        subcommands.add_parser(command, help="Проверить конфигурацию без запуска браузера.")
    subcommands.add_parser("start", help="Запустить основной экземпляр и Telegram-бота.")
    subcommands.add_parser("stop", help="Остановить основной экземпляр и Telegram-бота.")
    subcommands.add_parser("status", help="Показать состояние systemd-служб.")
    logs = subcommands.add_parser("logs", help="Показать последние записи журналов systemd.")
    logs.add_argument("--lines", type=_positive_lines, default=100)
    subcommands.add_parser("update", help="Обновить код из Git и синхронизировать зависимости.")
    return parser


def _positive_lines(value: str) -> int:
    number = int(value)
    if number < 1 or number > 1_000:
        raise argparse.ArgumentTypeError("Количество строк должно быть от 1 до 1000.")
    return number


def validate_setup(layout: ProjectLayout, *, environment: dict[str, str] | None = None) -> None:
    """Проверить все обязательные файлы и значения, ничего не изменяя."""

    missing_paths = [path for path in (layout.env_file, layout.config_file) if not path.is_file()]
    if missing_paths:
        relative_paths = ", ".join(str(path.relative_to(layout.root)) for path in missing_paths)
        raise SetupError(f"Не найдены обязательные файлы: {relative_paths}.")
    source = dict(os.environ if environment is None else environment)
    try:
        load_env_file(layout.env_file, environment=source)
    except EnvFileError as error:
        raise SetupError(str(error)) from error
    missing_values = [key for key in ("HH_PHONE", "HH_PASSWORD") if not source.get(key, "").strip()]
    if missing_values:
        raise SetupError("Не заполнены обязательные переменные .env: " + ", ".join(missing_values))
    worker_args = build_worker_parser().parse_args(
        ["--config-file", str(layout.config_file), "--full-activity"]
    )
    try:
        resolve_runtime_settings(worker_args)
        load_bot_settings(layout.env_file, environment=source)
    except (BotConfigError, ValueError) as error:
        raise SetupError(str(error)) from error


def run_systemctl(arguments: Sequence[str], *, root: Path) -> int:
    """Вызвать systemctl без shell и с понятной ошибкой вне Linux."""

    if sys.platform != "linux":
        raise SetupError("Команды start/stop/status/logs доступны через systemd только в Linux.")
    return subprocess.run(["systemctl", *arguments], cwd=root, check=False).returncode


def run_journalctl(arguments: Sequence[str], *, root: Path) -> int:
    """Прочитать системный журнал без shell и без изменения состояния служб."""

    if sys.platform != "linux":
        raise SetupError("Команда logs доступна через systemd только в Linux.")
    return subprocess.run(["journalctl", *arguments], cwd=root, check=False).returncode


def _require_valid_setup(layout: ProjectLayout) -> int:
    try:
        validate_setup(layout)
    except SetupError as error:
        print(f"HHRaiser не готов к запуску: {error}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    layout = ProjectLayout(args.project_dir.expanduser().resolve())
    if not (layout.root / "pyproject.toml").is_file():
        parser.error(f"Каталог проекта не найден: {layout.root}")
    if args.command in {"setup", "reconfigure"}:
        result = _require_valid_setup(layout)
        if result == 0:
            print("Конфигурация HHRaiser проверена: проект готов к запуску.")
        return result
    if args.command == "start":
        result = _require_valid_setup(layout)
        if result:
            return result
        return run_systemctl(("daemon-reload",), root=layout.root) or run_systemctl(
            ("enable", "--now", MAIN_SERVICE, BOT_SERVICE), root=layout.root
        )
    if args.command == "stop":
        return run_systemctl(("stop", MAIN_SERVICE, BOT_SERVICE), root=layout.root)
    if args.command == "status":
        return run_systemctl(("status", "--no-pager", MAIN_SERVICE, BOT_SERVICE), root=layout.root)
    if args.command == "logs":
        return run_journalctl(
            (
                "--no-pager",
                "--lines",
                str(args.lines),
                "--unit",
                MAIN_SERVICE,
                "--unit",
                BOT_SERVICE,
            ),
            root=layout.root,
        )
    if args.command == "update":
        result = _require_valid_setup(layout)
        if result:
            return result
        for command in (
            ("git", "pull", "--ff-only"),
            ("uv", "sync", "--locked"),
        ):
            if subprocess.run(command, cwd=layout.root, check=False).returncode:
                return 1
        return 0
    raise AssertionError(f"Неизвестная команда: {args.command}")
