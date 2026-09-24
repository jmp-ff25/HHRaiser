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
_GEMINI_ATTEMPTS = 5


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
        audit_store: OwnerInterventionStore | None = None,
    ) -> None:
        self.headless = headless
        self.answer_source = answer_source
        self.audit_store = audit_store

    def resolve_if_present(
        self,
        page: Page,
        *,
        stop_requested: Callable[[], bool] | None = None,
    ) -> bool:
        if not CaptchaGuard.is_present(page):
            return False
        should_stop = stop_requested or (lambda: False)
        _record_captcha_event(self.audit_store, "detected")
        if _try_gemini_answers(
            page,
            self.answer_source,
            should_stop,
            fallback_message=(
                "останавливаю headless-запуск"
                if self.headless
                else "перехожу к ручному вводу в Chromium"
            ),
            audit_store=self.audit_store,
        ):
            return True
        if self.headless:
            _record_captcha_event(self.audit_store, "manual_unavailable_headless")
            raise ManualCaptchaRequired(
                "HH запросил CAPTCHA, но Chromium запущен без окна. "
                "Запустите видимый режим или включите --telegram-captcha."
            )

        LOGGER.warning(
            "HH запросил CAPTCHA; активность приостановлена. "
            "Завершите проверку в открытом окне Chromium.",
            extra=event_data(LogEvent.CAPTCHA),
        )
        _record_captcha_event(self.audit_store, "manual_waiting_in_browser")
        while CaptchaGuard.is_present(page):
            if should_stop() or page.is_closed():
                raise OwnerInterventionCancelled
            page.wait_for_timeout(_POLL_MILLISECONDS)
        LOGGER.info(
            "CAPTCHA подтверждена в Chromium; автоматическая работа продолжена.",
            extra=event_data(LogEvent.CAPTCHA),
        )
        _record_captcha_event(self.audit_store, "manual_resolved_in_browser")
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
            "HH запросил CAPTCHA; пробую автоматическое распознавание перед ручным режимом.",
            extra=event_data(LogEvent.CAPTCHA),
        )
        _record_captcha_event(self.store, "detected")
        while self.is_present(page):
            if self._try_gemini_answers(page, should_stop):
                return True

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
            _record_captcha_event(self.store, "manual_requested")
            answer = self._wait_for_answer(page, challenge_id, should_stop)
            current_fingerprint = hashlib.sha256(image.screenshot(type="png")).digest()
            if current_fingerprint != fingerprint:
                self.store.finish(challenge_id, "stale")
                _record_captcha_event(self.store, "manual_stale")
                LOGGER.info(
                    "Изображение CAPTCHA изменилось до получения ответа; "
                    "в Telegram будет отправлено новое.",
                    extra=event_data(LogEvent.AUTH),
                )
                continue

            input_field.fill(answer)
            submit.click()
            _record_captcha_event(self.store, "manual_answer_submitted")
            if self._wait_for_result(page, should_stop):
                self.store.finish(challenge_id, "resolved")
                _record_captcha_event(self.store, "manual_resolved")
                LOGGER.info(
                    "Подтверждение владельца принято HH; автоматическая работа продолжена.",
                    extra=event_data(LogEvent.AUTH),
                )
                return True
            self.store.finish(challenge_id, "failed")
            _record_captcha_event(self.store, "manual_rejected")
            LOGGER.warning(
                "HH не принял введённый текст; новая CAPTCHA будет отправлена в Telegram.",
                extra=event_data(LogEvent.AUTH),
            )
        return True

    def _try_gemini_answers(self, page: Page, stop_requested: Callable[[], bool]) -> bool:
        """Проверить до пяти вариантов Gemini до перехода к ручному подтверждению."""
        return _try_gemini_answers(
            page,
            self.answer_source,
            stop_requested,
            fallback_message="отправляю её в Telegram",
            wait_for_result=self._wait_for_result,
            audit_store=self.store,
        )

    @staticmethod
    def is_present(page: Page) -> bool:
        """Распознать CAPTCHA HH по странице или полной видимой форме в модальном окне."""

        if urlsplit(page.url).path == _CAPTCHA_PATH:
            return True
        heading = page.get_by_role("heading", name=_CAPTCHA_HEADING, exact=True).first
        if heading.count() > 0 and heading.is_visible(timeout=0):
            return True
        image, input_field, submit = captcha_controls(page)
        return all(
            control.count() > 0 and control.is_visible(timeout=0)
            for control in (image, input_field, submit)
        )

    def _wait_for_answer(
        self,
        page: Page,
        challenge_id: str,
        stop_requested: Callable[[], bool],
    ) -> str:
        while True:
            if stop_requested() or page.is_closed():
                self.store.finish(challenge_id, "cancelled")
                _record_captcha_event(self.store, "manual_cancelled")
                raise OwnerInterventionCancelled
            answer = self.store.take_answer(challenge_id)
            if answer is not None:
                _record_captcha_event(self.store, "manual_answer_received")
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


def _try_gemini_answers(
    page: Page,
    answer_source: CaptchaAnswerSource | None,
    stop_requested: Callable[[], bool],
    *,
    fallback_message: str,
    wait_for_result: Callable[[Page, Callable[[], bool]], bool] | None = None,
    audit_store: OwnerInterventionStore | None = None,
) -> bool:
    """Проверить до пяти ответов Gemini перед доступным ручным fallback."""

    if answer_source is None:
        _record_captcha_event(audit_store, "gemini_disabled")
        return False

    for attempt in range(1, _GEMINI_ATTEMPTS + 1):
        if stop_requested() or page.is_closed() or not CaptchaGuard.is_present(page):
            return False
        _record_captcha_event(audit_store, "gemini_attempt_started", attempt=attempt)
        image, input_field, submit = captcha_controls(page)
        try:
            for control in (image, input_field, submit):
                control.wait_for(state="visible", timeout=_FORM_WAIT_MILLISECONDS)
        except PlaywrightTimeoutError:
            _record_captcha_event(audit_store, "gemini_controls_unavailable", attempt=attempt)
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
            _record_captcha_event(audit_store, "gemini_no_answer", attempt=attempt)
            continue
        LOGGER.info(
            "Пробую ответ Gemini: попытка %s из %s.",
            attempt,
            _GEMINI_ATTEMPTS,
            extra=event_data(LogEvent.CAPTCHA),
        )
        input_field.fill(answer)
        submit.click()
        _record_captcha_event(
            audit_store,
            "gemini_answer_submitted",
            attempt=attempt,
            answer=answer,
        )
        result_waiter = wait_for_result or _wait_for_captcha_result
        if result_waiter(page, stop_requested):
            _record_captcha_event(audit_store, "gemini_resolved", attempt=attempt)
            LOGGER.info("Ответ Gemini принят HH.", extra=event_data(LogEvent.CAPTCHA))
            return True
        LOGGER.warning(
            "HH не принял ответ Gemini: попытка %s из %s.",
            attempt,
            _GEMINI_ATTEMPTS,
            extra=event_data(LogEvent.CAPTCHA),
        )
        _record_captcha_event(audit_store, "gemini_rejected", attempt=attempt)

    LOGGER.warning(
        "Gemini не решил CAPTCHA за %s попыток; %s.",
        _GEMINI_ATTEMPTS,
        fallback_message,
        extra=event_data(LogEvent.CAPTCHA),
    )
    _record_captcha_event(audit_store, "gemini_fallback")
    return False


def _record_captcha_event(
    store: OwnerInterventionStore | None,
    event: str,
    *,
    attempt: int | None = None,
    answer: str | None = None,
) -> None:
    """Записать событие CAPTCHA и ответ Gemini в SQLite и journalctl."""

    normalized_answer = " ".join(answer.split()) if answer is not None else None
    if store is not None:
        store.record_captcha_event(event, attempt=attempt, answer=normalized_answer)
    suffix = f", попытка {attempt} из {_GEMINI_ATTEMPTS}" if attempt is not None else ""
    answer_suffix = f", ответ Gemini: {normalized_answer}" if normalized_answer else ""
    LOGGER.info(
        "CAPTCHA: %s%s%s.",
        event,
        suffix,
        answer_suffix,
        extra=event_data(LogEvent.CAPTCHA),
    )


def _wait_for_captcha_result(page: Page, stop_requested: Callable[[], bool]) -> bool:
    """Дождаться принятия автоматического ответа HH."""

    elapsed = 0
    while elapsed < _RESULT_WAIT_MILLISECONDS:
        if stop_requested() or page.is_closed():
            raise OwnerInterventionCancelled
        page.wait_for_timeout(_POLL_MILLISECONDS)
        elapsed += _POLL_MILLISECONDS
        if not CaptchaGuard.is_present(page):
            return True
    return False


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
