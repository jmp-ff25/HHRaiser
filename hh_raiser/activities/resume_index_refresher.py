from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from hh_raiser.browser import is_closed_playwright_error
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
    base_trailing_periods: int
    base_fingerprint: str


def _marker_path(profile_dir: Path) -> Path:
    return profile_dir / "resume-refresh-marker.json"


def _sequence_path(profile_dir: Path) -> Path:
    return profile_dir / "resume-refresh-sequence.json"


def _normalized_fingerprint(value: str) -> str:
    normalized = " ".join(value.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _split_trailing_whitespace(value: str) -> tuple[str, str]:
    body = value.rstrip()
    return body, value[len(body) :]


def trailing_period_count(value: str) -> int:
    body, _ = _split_trailing_whitespace(value)
    return len(body) - len(body.rstrip("."))


def build_marked_description(value: str, *, target_index: int) -> tuple[str, ResumeMarkerState]:
    body, trailing_whitespace = _split_trailing_whitespace(value)
    marked_value = f"{body}.{trailing_whitespace}"
    return marked_value, ResumeMarkerState(
        target_index=target_index,
        base_trailing_periods=trailing_period_count(value),
        base_fingerprint=_normalized_fingerprint(value),
    )


def restore_marked_description(value: str, marker: ResumeMarkerState) -> str | None:
    body, trailing_whitespace = _split_trailing_whitespace(value)
    current_periods = trailing_period_count(value)
    if marker.base_trailing_periods < 0:
        return f"{body[:-1]}{trailing_whitespace}" if current_periods else None
    if current_periods != marker.base_trailing_periods + 1:
        return None
    restored_value = f"{body[:-1]}{trailing_whitespace}"
    return (
        restored_value
        if _normalized_fingerprint(restored_value) == marker.base_fingerprint
        else None
    )


def remove_one_trailing_period(value: str) -> str | None:
    body, trailing_whitespace = _split_trailing_whitespace(value)
    if not body.endswith("."):
        return None
    return f"{body[:-1]}{trailing_whitespace}"


def description_matches_marker_base(value: str, marker: ResumeMarkerState) -> bool:
    return (
        marker.base_trailing_periods >= 0
        and trailing_period_count(value) == marker.base_trailing_periods
        and _normalized_fingerprint(value) == marker.base_fingerprint
    )


def _read_marker(profile_dir: Path) -> ResumeMarkerState | None:
    try:
        payload = json.loads(_marker_path(profile_dir).read_text(encoding="utf-8"))
        if "base_trailing_periods" not in payload:
            # Marker written by versions that compared the exact browser text.  HH may
            # normalize line endings, so an existing legacy marker still owns one dot.
            return ResumeMarkerState(
                target_index=int(payload["target_index"]),
                base_trailing_periods=-1,
                base_fingerprint="",
            )
        return ResumeMarkerState(
            target_index=int(payload["target_index"]),
            base_trailing_periods=int(payload["base_trailing_periods"]),
            base_fingerprint=str(payload["base_fingerprint"]),
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


def _read_next_target(profile_dir: Path) -> int:
    try:
        payload = json.loads(_sequence_path(profile_dir).read_text(encoding="utf-8"))
        return max(0, int(payload["next_target_index"]))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return 0


def _write_next_target(profile_dir: Path, target_index: int) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    _sequence_path(profile_dir).write_text(
        json.dumps({"next_target_index": target_index}, ensure_ascii=False),
        encoding="utf-8",
    )


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

        target_index = (
            marker.target_index if marker else _read_next_target(profile_dir) % button_count
        )
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

        operation: str
        if marker:
            restored_value = restore_marked_description(current_value, marker)
            if restored_value is None:
                if description_matches_marker_base(current_value, marker):
                    _clear_marker(profile_dir)
                    _write_next_target(profile_dir, (target_index + 1) % button_count)
                    return ActivityResult(
                        action=ActivityKind.REFRESH_RESUME_INDEX,
                        status=ActivityStatus.SUCCESS,
                        detail=(
                            "Контрольная точка уже отсутствует; локальное состояние "
                            "синхронизировано без повторного сохранения."
                        ),
                        metadata={
                            "marker_added": False,
                            "target_index": target_index,
                            "reconciled": True,
                        },
                    )
                return ActivityResult(
                    action=ActivityKind.REFRESH_RESUME_INDEX,
                    status=ActivityStatus.UNKNOWN,
                    detail=(
                        "Не удалось безопасно сопоставить сохранённый маркер; "
                        "он оставлен для следующей проверки."
                    ),
                )
            updated_value = restored_value
            operation = "removed"
        elif trailing_period_count(current_value) > 1:
            # Repair dots left by the old exact-hash implementation before starting
            # a new add/remove pair.  Work through experiences in stable order.
            updated_value = remove_one_trailing_period(current_value)
            if updated_value is None:
                return ActivityResult(
                    action=ActivityKind.REFRESH_RESUME_INDEX,
                    status=ActivityStatus.UNKNOWN,
                    detail="Лишняя контрольная точка распознана, но удалить её не удалось.",
                )
            operation = "repaired"
        else:
            updated_value, marker = build_marked_description(
                current_value, target_index=target_index
            )
            _write_marker(profile_dir, marker)
            operation = "added"

        description.fill(updated_value)
        page.locator(PROFILE_SAVE_BUTTON).click()
        description.wait_for(state="hidden", timeout=15_000)
        edit_buttons = page.locator(EXPERIENCE_EDIT_BUTTON)
        edit_buttons.nth(target_index).click()
        saved_description = page.locator(EXPERIENCE_DESCRIPTION_INPUT).first
        saved_description.wait_for(state="visible", timeout=15_000)
        if _normalized_fingerprint(saved_description.input_value()) != _normalized_fingerprint(
            updated_value
        ):
            return ActivityResult(
                action=ActivityKind.REFRESH_RESUME_INDEX,
                status=ActivityStatus.UNKNOWN,
                detail=(
                    "После сохранения интерфейс показал другое описание; "
                    "локальный маркер сохранён для повторной проверки."
                ),
            )
        page.goto(PROFILE_URL, wait_until="domcontentloaded")
        if operation == "removed":
            _clear_marker(profile_dir)
            _write_next_target(profile_dir, (target_index + 1) % button_count)
        elif operation == "repaired":
            if trailing_period_count(updated_value) <= 1:
                _write_next_target(profile_dir, (target_index + 1) % button_count)
        return ActivityResult(
            action=ActivityKind.REFRESH_RESUME_INDEX,
            status=ActivityStatus.SUCCESS,
            detail=(
                "Контрольная точка добавлена, новая версия резюме сохранена."
                if operation == "added"
                else (
                    "Лишняя точка прежней версии удалена и резюме сохранено."
                    if operation == "repaired"
                    else "Контрольная точка удалена, исходный текст восстановлен и сохранён."
                )
            ),
            metadata={
                "marker_added": operation == "added",
                "target_index": target_index,
                "legacy_repair": operation == "repaired",
            },
        )
    except PlaywrightTimeoutError:
        return ActivityResult(
            action=ActivityKind.REFRESH_RESUME_INDEX,
            status=ActivityStatus.UNKNOWN,
            detail="Результат сохранения не подтверждён интерфейсом; автоматического повтора нет.",
        )
    except PlaywrightError as error:
        if is_closed_playwright_error(error):
            raise
        return ActivityResult(
            action=ActivityKind.REFRESH_RESUME_INDEX,
            status=ActivityStatus.ERROR,
            detail=f"Не удалось обновить версию резюме: {error.__class__.__name__}",
        )
