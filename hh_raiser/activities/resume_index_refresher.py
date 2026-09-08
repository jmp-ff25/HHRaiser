from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from hh_raiser.domain.action import ActivityKind
from hh_raiser.domain.result import ActivityResult, ActivityStatus
from hh_raiser.infrastructure.browser.modal_guard import dismiss_hh_pro_modal
from hh_raiser.infrastructure.hh.selectors import (
    EXPERIENCE_DESCRIPTION_INPUT,
    EXPERIENCE_EDIT_BUTTON,
    PROFILE_SAVE_BUTTON,
    PROFILE_URL,
)
from hh_raiser.models import MOSCOW
from hh_raiser.storage import write_resume_refresh_attempt

if TYPE_CHECKING:
    from playwright.sync_api import Page

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


@dataclass(frozen=True)
class ResumeMarkerState:
    target_index: int
    base_hash: str
    marked_hash: str


def _marker_path(profile_dir: Path) -> Path:
    return profile_dir / "resume-refresh-marker.json"


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_marked_description(value: str, *, target_index: int) -> tuple[str, ResumeMarkerState]:
    marked_value = f"{value}."
    return marked_value, ResumeMarkerState(
        target_index=target_index,
        base_hash=_text_hash(value),
        marked_hash=_text_hash(marked_value),
    )


def restore_marked_description(value: str, marker: ResumeMarkerState) -> str | None:
    if _text_hash(value) != marker.marked_hash or not value.endswith("."):
        return None
    restored_value = value[:-1]
    return restored_value if _text_hash(restored_value) == marker.base_hash else None


def _read_marker(profile_dir: Path) -> ResumeMarkerState | None:
    try:
        payload = json.loads(_marker_path(profile_dir).read_text(encoding="utf-8"))
        return ResumeMarkerState(
            target_index=int(payload["target_index"]),
            base_hash=str(payload["base_hash"]),
            marked_hash=str(payload["marked_hash"]),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _write_marker(profile_dir: Path, marker: ResumeMarkerState) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    _marker_path(profile_dir).write_text(
        json.dumps(asdict(marker), ensure_ascii=False), encoding="utf-8"
    )


def _clear_marker(profile_dir: Path) -> None:
    try:
        _marker_path(profile_dir).unlink()
    except FileNotFoundError:
        pass


def refresh_resume_index(page: Page, *, profile_dir: Path) -> ActivityResult:
    attempted_at = datetime.now(MOSCOW)
    write_resume_refresh_attempt(profile_dir, attempted_at)
    marker = _read_marker(profile_dir)
    try:
        page.goto(PROFILE_URL, wait_until="domcontentloaded")
        dismiss_hh_pro_modal(page)
        edit_buttons = page.locator(EXPERIENCE_EDIT_BUTTON)
        edit_buttons.first.wait_for(state="visible", timeout=15_000)
        button_count = edit_buttons.count()
        if not button_count:
            return ActivityResult(
                action=ActivityKind.REFRESH_RESUME_INDEX,
                status=ActivityStatus.UNKNOWN,
                detail="Кнопки редактирования опыта не распознаны; резюме не изменено.",
            )

        target_index = marker.target_index if marker else random.randrange(button_count)
        if target_index >= button_count:
            _clear_marker(profile_dir)
            return ActivityResult(
                action=ActivityKind.REFRESH_RESUME_INDEX,
                status=ActivityStatus.UNKNOWN,
                detail="Состав опыта изменился; сохранённый маркер сброшен без редактирования.",
            )

        edit_buttons.nth(target_index).click()
        description = page.locator(EXPERIENCE_DESCRIPTION_INPUT).first
        description.wait_for(state="visible", timeout=15_000)
        current_value = description.input_value()
        if not current_value.strip():
            return ActivityResult(
                action=ActivityKind.REFRESH_RESUME_INDEX,
                status=ActivityStatus.SKIPPED,
                detail="Описание опыта пустое; резюме не изменено.",
            )

        if marker:
            restored_value = restore_marked_description(current_value, marker)
            if restored_value is None:
                _clear_marker(profile_dir)
                return ActivityResult(
                    action=ActivityKind.REFRESH_RESUME_INDEX,
                    status=ActivityStatus.UNKNOWN,
                    detail=(
                        "Описание было изменено вне приложения; маркер сброшен без редактирования."
                    ),
                )
            updated_value = restored_value
            operation = "removed"
        else:
            updated_value, marker = build_marked_description(
                current_value, target_index=target_index
            )
            _write_marker(profile_dir, marker)
            operation = "added"

        description.fill(updated_value)
        page.locator(PROFILE_SAVE_BUTTON).click()
        page.wait_for_url(
            re.compile(r"https://hh\.ru/(?:resume/|applicant/profile/).*"), timeout=15_000
        )
        if operation == "removed":
            _clear_marker(profile_dir)
        return ActivityResult(
            action=ActivityKind.REFRESH_RESUME_INDEX,
            status=ActivityStatus.SUCCESS,
            detail=(
                "Контрольная точка добавлена, новая версия резюме сохранена."
                if operation == "added"
                else "Контрольная точка удалена, исходный текст восстановлен и сохранён."
            ),
            metadata={"marker_added": operation == "added", "target_index": target_index},
        )
    except PlaywrightTimeoutError:
        return ActivityResult(
            action=ActivityKind.REFRESH_RESUME_INDEX,
            status=ActivityStatus.UNKNOWN,
            detail="Результат сохранения не подтверждён интерфейсом; автоматического повтора нет.",
        )
    except PlaywrightError as error:
        return ActivityResult(
            action=ActivityKind.REFRESH_RESUME_INDEX,
            status=ActivityStatus.ERROR,
            detail=f"Не удалось обновить версию резюме: {error.__class__.__name__}",
        )
