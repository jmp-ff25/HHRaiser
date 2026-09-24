from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from hh_raiser.activities.resume_index_refresher import (
    _normalized_fingerprint,
    refresh_resume_index,
    trailing_period_count,
)
from hh_raiser.config import read_file_config
from hh_raiser.domain.result import ActivityResult, ActivityStatus
from hh_raiser.infrastructure.browser.modal_guard import dismiss_hh_pro_modal
from hh_raiser.infrastructure.hh.selectors import (
    EXPERIENCE_DESCRIPTION_INPUT,
    EXPERIENCE_EDIT_BUTTON,
    PROFILE_URL,
    RESUME_CARD,
    RESUME_DIRECT_LINK,
)

if TYPE_CHECKING:
    from playwright.sync_api import Page

_LIVE_TEST_ENABLED = os.environ.get("HH_LIVE_RESUME_INDEX_E2E") == "1"
_HEADLESS = os.environ.get("HH_LIVE_RESUME_INDEX_HEADLESS", "true").lower() != "false"
_CONFIG_PATH = Path(os.environ.get("HH_LIVE_CONFIG_FILE", "state/main/hh-config.ini"))
_PROFILE_DIR = Path(os.environ.get("HH_LIVE_PROFILE_DIR", "state/main/browser-profile"))
_CDP_PORT = int(os.environ.get("HH_LIVE_RESUME_INDEX_CDP_PORT", "0"))
_DEBUG_PAUSE_SECONDS = int(os.environ.get("HH_LIVE_RESUME_INDEX_DEBUG_PAUSE_SECONDS", "0"))


@unittest.skipUnless(
    _LIVE_TEST_ENABLED,
    "Живой тест отключён. Укажите HH_LIVE_RESUME_INDEX_E2E=1 только для отдельной сессии.",
)
class ResumeIndexLiveTests(unittest.TestCase):
    """Проверяет обратимое сохранение точки на настоящей странице HH.

    Тест не использует рабочий каталог маркера: он создаёт временное состояние,
    добавляет одну точку и немедленно возвращает исходный текст. Его нельзя
    запускать одновременно с основным экземпляром HHRaiser, использующим тот же
    профиль браузера. Значение ``HH_LIVE_RESUME_INDEX_HEADLESS=false`` открывает
    видимый Chromium для ручного наблюдения за сценарием.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not _CONFIG_PATH.is_file():
            raise unittest.SkipTest(f"Не найден файл настроек: {_CONFIG_PATH}")
        if not _PROFILE_DIR.is_dir():
            raise unittest.SkipTest(f"Не найден профиль браузера: {_PROFILE_DIR}")

        config = read_file_config(_CONFIG_PATH)
        if not config.resume_title:
            raise unittest.SkipTest("В [resume] title не задано название резюме.")
        cls.resume_title = config.resume_title

    def test_adds_and_restores_period_for_configured_resume(self) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                _PROFILE_DIR,
                headless=_HEADLESS,
                viewport={"width": 1440, "height": 1080},
                args=self._debug_browser_args(),
            )
            try:
                page = context.pages[0]
                resume_url = self._configured_resume_url(page)
                target_index, original_value = self._select_safe_experience(page, resume_url)

                with TemporaryDirectory(prefix="hh-resume-index-e2e-") as temporary_dir:
                    test_state_dir = Path(temporary_dir)
                    (test_state_dir / "resume-refresh-sequence.json").write_text(
                        json.dumps({"next_target_index": target_index}), encoding="utf-8"
                    )
                    marker_path = test_state_dir / "resume-refresh-marker.json"

                    try:
                        added = refresh_resume_index(
                            page,
                            profile_dir=test_state_dir,
                            resume_title=self.resume_title,
                        )
                        self._pause_for_cdp_inspection(page, added)
                        self.assertEqual(added.status, ActivityStatus.SUCCESS, added.detail)
                        self.assertTrue(added.metadata.get("marker_added"), added.detail)

                        restored = refresh_resume_index(
                            page,
                            profile_dir=test_state_dir,
                            resume_title=self.resume_title,
                        )
                        self._pause_for_cdp_inspection(page, restored)
                        self.assertEqual(restored.status, ActivityStatus.SUCCESS, restored.detail)
                        self.assertFalse(restored.metadata.get("marker_added"), restored.detail)

                        final_value = self._read_experience_description(
                            page, resume_url, target_index
                        )
                        self.assertEqual(
                            _normalized_fingerprint(final_value),
                            _normalized_fingerprint(original_value),
                            "После теста описание опыта не восстановилось до исходного состояния.",
                        )
                    finally:
                        if marker_path.exists():
                            rollback = refresh_resume_index(
                                page,
                                profile_dir=test_state_dir,
                                resume_title=self.resume_title,
                            )
                            self.assertTrue(
                                rollback.status == ActivityStatus.SUCCESS,
                                f"Не удалось безопасно откатить тестовую точку: {rollback.detail}",
                            )

                    self.assertFalse(marker_path.exists())
            finally:
                context.close()

    @staticmethod
    def _debug_browser_args() -> list[str]:
        """Открыть CDP только для локальной диагностики живого E2E-теста."""
        if not _CDP_PORT:
            return []
        return [
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={_CDP_PORT}",
        ]

    @staticmethod
    def _pause_for_cdp_inspection(page: Page, outcome: ActivityResult) -> None:
        """Оставить DOM доступным через CDP только при неподтверждённом исходе."""
        if not _DEBUG_PAUSE_SECONDS or outcome.status == ActivityStatus.SUCCESS:
            return
        print(
            "Отладочная пауза после неподтверждённого сохранения: "
            f"{_DEBUG_PAUSE_SECONDS} сек.; CDP: http://127.0.0.1:{_CDP_PORT}; "
            f"исход: {outcome.detail}; признаки: {outcome.metadata}",
            flush=True,
        )
        deadline = time.monotonic() + _DEBUG_PAUSE_SECONDS
        while time.monotonic() < deadline:
            page.wait_for_timeout(250)

    def _configured_resume_url(self, page: Page) -> str:
        page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=30_000)
        dismiss_hh_pro_modal(page)

        matching_cards = []
        for index in range(page.locator(RESUME_CARD).count()):
            card = page.locator(RESUME_CARD).nth(index)
            heading = card.get_by_role("heading", name=self.resume_title, exact=True)
            if heading.count() == 1:
                matching_cards.append(card)

        self.assertEqual(
            len(matching_cards),
            1,
            f"В профиле должно быть ровно одно резюме «{self.resume_title}».",
        )
        direct_links = matching_cards[0].locator(RESUME_DIRECT_LINK)
        self.assertEqual(direct_links.count(), 1, "Не найдена единственная прямая ссылка резюме.")
        href = direct_links.first.get_attribute("href")
        self.assertIsNotNone(href, "У ссылки резюме нет адреса.")
        return f"https://hh.ru{href}"

    def _select_safe_experience(self, page: Page, resume_url: str) -> tuple[int, str]:
        page.goto(resume_url, wait_until="domcontentloaded", timeout=30_000)
        dismiss_hh_pro_modal(page)
        buttons = page.locator(EXPERIENCE_EDIT_BUTTON)
        self.assertGreater(buttons.count(), 0, "Кнопки редактирования опыта не распознаны.")

        for index in range(buttons.count()):
            buttons.nth(index).click()
            description = page.locator(EXPERIENCE_DESCRIPTION_INPUT).first
            description.wait_for(state="visible", timeout=15_000)
            value = description.input_value()
            page.goto(resume_url, wait_until="domcontentloaded", timeout=30_000)
            if value.strip() and trailing_period_count(value) <= 1:
                return index, value

        self.fail("Не найдено непустое описание опыта без лишних конечных точек.")

    def _read_experience_description(self, page: Page, resume_url: str, target_index: int) -> str:
        page.goto(resume_url, wait_until="domcontentloaded", timeout=30_000)
        dismiss_hh_pro_modal(page)
        page.locator(EXPERIENCE_EDIT_BUTTON).nth(target_index).click()
        description = page.locator(EXPERIENCE_DESCRIPTION_INPUT).first
        description.wait_for(state="visible", timeout=15_000)
        return description.input_value()
