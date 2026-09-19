from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from hh_raiser.bot.cli import main
from hh_raiser.bot.models import BotSettings


class BotCliTests(unittest.TestCase):
    def test_systemd_mode_overrides_value_from_env_file(self) -> None:
        with TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("HHRAISER_BOT_USER_SYSTEMD=true\n", encoding="utf-8")
            settings = BotSettings("token", frozenset({1}), {}, 25, 0, False)

            with (
                patch("hh_raiser.bot.cli.configure_logging"),
                patch(
                    "hh_raiser.bot.cli.load_bot_settings", return_value=settings
                ) as load_settings,
                patch("hh_raiser.bot.cli.run_telegram_bot", new=Mock(return_value=object())),
                patch("hh_raiser.bot.cli.asyncio.run"),
            ):
                result = main(
                    [
                        "--config-file",
                        str(env_path),
                        "--env-file",
                        str(env_path),
                        "--systemd-mode",
                        "system",
                    ]
                )

        self.assertEqual(result, 0)
        self.assertEqual(
            load_settings.call_args.kwargs["environment"]["HHRAISER_BOT_USER_SYSTEMD"], "false"
        )
