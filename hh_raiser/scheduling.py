from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from hh_raiser.models import MOSCOW, RUSSIAN_MONTHS, PageState


def parse_next_available(text: str, now: datetime) -> datetime | None:
    normalized = " ".join(text.lower().replace("ё", "е").split())
    month_names = "|".join(RUSSIAN_MONTHS)
    absolute = re.search(
        rf"(\d{{1,2}})\s+({month_names})\s+(\d{{4}}).*?в\s+(\d{{1,2}}):(\d{{2}})",
        normalized,
    )
    if absolute:
        day, month_name, year, hour, minute = absolute.groups()
        return datetime(
            int(year),
            RUSSIAN_MONTHS[month_name],
            int(day),
            int(hour),
            int(minute),
            tzinfo=now.tzinfo or MOSCOW,
        )
    relative_day = re.search(r"\b(сегодня|завтра).*?в\s+(\d{1,2}):(\d{2})", normalized)
    if relative_day:
        day_name, hour, minute = relative_day.groups()
        target_date = now.date() + timedelta(days=day_name == "завтра")
        return datetime.combine(
            target_date,
            datetime.min.time().replace(hour=int(hour), minute=int(minute)),
            tzinfo=now.tzinfo or MOSCOW,
        )
    hours_match = re.search(r"(\d+)\s*(?:час|часа|часов)", normalized)
    minutes_match = re.search(r"(\d+)\s*(?:минута|минуты|минут)", normalized)
    if "через" in normalized and (hours_match or minutes_match):
        return now + timedelta(
            hours=int(hours_match.group(1)) if hours_match else 0,
            minutes=int(minutes_match.group(1)) if minutes_match else 0,
        )
    return None


def decide_page_state(*, button_visible: bool, page_text: str, now: datetime) -> PageState:
    if button_visible:
        return PageState("available")
    next_at = parse_next_available(page_text, now)
    return PageState("waiting", next_at) if next_at else PageState("unknown")


def seconds_until(target: datetime, *, buffer_seconds: int, now: datetime | None = None) -> int:
    current_time = now or datetime.now(MOSCOW)
    return max(0, int((target - current_time).total_seconds()) + buffer_seconds)


def wait_for_due_time(
    target: datetime | None,
    *,
    buffer_seconds: int,
    poll_seconds: int,
    now: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    wait_for_stop: Callable[[float], bool] | None = None,
) -> bool:
    def wait(seconds: float) -> bool:
        if wait_for_stop is not None:
            return wait_for_stop(seconds)
        sleep(seconds)
        return False

    if target is None:
        return not wait(poll_seconds)
    clock = now or (lambda: datetime.now(MOSCOW))
    while True:
        remaining = seconds_until(target, buffer_seconds=buffer_seconds, now=clock())
        if remaining == 0:
            return True
        if wait(min(poll_seconds, remaining)):
            return False


def format_wait_duration(seconds: int) -> str:
    hours, remainder = divmod(max(0, seconds), 3_600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours} ч. {minutes} мин. {seconds} сек."
    if minutes:
        return f"{minutes} мин. {seconds} сек."
    return f"{seconds} сек."
