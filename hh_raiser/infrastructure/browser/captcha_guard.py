from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from hh_raiser.browser import is_closed_playwright_error
from hh_raiser.infrastructure.storage.owner_interventions import OwnerInterventionStore
from hh_raiser.logging_config import LOGGER, LogEvent, event_data

_CAPTCHA_PATH = "/account/captcha"
_CAPTCHA_HEADING = "Подтвердите, что вы не робот"
_POLL_MILLISECONDS = 750
_RESULT_WAIT_MILLISECONDS = 8_000


class OwnerInterventionCancelled(RuntimeError):
    """Raised when HHRaiser is stopped while waiting for its owner."""


class CaptchaGuard:
    """Pause Playwright and relay HH text CAPTCHA challenges through a local mailbox."""

    def __init__(self, store: OwnerInterventionStore) -> None:
        self.store = store
        self.store.cancel_pending()

    def resolve_if_present(
        self,
        page: Page,
        *,
        stop_requested: Callable[[], bool] | None = None,
    ) -> bool:
        """Resolve every currently displayed HH text CAPTCHA with an owner's answer."""

        if not self.is_present(page):
            return False
        should_stop = stop_requested or (lambda: False)
        LOGGER.warning(
            "HH запросил подтверждение владельца; активность приостановлена, "
            "CAPTCHA отправляется в Telegram.",
            extra=event_data(LogEvent.AUTH),
        )
        while self.is_present(page):
            input_field = page.get_by_placeholder("Текст с картинки", exact=True).first
            submit = page.get_by_role("button", name="Отправить", exact=True).first
            captcha_form = page.locator("form").filter(has=input_field).first
            image = captcha_form.locator("img").first
            if not image.count() or not input_field.count() or not submit.count():
                raise RuntimeError(
                    "HH показал неподдерживаемую интерактивную проверку; "
                    "требуется открыть браузер вручную."
                )

            challenge_id = uuid.uuid4().hex
            screenshot_path = self.store.screenshot_dir / f"captcha-{challenge_id}.png"
            image_bytes = image.screenshot(type="png")
            self._write_private(screenshot_path, image_bytes)
            fingerprint = hashlib.sha256(image_bytes).digest()
            self.store.create_captcha(
                challenge_id,
                screenshot_path,
                "Введите текст с изображения. Регистр обычно не важен.",
            )
            answer = self._wait_for_answer(page, challenge_id, should_stop)
            current_fingerprint = hashlib.sha256(image.screenshot(type="png")).digest()
            if current_fingerprint != fingerprint:
                self.store.finish(challenge_id, "stale")
                LOGGER.info(
                    "Изображение CAPTCHA изменилось до получения ответа; "
                    "в Telegram будет отправлено новое.",
                    extra=event_data(LogEvent.AUTH),
                )
                continue

            input_field.fill(answer)
            submit.click()
            if self._wait_for_result(page, should_stop):
                self.store.finish(challenge_id, "resolved")
                LOGGER.info(
                    "Подтверждение владельца принято HH; автоматическая работа продолжена.",
                    extra=event_data(LogEvent.AUTH),
                )
                return True
            self.store.finish(challenge_id, "failed")
            LOGGER.warning(
                "HH не принял введённый текст; новая CAPTCHA будет отправлена в Telegram.",
                extra=event_data(LogEvent.AUTH),
            )
        return True

    @staticmethod
    def is_present(page: Page) -> bool:
        """Recognize HH's own CAPTCHA before vacancy-title extraction."""

        if urlsplit(page.url).path == _CAPTCHA_PATH:
            return True
        heading = page.get_by_role("heading", name=_CAPTCHA_HEADING, exact=True).first
        return heading.count() > 0 and heading.is_visible(timeout=0)

    def _wait_for_answer(
        self,
        page: Page,
        challenge_id: str,
        stop_requested: Callable[[], bool],
    ) -> str:
        while True:
            if stop_requested() or page.is_closed():
                self.store.finish(challenge_id, "cancelled")
                raise OwnerInterventionCancelled
            answer = self.store.take_answer(challenge_id)
            if answer is not None:
                return answer
            page.wait_for_timeout(_POLL_MILLISECONDS)

    def _wait_for_result(self, page: Page, stop_requested: Callable[[], bool]) -> bool:
        elapsed = 0
        while elapsed < _RESULT_WAIT_MILLISECONDS:
            if stop_requested() or page.is_closed():
                raise OwnerInterventionCancelled
            page.wait_for_timeout(_POLL_MILLISECONDS)
            elapsed += _POLL_MILLISECONDS
            if not self.is_present(page):
                return True
        return False

    @staticmethod
    def _write_private(path: Path, content: bytes) -> None:
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_bytes(content)
            temporary.chmod(0o600)
            os.replace(temporary, path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise


def resolve_captcha(
    guard: CaptchaGuard | None,
    page: Page,
    *,
    stop_requested: Callable[[], bool] | None = None,
) -> bool:
    """Run the configured CAPTCHA guard while preserving optional local operation."""

    if guard is None:
        return False
    try:
        return guard.resolve_if_present(page, stop_requested=stop_requested)
    except PlaywrightError as error:
        if is_closed_playwright_error(error):
            raise
        raise RuntimeError("Не удалось обработать CAPTCHA HH через Telegram.") from error
