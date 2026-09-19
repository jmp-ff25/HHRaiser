from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from hh_raiser.bot.app import run_telegram_bot
from hh_raiser.bot.config import BotConfigError, load_bot_settings
from hh_raiser.env_file import DEFAULT_ENV_PATH, EnvFileError, load_env_file
from hh_raiser.logging_config import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Telegram-панель управления экземплярами HHRaiser."
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        default=DEFAULT_ENV_PATH,
        help=".env с настройками бота; hh-bot.ini можно передать для совместимости.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_PATH,
        help="Локальный .env; внешние переменные имеют приоритет.",
    )
    parser.add_argument(
        "--systemd-mode",
        choices=("user", "system"),
        help="Явно выбрать пользовательские или системные службы systemd.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging()
    environment = dict(os.environ)
    try:
        load_env_file(args.env_file.expanduser(), environment=environment)
    except EnvFileError as error:
        parser.error(str(error))
    if args.systemd_mode is not None:
        environment["HHRAISER_BOT_USER_SYSTEMD"] = str(args.systemd_mode == "user").lower()
    try:
        settings = load_bot_settings(
            args.config_file.expanduser().resolve(), environment=environment
        )
    except BotConfigError as error:
        parser.error(str(error))
    try:
        asyncio.run(run_telegram_bot(settings))
    except KeyboardInterrupt:
        return 0
    return 0
