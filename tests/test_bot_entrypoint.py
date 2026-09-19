from __future__ import annotations

import asyncio
import sqlite3
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from hh_raiser.bot.app import TelegramControlBot
from hh_raiser.bot.models import BotSettings, ManagedInstance, ServiceSnapshot
from hh_raiser.infrastructure.storage.owner_interventions import PendingCaptcha


class BotEntrypointTests(unittest.TestCase):
    def test_cli_imports_with_supported_aiogram_api(self) -> None:
        from hh_raiser.bot.cli import main

        self.assertTrue(callable(main))


class RelayOwnerInterventionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_continues_after_sqlite_error(self) -> None:
        controller, store = self._relay_controller()
        store.pending_for_user.side_effect = sqlite3.OperationalError("database is locked")

        with (
            patch("hh_raiser.bot.app.asyncio.sleep", side_effect=asyncio.CancelledError),
            self.assertRaises(asyncio.CancelledError),
        ):
            await controller.relay_owner_interventions(AsyncMock())

        store.pending_for_user.assert_called_once_with(10)

    async def test_continues_when_screenshot_disappears_during_delivery(self) -> None:
        controller, store = self._relay_controller()
        screenshot = Path(controller.settings.instances["main"].state_dir) / "captcha.png"
        screenshot.write_bytes(b"png")
        store.pending_for_user.return_value = [
            PendingCaptcha("challenge", screenshot, "Введите текст", datetime.now(UTC))
        ]
        bot = AsyncMock()
        bot.send_photo.side_effect = FileNotFoundError

        with (
            patch("hh_raiser.bot.app.asyncio.sleep", side_effect=asyncio.CancelledError),
            self.assertRaises(asyncio.CancelledError),
        ):
            await controller.relay_owner_interventions(bot)

        bot.send_photo.assert_called_once()

    def _relay_controller(self) -> tuple[TelegramControlBot, MagicMock]:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state_dir = Path(directory.name)
        instance = ManagedInstance(
            "main", "Основной", "hhraiser@main", state_dir, state_dir / "hh-config.ini"
        )
        controller = TelegramControlBot(BotSettings("token", frozenset({10}), {"main": instance}))
        store = MagicMock()
        controller.intervention_stores = {"main": store}
        return controller, store


class PeriodicSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_does_not_send_summary_when_instance_is_stopped(self) -> None:
        controller = self._controller()
        controller.services.snapshot = AsyncMock(
            return_value=ServiceSnapshot("inactive", "dead", None)
        )
        bot = AsyncMock()

        with (
            patch("hh_raiser.bot.app.asyncio.sleep", side_effect=[None, asyncio.CancelledError]),
            self.assertRaises(asyncio.CancelledError),
        ):
            await controller.send_periodic_summaries(bot)

        bot.send_message.assert_not_awaited()

    def _controller(self) -> TelegramControlBot:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state_dir = Path(directory.name)
        instance = ManagedInstance(
            "main", "Основной", "hhraiser@main", state_dir, state_dir / "hh-config.ini"
        )
        return TelegramControlBot(
            BotSettings("token", frozenset({10}), {"main": instance}, summary_interval_minutes=1)
        )


if __name__ == "__main__":
    unittest.main()
