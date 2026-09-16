from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from hh_raiser.browser import is_closed_playwright_error
from hh_raiser.infrastructure.browser.captcha_answer_source import (
    CaptchaAnswerSource,
    CaptchaRequest,
)
from hh_raiser.infrastructure.storage.owner_interventions import OwnerInterventionStore
from hh_raiser.logging_config import LOGGER, LogEvent, event_data

_CAPTCHA_PATH = "/account/captcha"
_CAPTCHA_HEADING = "Подтвердите, что вы не робот"
_POLL_MILLISECONDS = 750
_RESULT_WAIT_MILLISECONDS = 8_000
_FORM_WAIT_MILLISECONDS = 10_000


class CaptchaResolver(Protocol):
    """Общий контракт для Telegram- и локального режима ручной CAPTCHA."""

    def resolve_if_present(
        self,
        page: Page,
        *,
        stop_requested: Callable[[], bool] | None = None,
    ) -> bool:
        """Обработать CAPTCHA, если она показана на текущей странице."""


class OwnerInterventionCancelled(RuntimeError):
    """Возникает, когда HHRaiser остановлен во время ожидания владельца."""


class ManualCaptchaRequired(RuntimeError):
    """Возникает, когда headless-запуск нельзя завершить без участия владельца."""


class ManualCaptchaGuard:
    """Приостановить видимый браузер, пока владелец не решит CAPTCHA HH."""

    def __init__(
        self,
        *,
        headless: bool,
        answer_source: CaptchaAnswerSource | None = None,
    ) -> None:
        self.headless = headless
        self.answer_source = answer_source

    def resolve_if_present(
        self,
        page: Page,
        *,
        stop_requested: Callable[[], bool] | None = None,
    ) -> bool:
        if not CaptchaGuard.is_present(page):
            return False
        if self.headless:
            raise ManualCaptchaRequired(
                "HH запросил CAPTCHA, но Chromium запущен без окна. "
                "Запустите видимый режим или включите --telegram-captcha."
            )

        should_stop = stop_requested or (lambda: False)
        if _submit_external_answer(page, self.answer_source):
            return True
        LOGGER.warning(
            "HH запросил CAPTCHA; активность приостановлена. "
            "Завершите проверку в открытом окне Chromium.",
            extra=event_data(LogEvent.AUTH),
        )
        while CaptchaGuard.is_present(page):
            if should_stop() or page.is_closed():
                raise OwnerInterventionCancelled
            page.wait_for_timeout(_POLL_MILLISECONDS)
        LOGGER.info(
            "CAPTCHA подтверждена в Chromium; автоматическая работа продолжена.",
            extra=event_data(LogEvent.AUTH),
        )
        return True


class CaptchaGuard:
    """Приостановить Playwright и передать текстовую CAPTCHA HH через локальный ящик."""

    def __init__(
        self,
        store: OwnerInterventionStore,
        *,
        answer_source: CaptchaAnswerSource | None = None,
    ) -> None:
        self.store = store
        self.answer_source = answer_source
        self.store.cancel_pending()

    def resolve_if_present(
        self,
        page: Page,
        *,
        stop_requested: Callable[[], bool] | None = None,
    ) -> bool:
        """Решить каждую показанную текстовую CAPTCHA HH ответом владельца."""

        if not self.is_present(page):
            return False
        should_stop = stop_requested or (lambda: False)
        LOGGER.warning(
            "HH запросил подтверждение владельца; активность приостановлена, "
            "CAPTCHA отправляется в Telegram.",
            extra=event_data(LogEvent.AUTH),
        )
        while self.is_present(page):
            image, input_field, submit = captcha_controls(page)
            try:
                for control in (image, input_field, submit):
                    control.wait_for(state="visible", timeout=_FORM_WAIT_MILLISECONDS)
            except PlaywrightTimeoutError as error:
                raise RuntimeError(
                    "Форма текстовой CAPTCHA HH не загрузилась полностью; "
                    "автоматическая работа остановлена без дальнейших действий."
                ) from error

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
            external_answer = _answer_from_source(
                self.answer_source,
                CaptchaRequest(
                    challenge_id=challenge_id,
                    image_bytes=image_bytes,
                    prompt="Введите текст с изображения. Регистр обычно не важен.",
                ),
            )
            if external_answer is not None:
                input_field.fill(external_answer)
                submit.click()
                if self._wait_for_result(page, should_stop):
                    self.store.finish(challenge_id, "resolved")
                    return True
                self.store.finish(challenge_id, "failed")
                continue
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
        """Распознать CAPTCHA HH до извлечения названия вакансии."""

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
    guard: CaptchaResolver | None,
    page: Page,
    *,
    stop_requested: Callable[[], bool] | None = None,
) -> bool:
    """Запустить настроенный обработчик CAPTCHA, сохранив необязательный режим."""

    if guard is None:
        return False
    try:
        return guard.resolve_if_present(page, stop_requested=stop_requested)
    except PlaywrightError as error:
        if is_closed_playwright_error(error):
            raise
        raise RuntimeError("Не удалось обработать CAPTCHA HH.") from error


def _submit_external_answer(page: Page, answer_source: CaptchaAnswerSource | None) -> bool:
    """Ввести ответ личного сайта, если тот уже доступен; иначе оставить ручной режим."""

    if answer_source is None:
        return False
    image, input_field, submit = captcha_controls(page)
    try:
        for control in (image, input_field, submit):
            control.wait_for(state="visible", timeout=_FORM_WAIT_MILLISECONDS)
    except PlaywrightTimeoutError:
        return False
    answer = _answer_from_source(
        answer_source,
        CaptchaRequest(
            challenge_id=uuid.uuid4().hex,
            image_bytes=image.screenshot(type="png"),
            prompt="Введите текст с изображения. Регистр обычно не важен.",
        ),
    )
    if answer is None:
        return False
    input_field.fill(answer)
    submit.click()
    return not CaptchaGuard.is_present(page)


def _answer_from_source(
    source: CaptchaAnswerSource | None,
    request: CaptchaRequest,
) -> str | None:
    """Нормализовать короткий ручной ответ, не позволяя источнику менять браузер."""

    if source is None:
        return None
    answer = source.get_answer(request)
    if answer is None:
        return None
    normalized = " ".join(answer.split())
    return normalized if 1 <= len(normalized) <= 80 else None


def captcha_controls(page: Page) -> tuple[Locator, Locator, Locator]:
    """Вернуть семантические элементы CAPTCHA HH со стабильными data-qa fallback-ами."""

    image = page.get_by_role("img", name="captcha", exact=True).or_(
        page.locator('[data-qa="account-captcha-picture"]')
    )
    input_field = page.get_by_role("textbox", name="Текст с картинки", exact=True).or_(
        page.locator('[data-qa="account-captcha-input"]')
    )
    submit = page.get_by_role("button", name="Отправить", exact=True).or_(
        page.locator('[data-qa="account-captcha-submit"]')
    )
    return image.first, input_field.first, submit.first
