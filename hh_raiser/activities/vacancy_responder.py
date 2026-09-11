from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

from hh_raiser.browser import is_closed_playwright_error
from hh_raiser.domain.action import ActivityKind
from hh_raiser.domain.result import ActivityResult, ActivityStatus
from hh_raiser.domain.vacancy_response import ManualResponseReason, VacancyResponseStatus
from hh_raiser.infrastructure.browser.modal_guard import dismiss_hh_pro_modal
from hh_raiser.infrastructure.hh.selectors import (
    RESPOND_BUTTON,
    RESPONSE_LETTER_INPUT,
    RESPONSE_QUESTION_INPUT,
    RESPONSE_SUBMIT_BUTTON,
    RESPONSE_SUCCESS,
)
from hh_raiser.infrastructure.storage.vacancy_history import vacancy_id_from_url
from hh_raiser.logging_config import LOGGER

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

from playwright.sync_api import Error as PlaywrightError

_HH_HOST = "hh.ru"
_SUCCESS_TEXT = re.compile(r"(?:отклик отправлен|вы откликнулись)", re.IGNORECASE)
_QUESTIONNAIRE_TEXT = re.compile(
    r"(?:заполн(?:ить|ите) анкету|ответ(?:ить|ьте) на вопрос|вопросы работодателя)",
    re.IGNORECASE,
)
_EXTERNAL_TEXT = re.compile(
    r"(?:перейти на сайт работодателя|внешн(?:ий|яя) сайт|google forms|google-форм)",
    re.IGNORECASE,
)
_AUTH_TEXT = re.compile(r"(?:войти|авторизоваться|номер телефона)", re.IGNORECASE)


@dataclass(frozen=True)
class _PreflightSignal:
    status: VacancyResponseStatus
    detail: str
    manual_reason: ManualResponseReason | None = None


def respond_to_vacancy(
    page: Page,
    *,
    vacancy_url: str,
    vacancy_title: str,
) -> ActivityResult:
    """Send only a form-free HH response and stop on every ambiguous branch."""
    vacancy_id = vacancy_id_from_url(vacancy_url)
    if vacancy_id is None:
        return _result(
            VacancyResponseStatus.ERROR,
            "Некорректный публичный адрес вакансии; отклик не выполнялся.",
            vacancy_title=vacancy_title,
        )

    try:
        dismiss_hh_pro_modal(page)
        existing = _existing_response_visible(page)
        if existing:
            return _result(
                VacancyResponseStatus.ALREADY_SENT,
                "HH уже показывает отправленный отклик на эту вакансию.",
                vacancy_id=vacancy_id,
                vacancy_title=vacancy_title,
            )

        preflight = _read_public_response_requirements(page, vacancy_id)
        if preflight is not None:
            if preflight.manual_reason is not None:
                return _manual_result(
                    preflight.manual_reason,
                    preflight.detail,
                    vacancy_id=vacancy_id,
                    vacancy_title=vacancy_title,
                )
            return _result(
                preflight.status,
                preflight.detail,
                vacancy_id=vacancy_id,
                vacancy_title=vacancy_title,
            )

        response_button = page.locator(RESPOND_BUTTON).first
        if not _visible(response_button):
            response_button = page.get_by_role("button", name="Откликнуться", exact=True).first
        if not _visible(response_button):
            return _result(
                VacancyResponseStatus.UNAVAILABLE,
                "На странице не найдена доступная кнопка отклика.",
                vacancy_id=vacancy_id,
                vacancy_title=vacancy_title,
            )

        destination = _trusted_destination(page.url, response_button.get_attribute("href"))
        if destination is False:
            return _manual_result(
                ManualResponseReason.EXTERNAL_SITE,
                "Отклик ведёт на внешний сайт работодателя; переход оставлен владельцу.",
                vacancy_id=vacancy_id,
                vacancy_title=vacancy_title,
            )

        response_button.click(timeout=10_000)
        page.wait_for_timeout(1_000)
        if not _is_hh_url(page.url):
            return _manual_result(
                ManualResponseReason.EXTERNAL_SITE,
                "После нажатия HH перенаправил на внешний сайт; заполнение оставлено владельцу.",
                vacancy_id=vacancy_id,
                vacancy_title=vacancy_title,
            )
        if _existing_response_visible(page):
            return _sent_result(vacancy_id, vacancy_title)

        manual = _read_manual_requirement(page)
        if manual is not None:
            reason, detail = manual
            return _manual_result(
                reason,
                detail,
                vacancy_id=vacancy_id,
                vacancy_title=vacancy_title,
            )

        submit = page.locator(RESPONSE_SUBMIT_BUTTON).first
        if _visible(submit) and submit.is_enabled():
            submit.click(timeout=10_000)
            page.wait_for_timeout(1_000)
            if _existing_response_visible(page):
                return _sent_result(vacancy_id, vacancy_title)
            manual = _read_manual_requirement(page)
            if manual is not None:
                reason, detail = manual
                return _manual_result(
                    reason,
                    detail,
                    vacancy_id=vacancy_id,
                    vacancy_title=vacancy_title,
                )

        return _result(
            VacancyResponseStatus.UNKNOWN,
            "После единственного безопасного нажатия HH не показал ни подтверждение, ни "
            "распознанную форму. Повторный отклик автоматически не выполняется.",
            vacancy_id=vacancy_id,
            vacancy_title=vacancy_title,
        )
    except PlaywrightError as error:
        if is_closed_playwright_error(error):
            raise
        return _result(
            VacancyResponseStatus.ERROR,
            f"Не удалось проверить отклик: {error.__class__.__name__}.",
            vacancy_id=vacancy_id,
            vacancy_title=vacancy_title,
        )


def _read_public_response_requirements(
    page: Page,
    vacancy_id: str,
) -> _PreflightSignal | None:
    """Use the public HH API as a preflight signal, never as success confirmation."""
    try:
        response = page.request.get(
            f"https://api.hh.ru/vacancies/{vacancy_id}",
            timeout=10_000,
            fail_on_status_code=False,
        )
        if not response.ok:
            return None
        payload = response.json()
        if "got_response" in (payload.get("relations") or []):
            return _PreflightSignal(
                VacancyResponseStatus.ALREADY_SENT,
                "HH API сообщает, что на эту вакансию уже был отправлен отклик.",
            )
        if payload.get("has_test"):
            return _PreflightSignal(
                VacancyResponseStatus.MANUAL_REQUIRED,
                "Для вакансии указан обязательный тест; отклик оставлен владельцу.",
                ManualResponseReason.TEST,
            )
        if payload.get("response_letter_required"):
            return _PreflightSignal(
                VacancyResponseStatus.MANUAL_REQUIRED,
                "Работодатель требует сопроводительное письмо; текст автоматически не создаётся.",
                ManualResponseReason.COVER_LETTER,
            )
        response_url = payload.get("response_url")
        if (
            isinstance(response_url, str)
            and response_url
            and not _is_hh_url(urljoin(page.url, response_url))
        ):
            return _PreflightSignal(
                VacancyResponseStatus.MANUAL_REQUIRED,
                "Вакансия использует отдельный адрес отклика; переход оставлен владельцу.",
                ManualResponseReason.EXTERNAL_SITE,
            )
    except (PlaywrightError, TypeError, ValueError):
        return None
    return None


def _read_manual_requirement(
    page: Page,
) -> tuple[ManualResponseReason, str] | None:
    if _any_visible(page.locator(RESPONSE_QUESTION_INPUT)) or _text_visible(
        page, _QUESTIONNAIRE_TEXT
    ):
        return (
            ManualResponseReason.QUESTIONNAIRE,
            "HH показал вопросы или анкету работодателя; ответы должен заполнить кандидат.",
        )
    if _text_visible(page, _EXTERNAL_TEXT):
        return (
            ManualResponseReason.EXTERNAL_SITE,
            "HH предлагает продолжить на внешнем сайте; переход оставлен владельцу.",
        )
    letter = page.locator(RESPONSE_LETTER_INPUT).first
    submit = page.locator(RESPONSE_SUBMIT_BUTTON).first
    if _visible(letter) and (
        letter.get_attribute("required") is not None
        or letter.get_attribute("aria-required") == "true"
        or (_visible(submit) and not submit.is_enabled())
    ):
        return (
            ManualResponseReason.COVER_LETTER,
            "Форма требует сопроводительное письмо; его должен написать кандидат.",
        )
    resume_selector = page.locator('[data-qa*="resume-select"], [data-qa*="resume-selector"]').first
    if _visible(resume_selector):
        return (
            ManualResponseReason.RESUME_SELECTION,
            "HH просит выбрать резюме вручную; автоматический выбор не выполнялся.",
        )
    if _text_visible(page, _AUTH_TEXT) and (
        "/account/" in page.url or "/applicant/vacancy_response" in page.url
    ):
        return (
            ManualResponseReason.AUTHENTICATION,
            "HH запросил вход или подтверждение контактных данных.",
        )
    required_fields = page.locator(
        "form input[required]:visible, form textarea[required]:visible, "
        "form select[required]:visible"
    )
    if required_fields.count() > 0:
        return (
            ManualResponseReason.OTHER_FORM,
            "Перед отправкой обнаружены обязательные поля; их должен заполнить кандидат.",
        )
    return None


def _existing_response_visible(page: Page) -> bool:
    return _any_visible(page.locator(RESPONSE_SUCCESS)) or _text_visible(page, _SUCCESS_TEXT)


def _text_visible(page: Page, pattern: re.Pattern[str]) -> bool:
    return _any_visible(page.get_by_text(pattern).all())


def _any_visible(locators: Locator | list[Locator]) -> bool:
    items = locators if isinstance(locators, list) else locators.all()
    return any(_visible(locator) for locator in items[:10])


def _visible(locator: Locator) -> bool:
    try:
        return locator.count() > 0 and locator.is_visible(timeout=0)
    except PlaywrightError:
        return False


def _trusted_destination(current_url: str, href: str | None) -> bool | None:
    if not href:
        return None
    return _is_hh_url(urljoin(current_url, href))


def _is_hh_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == _HH_HOST or host.endswith(f".{_HH_HOST}")


def _sent_result(vacancy_id: str, vacancy_title: str) -> ActivityResult:
    LOGGER.info("Отклик на вакансию «%s» успешно отправлен.", vacancy_title)
    return _result(
        VacancyResponseStatus.SENT,
        "HH показал подтверждение успешного отклика.",
        vacancy_id=vacancy_id,
        vacancy_title=vacancy_title,
    )


def _manual_result(
    reason: ManualResponseReason,
    detail: str,
    *,
    vacancy_id: str,
    vacancy_title: str,
) -> ActivityResult:
    LOGGER.info("Отклик на вакансию «%s» требует участия кандидата: %s", vacancy_title, detail)
    return _result(
        VacancyResponseStatus.MANUAL_REQUIRED,
        detail,
        vacancy_id=vacancy_id,
        vacancy_title=vacancy_title,
        manual_reason=reason,
    )


def _result(
    response_status: VacancyResponseStatus,
    detail: str,
    *,
    vacancy_title: str,
    vacancy_id: str = "",
    manual_reason: ManualResponseReason | None = None,
) -> ActivityResult:
    activity_status = {
        VacancyResponseStatus.SENT: ActivityStatus.SUCCESS,
        VacancyResponseStatus.MANUAL_REQUIRED: ActivityStatus.SKIPPED,
        VacancyResponseStatus.ALREADY_SENT: ActivityStatus.SKIPPED,
        VacancyResponseStatus.UNAVAILABLE: ActivityStatus.SKIPPED,
        VacancyResponseStatus.UNKNOWN: ActivityStatus.UNKNOWN,
        VacancyResponseStatus.ERROR: ActivityStatus.ERROR,
    }[response_status]
    return ActivityResult(
        action=ActivityKind.RESPOND_VACANCY,
        status=activity_status,
        detail=detail,
        metadata={
            "vacancy_id": vacancy_id,
            "vacancy_title": vacancy_title,
            "response_status": response_status.value,
            "manual_reason": manual_reason.value if manual_reason else None,
        },
    )
