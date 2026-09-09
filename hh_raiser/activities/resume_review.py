from __future__ import annotations

from typing import TYPE_CHECKING

from hh_raiser.browser import is_closed_playwright_error
from hh_raiser.domain.action import ActivityKind
from hh_raiser.domain.result import ActivityResult, ActivityStatus
from hh_raiser.infrastructure.browser.modal_guard import dismiss_hh_pro_modal
from hh_raiser.infrastructure.hh.selectors import (
    PROFILE_EDUCATION,
    PROFILE_EXPERIENCE,
    PROFILE_LANGUAGE,
    PROFILE_URL,
    RESUME_CARD,
    RESUME_RECOMMENDATION,
)

if TYPE_CHECKING:
    from playwright.sync_api import Page

from playwright.sync_api import Error as PlaywrightError


def review_resume(page: Page) -> ActivityResult:
    try:
        page.goto(PROFILE_URL, wait_until="domcontentloaded")
        dismiss_hh_pro_modal(page)
        if not page.locator(RESUME_CARD).count():
            return ActivityResult(
                action=ActivityKind.REVIEW_RESUME,
                status=ActivityStatus.UNKNOWN,
                detail="Карточка резюме не распознана; изменения не предлагались.",
            )
        checks = {
            "experience": page.locator(PROFILE_EXPERIENCE).count() > 0,
            "education": page.locator(PROFILE_EDUCATION).count() > 0,
            "languages": page.locator(PROFILE_LANGUAGE).count() > 0,
            "hh_recommendation": page.locator(RESUME_RECOMMENDATION).count() > 0,
        }
        suggestions: list[str] = []
        if not checks["experience"]:
            suggestions.append("Проверить и при необходимости добавить актуальный опыт работы.")
        if not checks["education"]:
            suggestions.append("Проверить полноту раздела образования.")
        if not checks["languages"]:
            suggestions.append("Указать реальные языки и подтверждённые уровни владения.")
        if checks["hh_recommendation"]:
            suggestions.append(
                "Вручную проверить рекомендацию HH и применить только содержательную правку."
            )
        return ActivityResult(
            action=ActivityKind.REVIEW_RESUME,
            status=ActivityStatus.SUCCESS,
            detail=(
                "; ".join(suggestions)
                if suggestions
                else "Обязательные разделы присутствуют; явных структурных правок не найдено."
            ),
            metadata={"suggestion_count": len(suggestions), **checks},
        )
    except PlaywrightError as error:
        if is_closed_playwright_error(error):
            raise
        return ActivityResult(
            action=ActivityKind.REVIEW_RESUME,
            status=ActivityStatus.ERROR,
            detail=f"Не удалось проверить структуру резюме: {error.__class__.__name__}",
        )
