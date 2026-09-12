from __future__ import annotations

import unittest


class BotEntrypointTests(unittest.TestCase):
    def test_cli_imports_with_supported_aiogram_api(self) -> None:
        from hh_raiser.bot.cli import main

        self.assertTrue(callable(main))


if __name__ == "__main__":
    unittest.main()
