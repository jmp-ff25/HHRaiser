from __future__ import annotations

import argparse
import asyncio
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
        default=Path("hh-bot.ini"),
        help="Локальный INI-файл со списком разрешённых экземпляров.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_PATH,
        help="Локальный .env с токеном Telegram; внешняя переменная имеет приоритет.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging()
    try:
        load_env_file(args.env_file.expanduser())
    except EnvFileError as error:
        parser.error(str(error))
    try:
        settings = load_bot_settings(args.config_file.expanduser().resolve())
    except BotConfigError as error:
        parser.error(str(error))
    try:
        asyncio.run(run_telegram_bot(settings))
    except KeyboardInterrupt:
        return 0
    return 0
