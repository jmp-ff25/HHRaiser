from __future__ import annotations

import argparse
import queue
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from hh_raiser.credentials import normalize_russian_phone, resolve_credentials
from hh_raiser.infrastructure.browser.modal_guard import dismiss_hh_pro_modal
from hh_raiser.infrastructure.browser.page_state_reader import redact_url
from hh_raiser.logging_config import LOGGER, LogEvent, event_data
from hh_raiser.models import (
    MOSCOW,
    PROFILE_URL,
    RAISE_BUTTON_SELECTOR,
    LoginEvidence,
    PageState,
)
from hh_raiser.scheduling import decide_page_state

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Locator, Page, Response

    from hh_raiser.infrastructure.browser.captcha_guard import CaptchaResolver

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

_CLOSED_PLAYWRIGHT_ERROR_MARKERS = (
    "target page, context or browser has been closed",
    "connection closed while reading from the driver",
)
_DOM_RECHECK_INTERVAL_MS = 1_000
_PAGE_CLOSE_POLL_SECONDS = 0.25
_MANUAL_LOGIN_POLL_SECONDS = 0.25


class ManualLoginCancelled(RuntimeError):
    """Ручной вход не завершён: пользователь остановил сценарий или закрыл окно."""


def choose_login_action(evidence: LoginEvidence) -> str:
    if evidence.password_input:
        return "fill-password"
    if evidence.phone_input:
        return "fill-phone"
    if evidence.landing_button:
        return "open-login-form"
    return "manual"


def read_login_evidence(page: Page) -> LoginEvidence:
    phone_input = page.locator('[data-qa="magritte-phone-input-national-number-input"]').first
    password_input = page.locator(
        '[data-qa="applicant-login-input-password"], '
        '[data-qa="login-input-password"], input[name="password"], input[type="password"]'
    ).first
    landing_button = page.get_by_role("button", name="Войти", exact=True).first
    return LoginEvidence(
        landing_button=landing_button.count() > 0
        and landing_button.is_visible()
        and not (phone_input.count() and phone_input.is_visible())
        and not (password_input.count() and password_input.is_visible()),
        phone_input=phone_input.count() > 0 and phone_input.is_visible(),
        password_input=password_input.count() > 0 and password_input.is_visible(),
    )


def wait_for_login_action(page: Page, *, previous: str | None = None, timeout: float = 20) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if "/applicant/profile/" in page.url:
            return "authenticated"
        action = choose_login_action(read_login_evidence(page))
        if action != "manual" and action != previous:
            return action
        page.wait_for_timeout(200)
    return "manual"


def _read_manual_login_answer(prompt: str) -> str | None:
    """Прочитать ответ владельца, не превращая закрытый stdin в traceback."""
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return None


def _manual_login_answers() -> queue.Queue[str | None]:
    """Запустить ожидание консольного ответа в daemon-потоке."""
    answers: queue.Queue[str | None] = queue.Queue(maxsize=1)
    prompt = "После завершения нажмите Enter; для выхода введите q: "
    threading.Thread(
        target=lambda: answers.put(_read_manual_login_answer(prompt)),
        name="hh-manual-login-input",
        daemon=True,
    ).start()
    return answers


def wait_for_manual_login(
    page: Page,
    *,
    stop_requested: Callable[[], bool] | None = None,
) -> None:
    LOGGER.warning(
        "HH запросил код, CAPTCHA или дополнительное подтверждение.",
        extra=event_data(LogEvent.AUTH),
    )
    LOGGER.info(
        "Завершите вход в открытом окне браузера.",
        extra=event_data(LogEvent.AUTH),
    )

    if stop_requested is not None and stop_requested():
        raise ManualLoginCancelled("Вход в HH остановлен пользователем.")
    if page.is_closed():
        raise ManualLoginCancelled("Окно браузера закрыто до завершения входа в HH.")
    if "/applicant/profile/" in page.url:
        return

    answers = _manual_login_answers()
    while True:
        if stop_requested is not None and stop_requested():
            raise ManualLoginCancelled("Вход в HH остановлен пользователем.")
        if page.is_closed():
            raise ManualLoginCancelled("Окно браузера закрыто до завершения входа в HH.")
        if "/applicant/profile/" in page.url:
            return
        if wait_for_page_close(
            page,
            _MANUAL_LOGIN_POLL_SECONDS,
            stop_requested=stop_requested,
        ):
            if stop_requested is not None and stop_requested():
                raise ManualLoginCancelled("Вход в HH остановлен пользователем.")
            raise ManualLoginCancelled("Окно браузера закрыто до завершения входа в HH.")
        try:
            answer = answers.get_nowait()
        except queue.Empty:
            continue
        if answer is None:
            raise ManualLoginCancelled("Ввод в терминале закрыт; вход в HH отменён.")
        if answer == "q":
            raise ManualLoginCancelled("Вход в HH отменён пользователем.")
        page.goto(PROFILE_URL, wait_until="domcontentloaded")
        if wait_for_login_action(page, timeout=10) == "authenticated":
            return
        LOGGER.info(
            "Сессия ещё не подтверждена. Текущая страница: %s",
            page.url,
            extra=event_data(LogEvent.AUTH),
        )


def login_if_needed(
    page: Page,
    args: argparse.Namespace,
    *,
    captcha_guard: CaptchaResolver | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> None:
    from hh_raiser.infrastructure.browser.captcha_guard import resolve_captcha

    page.goto(PROFILE_URL, wait_until="domcontentloaded")
    if resolve_captcha(captcha_guard, page, stop_requested=stop_requested):
        page.goto(PROFILE_URL, wait_until="domcontentloaded")
    if "/applicant/profile/" in page.url:
        return
    credentials = resolve_credentials(args)
    action = wait_for_login_action(page)
    if action == "open-login-form":
        page.get_by_role("button", name="Войти", exact=True).click()
        action = wait_for_login_action(page, previous="open-login-form")
    if action != "fill-phone":
        if resolve_captcha(captcha_guard, page, stop_requested=stop_requested):
            login_if_needed(
                page,
                args,
                captcha_guard=captcha_guard,
                stop_requested=stop_requested,
            )
            return
        wait_for_manual_login(page, stop_requested=stop_requested)
        return
    page.locator('[data-qa="magritte-phone-input-national-number-input"]').first.fill(
        normalize_russian_phone(credentials.phone)
    )
    page.get_by_role("button", name=re.compile(r"Войти с\s+паролем", re.IGNORECASE)).first.click()
    if wait_for_login_action(page, previous="fill-phone") != "fill-password":
        if resolve_captcha(captcha_guard, page, stop_requested=stop_requested):
            login_if_needed(
                page,
                args,
                captcha_guard=captcha_guard,
                stop_requested=stop_requested,
            )
            return
        wait_for_manual_login(page, stop_requested=stop_requested)
        return
    password_input = page.locator(
        '[data-qa="applicant-login-input-password"], '
        '[data-qa="login-input-password"], input[name="password"], input[type="password"]'
    ).first
    password_input.fill(credentials.password)
    submit = page.get_by_role("button", name="Войти", exact=True).first
    submit.click() if submit.count() and submit.is_visible() else password_input.press("Enter")
    if wait_for_login_action(page, previous="fill-password", timeout=30) != "authenticated":
        if resolve_captcha(captcha_guard, page, stop_requested=stop_requested):
            login_if_needed(
                page,
                args,
                captcha_guard=captcha_guard,
                stop_requested=stop_requested,
            )
            return
        wait_for_manual_login(page, stop_requested=stop_requested)


def find_raise_button(page: Page, resume_title: str) -> Locator | None:
    title = page.get_by_role("heading", name=resume_title, exact=True).first
    if title.count():
        card = title.locator(
            "xpath=ancestor::*[.//*[@data-qa and "
            "contains(concat(' ', normalize-space(@data-qa), ' '), "
            "' resume-update-button ')]][1]"
        )
        scoped = card.locator(RAISE_BUTTON_SELECTOR)
        if scoped.count() == 1:
            return scoped
    page_buttons = page.locator(RAISE_BUTTON_SELECTOR)
    return page_buttons if page_buttons.count() == 1 else None


def read_page_state(page: Page, resume_title: str) -> tuple[PageState, Locator | None]:
    button = find_raise_button(page, resume_title)
    visible = is_raise_button_usable(button)
    state = decide_page_state(
        button_visible=visible,
        page_text=page.locator("body").inner_text(),
        now=datetime.now(MOSCOW),
    )
    return state, button


def is_raise_button_usable(button: Locator | None) -> bool:
    if button is None:
        return False
    try:
        return button.is_visible(timeout=0) and button.is_enabled(timeout=0)
    except PlaywrightTimeoutError:
        return False


def wait_for_profile_content(
    page: Page,
    resume_title: str,
    *,
    page_refresh_seconds: int,
) -> None:
    while True:
        dismiss_hh_pro_modal(page)
        deadline = time.monotonic() + page_refresh_seconds
        heading = page.get_by_role("heading", name=resume_title, exact=True)
        while time.monotonic() < deadline:
            dismiss_hh_pro_modal(page)
            if heading.is_visible(timeout=0):
                return
            page.wait_for_timeout(_DOM_RECHECK_INTERVAL_MS)
        LOGGER.warning(
            "Резюме «%s» не появилось за %s секунд; перезагружаю страницу.",
            resume_title,
            page_refresh_seconds,
            extra=event_data(LogEvent.RESUME, resume_title=resume_title),
        )
        page.reload(wait_until="domcontentloaded")


def wait_for_profile_raise_state(
    page: Page,
    resume_title: str,
    *,
    page_refresh_seconds: int,
) -> tuple[PageState, Locator | None]:
    return wait_for_recognized_state(
        page,
        resume_title,
        accepted_states={"available", "waiting"},
        page_refresh_seconds=page_refresh_seconds,
    )


def wait_for_post_click_state(
    page: Page,
    resume_title: str,
    *,
    page_refresh_seconds: int,
) -> PageState:
    state, _ = wait_for_recognized_state(
        page,
        resume_title,
        accepted_states={"waiting"},
        page_refresh_seconds=page_refresh_seconds,
    )
    return state


def wait_for_recognized_state(
    page: Page,
    resume_title: str,
    *,
    accepted_states: set[str],
    page_refresh_seconds: int,
) -> tuple[PageState, Locator | None]:
    while True:
        dismiss_hh_pro_modal(page)
        deadline = time.monotonic() + page_refresh_seconds
        while time.monotonic() < deadline:
            dismiss_hh_pro_modal(page)
            state, button = read_page_state(page, resume_title)
            if state.kind in accepted_states:
                return state, button
            page.wait_for_timeout(_DOM_RECHECK_INTERVAL_MS)
        LOGGER.warning(
            "Не удалось определить состояние резюме за %s секунд; перезагружаю страницу.",
            page_refresh_seconds,
            extra=event_data(LogEvent.RESUME),
        )
        page.reload(wait_until="domcontentloaded")


class NetworkCapture:
    def __init__(self) -> None:
        self.enabled = False
        self.events: list[str] = []

    def observe(self, response: Response) -> None:
        if not self.enabled or not response.url.startswith("https://hh.ru/"):
            return
        request = response.request
        if request.resource_type in {"xhr", "fetch"}:
            self.events.append(f"{request.method} {response.status} {redact_url(response.url)}")


def close_context_quietly(context: BrowserContext) -> None:
    try:
        context.close()
    except KeyboardInterrupt:
        LOGGER.debug(
            "Browser shutdown was interrupted; continuing process cleanup.",
            extra=event_data(LogEvent.BROWSER),
        )
    except Exception as error:
        if not is_closed_playwright_error(error):
            raise
        LOGGER.debug(
            "Browser context was already closed during shutdown: %s",
            error,
            extra=event_data(LogEvent.BROWSER),
        )


def is_closed_playwright_error(error: BaseException) -> bool:
    return error.__class__.__name__ == "TargetClosedError" or any(
        marker in str(error).lower() for marker in _CLOSED_PLAYWRIGHT_ERROR_MARKERS
    )


def wait_for_page_close(
    page: Page,
    timeout_seconds: float,
    *,
    stop_requested: Callable[[], bool] | None = None,
) -> bool:
    """Keep Playwright responsive while waiting and report any requested stop."""
    deadline = time.monotonic() + max(0, timeout_seconds)
    while True:
        if stop_requested is not None and stop_requested():
            return True
        if page.is_closed():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            page.wait_for_event(
                "close",
                timeout=max(1, min(_PAGE_CLOSE_POLL_SECONDS, remaining) * 1_000),
            )
            return True
        except PlaywrightTimeoutError:
            continue
