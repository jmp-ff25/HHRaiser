from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramBadRequest

from hh_raiser.bot.app import edit_message_if_changed


class BotUiTests(unittest.IsolatedAsyncioTestCase):
    async def test_unchanged_message_is_a_harmless_refresh(self) -> None:
        message = MagicMock()
        message.text = "same"
        message.edit_text = AsyncMock(
            side_effect=TelegramBadRequest(
                method=MagicMock(),
                message="Bad Request: message is not modified",
            )
        )

        await edit_message_if_changed(message, "same", MagicMock())

    async def test_other_telegram_edit_error_is_not_hidden(self) -> None:
        message = MagicMock()
        message.text = "old"
        message.edit_text = AsyncMock(
            side_effect=TelegramBadRequest(
                method=MagicMock(),
                message="Bad Request: message to edit not found",
            )
        )

        with self.assertRaisesRegex(TelegramBadRequest, "not found"):
            await edit_message_if_changed(message, "new", MagicMock())

    async def test_media_message_opens_a_new_text_view(self) -> None:
        message = MagicMock()
        message.text = None
        message.edit_text = AsyncMock()
        message.edit_reply_markup = AsyncMock()
        message.answer = AsyncMock()
        keyboard = MagicMock()

        await edit_message_if_changed(message, "Все экземпляры", keyboard)

        message.edit_text.assert_not_awaited()
        message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
        message.answer.assert_awaited_once_with("Все экземпляры", reply_markup=keyboard)


if __name__ == "__main__":
    unittest.main()
