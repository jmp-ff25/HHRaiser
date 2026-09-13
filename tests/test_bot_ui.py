from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramBadRequest

from hh_raiser.bot.app import edit_message_if_changed


class BotUiTests(unittest.IsolatedAsyncioTestCase):
    async def test_unchanged_message_is_a_harmless_refresh(self) -> None:
        message = MagicMock()
        message.edit_text = AsyncMock(
            side_effect=TelegramBadRequest(
                method=MagicMock(),
                message="Bad Request: message is not modified",
            )
        )

        await edit_message_if_changed(message, "same", MagicMock())

    async def test_other_telegram_edit_error_is_not_hidden(self) -> None:
        message = MagicMock()
        message.edit_text = AsyncMock(
            side_effect=TelegramBadRequest(
                method=MagicMock(),
                message="Bad Request: message to edit not found",
            )
        )

        with self.assertRaisesRegex(TelegramBadRequest, "not found"):
            await edit_message_if_changed(message, "new", MagicMock())


if __name__ == "__main__":
    unittest.main()
