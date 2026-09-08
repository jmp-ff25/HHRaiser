from __future__ import annotations

import argparse
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING

from hh_raiser.credentials import normalize_russian_phone, resolve_credentials
from hh_raiser.infrastructure.browser.modal_guard import dismiss_hh_pro_modal
from hh_raiser.infrastructure.browser.page_state_reader import redact_url
from hh_raiser.logging_config import LOGGER
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

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

_CLOSED_PLAYWRIGHT_ERROR_MARKERS = (
    "target page, context or browser has been closed",
    "connection closed while reading from the driver",
)
_DOM_RECHECK_INTERVAL_MS = 1_000


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


def wait_for_manual_login(page: Page) -> None:
    LOGGER.warning("HH запросил код, CAPTCHA или дополнительное подтверждение.")
    LOGGER.info("Завершите вход в открытом окне браузера.")
    while True:
        answer = input("После завершения нажмите Enter; для выхода введите q: ").strip().lower()
        if answer == "q":
            raise RuntimeError("Вход в HH отменён пользователем")
        if "/applicant/profile/" in page.url:
            return
        page.goto(PROFILE_URL, wait_until="domcontentloaded")
        if wait_for_login_action(page, timeout=10) == "authenticated":
            return
        LOGGER.info("Сессия ещё не подтверждена. Текущая страница: %s", page.url)


def login_if_needed(page: Page, args: argparse.Namespace) -> None:
    page.goto(PROFILE_URL, wait_until="domcontentloaded")
    if "/applicant/profile/" in page.url:
        return
    credentials = resolve_credentials(args)
    action = wait_for_login_action(page)
    if action == "open-login-form":
        page.get_by_role("button", name="Войти", exact=True).click()
        action = wait_for_login_action(page, previous="open-login-form")
    if action != "fill-phone":
        wait_for_manual_login(page)
        return
    page.locator('[data-qa="magritte-phone-input-national-number-input"]').first.fill(
        normalize_russian_phone(credentials.phone)
    )
    page.get_by_role("button", name=re.compile(r"Войти с\s+паролем", re.IGNORECASE)).first.click()
    if wait_for_login_action(page, previous="fill-phone") != "fill-password":
        wait_for_manual_login(page)
        return
    password_input = page.locator(
        '[data-qa="applicant-login-input-password"], '
        '[data-qa="login-input-password"], input[name="password"], input[type="password"]'
    ).first
    password_input.fill(credentials.password)
    submit = page.get_by_role("button", name="Войти", exact=True).first
    submit.click() if submit.count() and submit.is_visible() else password_input.press("Enter")
    if wait_for_login_action(page, previous="fill-password", timeout=30) != "authenticated":
        wait_for_manual_login(page)


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
        LOGGER.debug("Browser shutdown was interrupted; continuing process cleanup.")
    except Exception as error:
        if not is_closed_playwright_error(error):
            raise
        LOGGER.debug("Browser context was already closed during shutdown: %s", error)


def is_closed_playwright_error(error: BaseException) -> bool:
    return error.__class__.__name__ == "TargetClosedError" or any(
        marker in str(error).lower() for marker in _CLOSED_PLAYWRIGHT_ERROR_MARKERS
    )
