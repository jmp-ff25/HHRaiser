from __future__ import annotations

from typing import TYPE_CHECKING

from hh_raiser.logging_config import LOGGER, LogEvent, event_data

if TYPE_CHECKING:
    from playwright.sync_api import Page


def read_resume_text(page: Page, resume_title: str) -> str:
    """Read the current profile in memory without persisting personal text."""
    heading = page.get_by_role("heading", name=resume_title, exact=True).first
    if not heading.count() or not heading.is_visible(timeout=0):
        LOGGER.warning(
            "Текст резюме «%s» не распознан; фильтрация вакансий будет пропущена.",
            resume_title,
            extra=event_data(LogEvent.RESUME, resume_title=resume_title),
        )
        return ""
    body = page.locator("body")
    return body.inner_text() if body.count() else ""
