from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from hh_raiser.models import MOSCOW
from hh_raiser.scheduling import (
    decide_page_state,
    format_wait_duration,
    parse_next_available,
    wait_for_due_time,
)


class SchedulingTests(unittest.TestCase):
    def test_parses_absolute_next_time(self) -> None:
        now = datetime(2026, 8, 27, 0, 40, tzinfo=MOSCOW)
        result = parse_next_available("Поднять вручную можно 27 августа 2026 в 04:34", now)
        self.assertEqual(result, datetime(2026, 8, 27, 4, 34, tzinfo=MOSCOW))

    def test_parses_relative_next_time(self) -> None:
        now = datetime(2026, 8, 27, 0, 40, tzinfo=MOSCOW)
        self.assertEqual(
            parse_next_available("В следующий раз можно будет через 3 часа 54 минуты", now),
            now + timedelta(hours=3, minutes=54),
        )

    def test_available_button_wins_over_stale_time(self) -> None:
        state = decide_page_state(
            button_visible=True,
            page_text="Поднять вручную можно 27 августа 2026 в 00:34",
            now=datetime(2026, 8, 27, 0, 40, tzinfo=MOSCOW),
        )
        self.assertEqual(state.kind, "available")
        self.assertIsNone(state.next_at)

    def test_wait_state_keeps_absolute_time(self) -> None:
        now = datetime(2026, 8, 27, 0, 40, tzinfo=MOSCOW)
        state = decide_page_state(
            button_visible=False, page_text="Поднять вручную можно 27 августа 2026 в 04:34", now=now
        )
        self.assertEqual(state.next_at, datetime(2026, 8, 27, 4, 34, tzinfo=MOSCOW))

    def test_wait_rechecks_wall_clock_after_each_poll(self) -> None:
        started_at = datetime(2026, 9, 1, 9, 0, tzinfo=MOSCOW)
        values = iter(
            [started_at, started_at + timedelta(minutes=10), started_at + timedelta(minutes=20)]
        )
        sleeps: list[float] = []
        wait_for_due_time(
            started_at + timedelta(minutes=20),
            buffer_seconds=0,
            poll_seconds=600,
            now=lambda: next(values),
            sleep=sleeps.append,
        )
        self.assertEqual(sleeps, [600, 600])

    def test_wait_can_stop_while_browser_is_closed(self) -> None:
        started_at = datetime(2026, 9, 1, 9, 0, tzinfo=MOSCOW)
        waits: list[float] = []

        due = wait_for_due_time(
            started_at + timedelta(minutes=20),
            buffer_seconds=0,
            poll_seconds=600,
            now=lambda: started_at,
            wait_for_stop=lambda seconds: waits.append(seconds) or True,
        )

        self.assertFalse(due)
        self.assertEqual(waits, [600])

    def test_formats_wait_duration(self) -> None:
        self.assertEqual(format_wait_duration(3_661), "1 ч. 1 мин. 1 сек.")
