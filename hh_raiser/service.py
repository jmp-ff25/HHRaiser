from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from hh_raiser.browser import (
    NetworkCapture,
    wait_for_post_click_state,
    wait_for_profile_content,
    wait_for_profile_raise_state,
)
from hh_raiser.infrastructure.browser.modal_guard import (
    dismiss_hh_pro_modal,
    hh_pro_modal_visible,
)
from hh_raiser.logging_config import LOGGER
from hh_raiser.models import MOSCOW, PROFILE_URL
from hh_raiser.storage import guarded_until, write_attempt_guard, write_next_raise_time

if TYPE_CHECKING:
    from playwright.sync_api import Page

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


def run_cycle(
    page: Page,
    *,
    resume_title: str,
    profile_dir: Path,
    dry_run: bool,
    capture: NetworkCapture,
    minimum_cooldown: timedelta,
    page_refresh_seconds: int,
) -> datetime | None:
    page.goto(PROFILE_URL, wait_until="domcontentloaded")
    dismiss_hh_pro_modal(page)
    wait_for_profile_content(
        page,
        resume_title,
        page_refresh_seconds=page_refresh_seconds,
    )
    state, button = wait_for_profile_raise_state(
        page,
        resume_title,
        page_refresh_seconds=page_refresh_seconds,
    )
    LOGGER.info("Состояние резюме «%s»: %s", resume_title, state.kind)
    if state.kind == "waiting":
        if state.next_at is None:
            return None
        write_next_raise_time(profile_dir, state.next_at)
        LOGGER.info("Следующее поднятие доступно: %s", state.next_at.strftime("%Y-%m-%d %H:%M %Z"))
        return state.next_at
    if state.kind != "available" or button is None:
        LOGGER.warning("Не удалось распознать кнопку или время; повторю только чтение позже.")
        return None
    if dry_run:
        LOGGER.info("Кнопка доступна; --dry-run запрещает нажатие.")
        return None
    protected_until = guarded_until(profile_dir, minimum_cooldown)
    if protected_until:
        write_next_raise_time(profile_dir, protected_until)
        LOGGER.info(
            "Защита от повтора активна до %s.", protected_until.strftime("%Y-%m-%d %H:%M %Z")
        )
        return protected_until
    dismiss_hh_pro_modal(page)
    if hh_pro_modal_visible(page):
        LOGGER.warning("Окно hh PRO осталось открытым; откладываю поднятие без клика.")
        return None
    try:
        button.click(trial=True, timeout=5_000)
    except PlaywrightTimeoutError:
        LOGGER.warning("Кнопка поднятия перекрыта или недоступна; повторю проверку позже.")
        return None
    attempted_at = datetime.now(MOSCOW)
    write_attempt_guard(profile_dir, attempted_at)
    capture.enabled = True
    try:
        button.click(timeout=10_000)
        result = wait_for_post_click_state(
            page,
            resume_title,
            page_refresh_seconds=page_refresh_seconds,
        )
    finally:
        capture.enabled = False
    if capture.events:
        LOGGER.info("XHR/fetch после единственного клика:")
        for event in dict.fromkeys(capture.events):
            LOGGER.info("  %s", event)
    if result.kind == "waiting" and result.next_at is not None:
        write_next_raise_time(profile_dir, result.next_at)
        LOGGER.info(
            "Резюме поднято. Следующая попытка: %s", result.next_at.strftime("%Y-%m-%d %H:%M %Z")
        )
        return result.next_at
    fallback = attempted_at + minimum_cooldown
    write_next_raise_time(profile_dir, fallback)
    LOGGER.warning(
        "Клик был отправлен, но время не распознано; повтор защищён до %s",
        fallback.strftime("%Y-%m-%d %H:%M %Z"),
    )
    return fallback
